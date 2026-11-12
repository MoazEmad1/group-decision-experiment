"""Live session state (in memory, single source of truth), phase timers and WebSocket handling.

Every state change for a session happens under that session's asyncio.Lock and is written
to the DB before it is broadcast, so the research record and what clients saw agree.

Timers: entering a phase bumps session.generation and cancels the previous timer. A timer
only advances the session if its generation is still current, so a timer that fires after
the phase already moved on (e.g. voting ended early) does nothing.
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import logic, models, ws_schema
from app.config import ExperimentConfig
from app.lobby import JoinResult
from app.logic import Phase, RoundTally, decide, everyone_voted, is_chat, is_vote, latest_votes, next_phase
from app.logic import phase_duration, round_of, tally_round
from app.models import utcnow

log = logging.getLogger("uvicorn.error")

# Custom close codes (4000-4999 are application-defined).
CLOSE_INVALID_TOKEN = 4401
CLOSE_REPLACED = 4409  # same participant connected again (new tab / reconnect)

ERROR_MESSAGES = {
    "chat_closed": "chat is not open right now",
    "voting_closed": "voting is not open right now",
    "invalid_target": "you can only vote for one of the players",
}


@dataclass
class LiveParticipant:
    id: uuid.UUID
    label: str
    kind: str  # server-side only; never serialised to clients
    token: str | None
    joined: bool = False  # humans: claimed via /join; AIs: join delay elapsed


@dataclass
class LiveSession:
    id: uuid.UUID
    seed: int
    phase: Phase
    deadline: datetime | None = None
    participants: dict[uuid.UUID, LiveParticipant] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)  # ws_schema.chat_entry dicts, in order
    votes: list[logic.Vote] = field(default_factory=list)  # append-only, by label, in insert order
    tallies: dict[int, RoundTally] = field(default_factory=dict)  # completed rounds
    results: dict | None = None  # ws_schema.results_payload once RESULTS
    connections: dict[uuid.UUID, WebSocket] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    generation: int = 0
    timer: asyncio.Task | None = None

    @property
    def labels(self) -> list[str]:
        return sorted(p.label for p in self.participants.values())

    def by_label(self, label: str) -> LiveParticipant | None:
        return next((p for p in self.participants.values() if p.label == label), None)


class SessionManager:
    def __init__(self, config: ExperimentConfig, sessionmaker: async_sessionmaker[AsyncSession]):
        self.config = config
        self._db = sessionmaker
        self._sessions: dict[uuid.UUID, LiveSession] = {}
        self._by_token: dict[str, tuple[LiveSession, LiveParticipant]] = {}
        self._load_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._rng = random.Random()

    def get(self, session_id: uuid.UUID) -> LiveSession | None:
        return self._sessions.get(session_id)

    async def close(self) -> None:
        """Cancel timers and pending AI joins (app shutdown, tests)."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn(self, what: str, fn: Callable[..., Awaitable], *args) -> asyncio.Task:
        # The coroutine is created inside the task, so cancelling a task that hasn't started yet
        # (e.g. a timer replaced right away) leaves no never-awaited coroutine behind.
        async def guarded():
            try:
                await fn(*args)
            except asyncio.CancelledError:
                raise
            except Exception:  # a background failure must be visible, not silently swallowed
                log.exception("Background task failed: %s", what)

        task = asyncio.create_task(guarded(), name=what)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- waiting room ------------------------------------------------------------

    async def on_join(self, result: JoinResult) -> None:
        session, created = await self._load(result.session_id)
        if created:
            await self._schedule_ai_joins(session)
        async with session.lock:
            await self._maybe_start(session)

    async def _load(self, session_id: uuid.UUID) -> tuple[LiveSession, bool]:
        """Create or refresh the in-memory session from the DB (picks up newly joined tokens).
        Restarts abort unfinished sessions, so every live session was created by this process."""
        async with self._load_lock:
            async with self._db() as db:
                row = await db.get(models.ExperimentSession, session_id)
                rows = (
                    await db.scalars(select(models.Participant).where(models.Participant.session_id == session_id))
                ).all()

            session = self._sessions.get(session_id)
            created = session is None
            if created:
                session = LiveSession(id=row.id, seed=row.seed, phase=Phase(row.phase))
                self._sessions[session_id] = session
            for p in rows:
                live = session.participants.get(p.id)
                if live is None:
                    live = LiveParticipant(id=p.id, label=p.label, kind=p.kind, token=p.token)
                    session.participants[p.id] = live
                live.token = p.token
                # Monotonic: never un-join (the DB read may predate an AI join that just happened).
                live.joined = live.joined or p.joined_at is not None
                if p.token:
                    self._by_token[p.token] = (session, live)
            return session, created

    async def _schedule_ai_joins(self, session: LiveSession) -> None:
        ai = self.config.ai
        for participant in session.participants.values():
            if participant.kind != "ai":
                continue
            delay = self._rng.uniform(ai.join_delay_min_seconds, ai.join_delay_max_seconds)
            if delay <= 0:
                await self._ai_join(session, participant)
            else:
                self._spawn(f"ai-join {participant.id}", self._ai_join_after, session, participant, delay)

    async def _ai_join_after(self, session: LiveSession, participant: LiveParticipant, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._ai_join(session, participant)

    async def _ai_join(self, session: LiveSession, participant: LiveParticipant) -> None:
        async with session.lock:
            if session.phase != Phase.WAITING or participant.joined:
                return
            async with self._db() as db, db.begin():
                row = await db.get(models.Participant, participant.id)
                row.joined_at = utcnow()
            participant.joined = True
            await self._maybe_start(session)

    async def _maybe_start(self, session: LiveSession) -> None:
        """Caller holds session.lock. Start once every human and every AI has joined."""
        if session.phase == Phase.WAITING and all(p.joined for p in session.participants.values()):
            await self._enter_phase(session, Phase.ROUND_1_CHAT)

    # --- phases ------------------------------------------------------------------

    async def on_timer(self, session: LiveSession, generation: int) -> None:
        async with session.lock:
            if generation != session.generation:
                return  # stale: the phase already advanced (e.g. voting ended early)
            await self._advance(session)

    async def _timer(self, session: LiveSession, generation: int, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.on_timer(session, generation)

    async def _advance(self, session: LiveSession) -> None:
        """Caller holds session.lock."""
        await self._enter_phase(session, next_phase(session.phase))

    async def _enter_phase(self, session: LiveSession, phase: Phase) -> None:
        """Caller holds session.lock. Close the current round if leaving a vote phase,
        persist, broadcast, then arm the timer for the new phase."""
        now = utcnow()
        session.generation += 1
        if session.timer is not None and session.timer is not asyncio.current_task():
            session.timer.cancel()
        session.timer = None

        duration = phase_duration(phase, self.config.timing)
        deadline = now + timedelta(seconds=duration) if duration is not None else None

        closing_round = round_of(session.phase) if is_vote(session.phase) else None
        if closing_round is not None:
            session.tallies[closing_round] = tally_round(session.votes, closing_round, session.labels)
        decision = decide(session.votes, session.labels, session.seed) if phase == Phase.RESULTS else None

        async with self._db() as db, db.begin():
            row = await db.get(models.ExperimentSession, session.id)
            row.phase = phase.value
            if phase == Phase.ROUND_1_CHAT:
                row.started_at = now
            if decision is not None:
                row.decision_maker_id = session.by_label(decision.winner).id
                row.tiebreak_method = decision.method.value
                row.ended_at = now
            db.add(models.PhaseEvent(session_id=session.id, phase=phase.value, started_at=now, deadline=deadline))

        session.phase, session.deadline = phase, deadline
        if decision is not None:
            session.results = ws_schema.results_payload(decision.winner, decision.method.value, session.tallies)
        log.info("Session %s -> %s", session.id, phase.value)

        # Round-1 tally goes out first, so clients already have it when round 2 renders.
        if closing_round == 1:
            await self._broadcast(session, ws_schema.vote_tally(session.tallies[1]))
        await self._broadcast(session, ws_schema.phase_changed(phase.value, round_of(phase), deadline, now))
        if session.results is not None and phase == Phase.RESULTS:
            await self._broadcast(session, ws_schema.results(session.results))

        if duration is not None:
            session.timer = self._spawn(
                f"timer {session.id} {phase.value}", self._timer, session, session.generation, duration
            )

    # --- chat and votes ----------------------------------------------------------

    async def post_chat(self, session: LiveSession, sender: LiveParticipant, text: str) -> str | None:
        """Shared path for human and AI messages (identical shape and timing path: blind).
        Returns an error code, or None on success."""
        async with session.lock:
            if not is_chat(session.phase):
                return "chat_closed"
            rnd = round_of(session.phase)
            now = utcnow()
            async with self._db() as db, db.begin():
                row = models.Message(
                    session_id=session.id, participant_id=sender.id, round=rnd, text=text, created_at=now
                )
                db.add(row)
                await db.flush()
                message_id = row.id

            entry = ws_schema.chat_entry(message_id, sender.label, rnd, text, now)
            session.messages.append(entry)
            await self._broadcast(session, ws_schema.chat_message(entry))
            return None

    async def cast_vote(self, session: LiveSession, voter: LiveParticipant, target_label: str) -> str | None:
        """Shared path for human and AI votes. Append-only: a changed vote is a new row.
        Ends the vote phase early once every participant has voted. Returns an error code or None."""
        async with session.lock:
            if not is_vote(session.phase):
                return "voting_closed"
            target = session.by_label(target_label)
            if target is None:
                return "invalid_target"
            rnd = round_of(session.phase)
            now = utcnow()
            async with self._db() as db, db.begin():
                db.add(
                    models.Vote(
                        session_id=session.id, round=rnd, voter_id=voter.id, target_id=target.id, created_at=now
                    )
                )
            session.votes.append(logic.Vote(round=rnd, voter=voter.label, target=target.label, cast_at=now))
            await self._send(session, voter, ws_schema.vote_recorded(rnd, target.label))

            if everyone_voted(session.votes, rnd, session.labels):
                await self._advance(session)
            return None

    # --- websocket ---------------------------------------------------------------

    async def handle_connection(self, ws: WebSocket, token: str) -> None:
        await ws.accept()  # accept first so the client receives our close code
        found = self._by_token.get(token)
        if found is None:
            await ws.close(code=CLOSE_INVALID_TOKEN, reason="invalid token")
            return
        session, me = found

        async with session.lock:
            # Register and snapshot under the lock: no broadcast can slip between them.
            previous = session.connections.get(me.id)
            session.connections[me.id] = ws
            if previous is not None:
                await self._safe_close(previous, CLOSE_REPLACED, "connected elsewhere")
            await ws.send_json(self.snapshot(session, me))

        try:
            while True:
                raw = await ws.receive_text()
                await self._handle_client_message(session, me, ws, raw)
        except WebSocketDisconnect:
            pass
        finally:
            if session.connections.get(me.id) is ws:
                del session.connections[me.id]

    async def _handle_client_message(self, session: LiveSession, me: LiveParticipant, ws: WebSocket, raw: str):
        try:
            msg = ws_schema.parse_client_message(raw)
        except ValidationError as e:
            first = e.errors()[0]
            await ws.send_json(ws_schema.error("invalid_message", first.get("msg", "invalid message")))
            return

        if isinstance(msg, ws_schema.SendMessage):
            code = await self.post_chat(session, me, msg.payload.text)
        else:
            code = await self.cast_vote(session, me, msg.payload.target)
        if code:
            await ws.send_json(ws_schema.error(code, ERROR_MESSAGES[code]))

    def snapshot(self, session: LiveSession, me: LiveParticipant) -> dict:
        rnd = round_of(session.phase)
        my_vote = latest_votes(session.votes, rnd, session.labels).get(me.label) if is_vote(session.phase) else None
        return ws_schema.state_snapshot(
            you=me.label,
            players=session.labels,
            phase=session.phase.value,
            round=rnd,
            deadline=session.deadline,
            server_time=utcnow(),
            messages=list(session.messages),
            my_vote=my_vote,
            tallies=session.tallies,
            results=session.results,
        )

    async def _send(self, session: LiveSession, participant: LiveParticipant, frame: dict) -> None:
        ws = session.connections.get(participant.id)  # AIs have no connection
        if ws is None:
            return
        try:
            await ws.send_json(frame)
        except Exception:
            if session.connections.get(participant.id) is ws:
                del session.connections[participant.id]

    async def _broadcast(self, session: LiveSession, frame: dict) -> None:
        for participant in list(session.participants.values()):
            await self._send(session, participant, frame)

    @staticmethod
    async def _safe_close(ws: WebSocket, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass
