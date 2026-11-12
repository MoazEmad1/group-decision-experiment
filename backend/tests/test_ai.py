"""Rule-based AI agents: reply cap, vote consistency, vote timing, human-like pacing.
Integration tests drive Lobby + SessionManager (conftest `env`) with tiny durations;
unit tests hit the rule-based brain directly."""

import asyncio
import random
import time
from datetime import timedelta

import pytest

from app import models
from app.ai.agent import make_agent_factory
from app.ai.base import Context, mentioned_labels
from app.ai.rule_based import RuleBasedBrain
from app.logic import Phase
from tests.helpers import NO_AI_JOIN_DELAY, FakeWS, preset, wait_for

LONG = 30

# AIs that always talk and always answer, with tiny delays: the worst case for looping.
CHATTY = dict(open_probability=1.0, reply_probability=1.0, engaged_reply_probability=1.0,
              deadline_margin_seconds=0.02)


def fast_ai(**ai) -> dict:
    return {**NO_AI_JOIN_DELAY, "reply_delay_min_seconds": 0.01, "reply_delay_max_seconds": 0.03,
            "typing_seconds_per_char": 0, **ai}


async def start_1_human_2_ai(env, timing: dict, ai: dict, **settings):
    config = preset("1_human_2_ai", timing=timing, ai=fast_ai(**ai))
    env.setup(config, agent_factory=make_agent_factory(config, **settings))
    session = await env.join()
    human = next(p for p in session.participants.values() if p.kind == "human")
    ws = session.connections[human.id] = FakeWS()
    agents = {session.participants[pid].label: agent for pid, agent in session.agents.items()}
    return session, human, ws, agents


async def phase_events(env, session) -> dict[str, models.PhaseEvent]:
    return {e.phase: e for e in await env.rows(models.PhaseEvent, session)}


# --- reply cap -------------------------------------------------------------------


async def test_reply_cap_stops_two_ais_looping_even_when_spammed(env):
    session, human, ws, agents = await start_1_human_2_ai(
        env, timing={"chat_seconds": 0.8, "vote_seconds": LONG}, ai={"max_messages_per_round": 2}, **CHATTY
    )
    assert session.phase == Phase.ROUND_1_CHAT
    ai_labels = sorted(agents)
    for i in range(8):  # the human keeps poking both AIs
        await env.manager.post_chat(session, human, f"what do you think {ai_labels[i % 2]}?")
        await asyncio.sleep(0.03)
    await wait_for(lambda: session.phase == Phase.ROUND_1_VOTE)

    rows = await env.rows(models.Message, session)
    labels = {p.id: p.label for p in session.participants.values()}
    per_ai = {label: [r for r in rows if labels[r.participant_id] == label] for label in ai_labels}
    for label, messages in per_ai.items():
        assert 1 <= len(messages) <= 2, (label, [m.text for m in messages])
        assert len(agents[label].history) == len(messages)

    # Same path as humans: identical frame shape, stored like any message, before the deadline.
    chat_deadline = (await phase_events(env, session))["ROUND_1_CHAT"].deadline
    ai_frames = [f for f in ws.of_type("chat_message") if f["from"] in ai_labels]
    assert len(ai_frames) == sum(len(m) for m in per_ai.values())
    assert all(set(f) == {"id", "from", "round", "text", "ts"} for f in ai_frames)
    assert all(m.created_at <= chat_deadline for ms in per_ai.values() for m in ms)


async def test_two_ais_alone_stop_at_cap(env):
    session, _, _, agents = await start_1_human_2_ai(
        env, timing={"chat_seconds": 0.6, "vote_seconds": LONG}, ai={"max_messages_per_round": 3}, **CHATTY
    )
    await wait_for(lambda: session.phase == Phase.ROUND_1_VOTE)
    rows = await env.rows(models.Message, session)
    assert 2 <= len(rows) <= 6  # both open, then they answer each other until the cap
    assert all(len(agent.history) <= 3 for agent in agents.values())


