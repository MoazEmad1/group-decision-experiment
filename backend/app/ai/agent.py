"""AI participant driver: decides *when* an AI speaks and votes; a Brain decides *what*.

Blind-condition rules enforced here, for every brain:
- never opens a round instantly; replies after a think delay plus typing time that grows
  with message length; nothing is posted so late it would land on the deadline;
- does not reply to every message, has at most one reply in flight, and has a hard cap of
  messages per round (two AIs cannot loop on each other);
- votes at a random moment inside the vote window, always for the candidate it last argued for.

The SessionManager only calls on_phase() / on_message() (synchronously, under the session
lock) and gives the agent `actions` that go through the same post_chat / cast_vote paths as
human participants.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from app.ai.base import Brain, Context, mentioned_labels
from app.ai.rule_based import RuleBasedBrain
from app.config import AIConfig, ExperimentConfig
from app.logic import Phase, RoundTally, is_chat, is_vote, round_of
from app.models import utcnow


class SessionView(Protocol):
    """Read-only view of the live session (LiveSession satisfies it)."""

    phase: Phase
    deadline: datetime | None
    messages: list[dict]
    tallies: dict[int, RoundTally]

    @property
    def labels(self) -> list[str]: ...


class AgentActions(Protocol):
    async def say(self, text: str) -> bool: ...

    async def vote(self, target: str) -> bool: ...


Spawn = Callable[..., asyncio.Task]  # SessionManager._spawn(what, fn, *args)


@dataclass(frozen=True)
class AgentSettings:
    reply_delay: tuple[float, float]  # think time before composing a reply
    typing_seconds_per_char: float
    max_messages_per_round: int
    open_probability: float = 0.85  # chance to start talking in a round unprompted
    reply_probability: float = 0.45  # chance to answer a message
    engaged_reply_probability: float = 0.85  # ...that names me / my candidate, or asks something
    vote_window: tuple[float, float] = (0.15, 0.85)  # fraction of the vote phase
    deadline_margin_seconds: float = 1.0  # don't post this close to the end of a chat phase

    @classmethod
    def from_config(cls, ai: AIConfig, **overrides) -> "AgentSettings":
        settings = cls(
            reply_delay=(ai.reply_delay_min_seconds, ai.reply_delay_max_seconds),
            typing_seconds_per_char=ai.typing_seconds_per_char,
            max_messages_per_round=ai.max_messages_per_round,
        )
        return replace(settings, **overrides)

    @property
    def open_delay(self) -> tuple[float, float]:
        # Opening a round takes about twice as long as a reply: never instant.
        return (2 * self.reply_delay[0], 2 * self.reply_delay[1])


class AIAgent:
    def __init__(
        self,
        *,
        label: str,
        view: SessionView,
        brain: Brain,
        settings: AgentSettings,
        actions: AgentActions,
        spawn: Spawn,
        rng: random.Random,
    ):
        self.label = label
        self._view = view
        self._brain = brain
        self._settings = settings
        self._actions = actions
        self._spawn = spawn
        self._rng = rng
        self.candidate = brain.first_candidate(label, view.labels)
        self.initial_candidate = self.candidate
        # Research/debug record of what this agent actually did.
        self.history: list[tuple[int, str, str]] = []  # (round, text, candidate) per posted message
        self.votes: list[tuple[int, str]] = []
        self._round: int | None = None
        self._sent = 0
        self._chat_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    # --- hooks (called by SessionManager under the session lock; must not await) ---

    def on_phase(self) -> None:
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()  # nothing from the previous phase may leak into this one
        self._tasks.clear()
        self._chat_task = None

        phase, s = self._view.phase, self._settings
        if is_chat(phase):
            self._round, self._sent = round_of(phase), 0
            if s.max_messages_per_round > 0 and self._rng.random() < s.open_probability:
                delay = self._rng.uniform(*s.open_delay)
                self._chat_task = self._start("open", self._speak, self._round, delay, None)
        elif is_vote(phase):
            delay = self._remaining() * self._rng.uniform(*s.vote_window)
            self._start("vote", self._vote, round_of(phase), delay)

    def on_message(self, entry: dict) -> None:
        s = self._settings
        if entry["from"] == self.label or not is_chat(self._view.phase):
            return
        if self._sent >= s.max_messages_per_round:
            return
        if self._chat_task is not None and not self._chat_task.done():
            return  # one message in flight at a time
        probability = s.engaged_reply_probability if self._engaged(entry) else s.reply_probability
        if self._rng.random() >= probability:
            return
        delay = self._rng.uniform(*s.reply_delay)
        self._chat_task = self._start("reply", self._speak, self._round, delay, entry)

    # --- tasks -------------------------------------------------------------------

    async def _speak(self, round: int, delay: float, trigger: dict | None) -> None:
        await asyncio.sleep(delay)
        if not self._still_chatting(round):
            return
        ctx = self._context(round)
        reply = await (self._brain.open_round(ctx) if trigger is None else self._brain.respond(ctx, trigger))
        if reply is None or reply.candidate not in ctx.players:
            return

        s = self._settings
        typing = len(reply.text) * s.typing_seconds_per_char * self._rng.uniform(0.7, 1.3)
        if typing + s.deadline_margin_seconds > self._remaining():
            return  # would arrive at or after the deadline
        await asyncio.sleep(typing)
        if not self._still_chatting(round) or self._sent >= s.max_messages_per_round:
            return
        if await self._actions.say(reply.text):
            self._sent += 1
            self.candidate = reply.candidate  # only what was actually said counts
            self.history.append((round, reply.text, reply.candidate))

    async def _vote(self, round: int, delay: float) -> None:
        await asyncio.sleep(delay)
        phase = self._view.phase
        if is_vote(phase) and round_of(phase) == round:
            target = self.candidate  # consistent with what it argued for in the chat
            if await self._actions.vote(target):
                self.votes.append((round, target))

    # --- helpers -------------------------------------------------------------------

    def _start(self, what: str, fn: Callable[..., Awaitable], *args) -> asyncio.Task:
        task = self._spawn(f"ai {self.label} {what}", fn, *args)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _still_chatting(self, round: int) -> bool:
        phase = self._view.phase
        return is_chat(phase) and round_of(phase) == round

    def _remaining(self) -> float:
        deadline = self._view.deadline
        return max(0.0, (deadline - utcnow()).total_seconds()) if deadline else 0.0

    def _engaged(self, entry: dict) -> bool:
        mentions = mentioned_labels(entry["text"], self._view.labels)
        return "?" in entry["text"] or self.label in mentions or self.candidate in mentions

    def _context(self, round: int) -> Context:
        round1 = self._view.tallies.get(1)
        return Context(
            me=self.label,
            players=self._view.labels,
            round=round,
            candidate=self.candidate,
            messages=list(self._view.messages),
            round1_counts=dict(round1.counts) if round1 else None,
        )


# --- factory: the only place that knows which brain to use (step 9 adds "llm") -----


AgentFactory = Callable[..., AIAgent]


def make_brain(config: ExperimentConfig, rng: random.Random) -> Brain:
    return RuleBasedBrain(rng)


def make_agent_factory(config: ExperimentConfig, **setting_overrides) -> AgentFactory:
    settings = AgentSettings.from_config(config.ai, **setting_overrides)

    def factory(*, label: str, seed: int, view: SessionView, actions: AgentActions, spawn: Spawn) -> AIAgent:
        # Seeded from the session seed + label: an agent's choices are reproducible given the same chat.
        rng = random.Random(f"{seed}:{label}")
        return AIAgent(
            label=label,
            view=view,
            brain=make_brain(config, rng),
            settings=settings,
            actions=actions,
            spawn=spawn,
            rng=rng,
        )

    return factory