async def test_cap_zero_means_silent_but_still_votes(env):
    session, _, _, agents = await start_1_human_2_ai(
        env, timing={"chat_seconds": 0.1, "vote_seconds": 0.3}, ai={"max_messages_per_round": 0}, **CHATTY
    )
    await wait_for(lambda: session.phase == Phase.RESULTS)
    assert await env.rows(models.Message, session) == []
    for agent in agents.values():
        assert agent.votes == [(1, agent.initial_candidate), (2, agent.initial_candidate)]


# --- vote consistency ----------------------------------------------------------------


@pytest.mark.parametrize("run", range(6))
async def test_ai_votes_for_the_candidate_it_last_argued_for(env, run):
    session, human, _, agents = await start_1_human_2_ai(
        env, timing={"chat_seconds": 0.5, "vote_seconds": 0.3}, ai={"max_messages_per_round": 3}, **CHATTY
    )
    ai_labels = sorted(agents)
    # Prompts that make the rule-based brain switch candidates now and then.
    prompts = ["hi all", f"I think {ai_labels[0]} should do it", "I can do it", f"player {ai_labels[1]}?"]
    for text in prompts:
        await env.manager.post_chat(session, human, text)
        await asyncio.sleep(0.05)
    await wait_for(lambda: session.phase == Phase.RESULTS, timeout=5)

    votes = await env.rows(models.Vote, session, order_by=models.Vote.id)
    labels = {p.id: p.label for p in session.participants.values()}
    for label, agent in agents.items():
        assert [r for r, _ in agent.votes] == [1, 2], "each AI votes once per round"
        stored = [(v.round, labels[v.target_id]) for v in votes if labels[v.voter_id] == label]
        assert stored == agent.votes  # went through cast_vote and was persisted

        for vote_round, target in agent.votes:
            stated = [cand for r, _, cand in agent.history if r <= vote_round]
            assert target == (stated[-1] if stated else agent.initial_candidate)


# --- timing ----------------------------------------------------------------------------


async def test_ai_votes_at_random_time_inside_vote_window(env):
    window = 1.0
    session, _, _, agents = await start_1_human_2_ai(
        env, timing={"chat_seconds": 0.05, "vote_seconds": window}, ai={"max_messages_per_round": 0}
    )
    await wait_for(lambda: session.phase == Phase.RESULTS, timeout=5)

    events = await phase_events(env, session)
    votes = await env.rows(models.Vote, session)
    assert len(votes) == 4  # 2 AIs x 2 rounds; the human abstains, so phases run to the deadline
    tolerance = timedelta(seconds=0.1)
    for round_ in (1, 2):
        phase = events[f"ROUND_{round_}_VOTE"]
        times = [v.created_at for v in votes if v.round == round_]
        for t in times:
            assert phase.started_at + timedelta(seconds=0.15 * window) - tolerance <= t
            assert t <= phase.started_at + timedelta(seconds=0.85 * window) + tolerance
            assert t < phase.deadline
        assert times[0] != times[1]  # independent random moments, not in lockstep


async def test_vote_phase_ends_early_once_human_and_ais_voted(env):
    window = 3.0
    session, human, _, agents = await start_1_human_2_ai(
        env, timing={"chat_seconds": 0.05, "vote_seconds": window}, ai={"max_messages_per_round": 0}
    )
    await wait_for(lambda: session.phase == Phase.ROUND_1_VOTE)
    started = time.monotonic()
    assert await env.manager.cast_vote(session, human, "A") is None
    await wait_for(lambda: session.phase == Phase.ROUND_2_CHAT, timeout=window)
    assert time.monotonic() - started < 0.85 * window + 0.2  # the last AI vote ended it, not the timer


async def test_ai_never_opens_instantly_and_typing_scales_with_length(env):
    per_char = 0.004
    session, _, _, agents = await start_1_human_2_ai(
        env,
        timing={"chat_seconds": 2, "vote_seconds": LONG},
        ai={"reply_delay_min_seconds": 0.1, "reply_delay_max_seconds": 0.15, "typing_seconds_per_char": per_char},
        open_probability=1.0, deadline_margin_seconds=0.02,
    )
    await wait_for(lambda: all(agent.history for agent in agents.values()), timeout=3)

    round_start = (await phase_events(env, session))["ROUND_1_CHAT"].started_at
    rows = await env.rows(models.Message, session, order_by=models.Message.id)
    labels = {p.id: p.label for p in session.participants.values()}
    for label in agents:
        first = next(r for r in rows if labels[r.participant_id] == label)
        min_open = 2 * 0.1  # opening delay is twice the reply delay
        min_typing = 0.7 * len(first.text) * per_char
        assert (first.created_at - round_start).total_seconds() >= min_open + min_typing - 0.01


# --- rule-based brain (unit) -------------------------------------------------------


class FixedRandom(random.Random):
    """random() always returns `value`; choice() stays seeded and deterministic."""

    def __init__(self, value: float):
        super().__init__(0)
        self.value = value

    def random(self) -> float:
        return self.value


def ctx(me="B", candidate="C", round=1, round1_counts=None) -> Context:
    return Context(me=me, players=["A", "B", "C"], round=round, candidate=candidate, messages=[],
                   round1_counts=round1_counts)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I think B should do it", ["B"]),
        ("player c? or Player a", ["C", "A"]),
        ("A good idea would be C", ["C"]),  # leading "A" is the article
        ("I vote A", ["A"]),
        ("a b c lowercase letters", []),
        ("Bob and Carl", []),
    ],
)
def test_mentioned_labels(text, expected):
    assert mentioned_labels(text, ["A", "B", "C"]) == expected


async def test_volunteer_can_win_the_ai_over_or_not():
    message = {"from": "A", "text": "I can do it"}
    switched = await RuleBasedBrain(FixedRandom(0.0)).respond(ctx(), message)
    assert switched.candidate == "A"
    stuck = await RuleBasedBrain(FixedRandom(0.99)).respond(ctx(), message)
    assert stuck.candidate == "C"
    assert "C" in mentioned_labels(stuck.text, ["A", "B", "C"])  # it names whom it argues for


async def test_cannot_is_not_a_volunteer():
    reply = await RuleBasedBrain(FixedRandom(0.0)).respond(ctx(), {"from": "A", "text": "I can't do it sorry"})
    assert reply is None or reply.candidate == "C"


async def test_nominated_ai_accepts_or_declines():
    message = {"from": "A", "text": "B should be the decision maker"}
    assert (await RuleBasedBrain(FixedRandom(0.0)).respond(ctx(), message)).candidate == "B"
    assert (await RuleBasedBrain(FixedRandom(0.99)).respond(ctx(), message)).candidate == "C"


async def test_round_2_bandwagon_to_round_1_leader():
    counts = {"A": 2, "B": 0, "C": 1}
    joined = await RuleBasedBrain(FixedRandom(0.0)).open_round(ctx(round=2, round1_counts=counts))
    assert joined.candidate == "A"
    kept = await RuleBasedBrain(FixedRandom(0.99)).open_round(ctx(round=2, round1_counts=counts))
    assert kept.candidate == "C"


@pytest.mark.parametrize("seed", range(30))
async def test_messages_short_casual_and_never_reveal_type(seed):
    brain = RuleBasedBrain(random.Random(seed))
    texts = []
    for c in (ctx(candidate="C"), ctx(candidate="B"), ctx(round=2, round1_counts={"A": 1, "B": 1, "C": 1})):
        texts.append((await brain.open_round(c)).text)
        for text in ["hey", "who?", "I can do it", "A should", "B!", "ok", "lol"]:
            reply = await brain.respond(c, {"from": "A", "text": text})
            if reply:
                texts.append(reply.text)
    for text in texts:
        assert 0 < len(text) <= 80
        assert not any(word in text.lower().split() for word in ("ai", "bot", "human", "robot", "agent", "model"))
