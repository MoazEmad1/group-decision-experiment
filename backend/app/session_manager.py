"""Live session state (in memory, single source of truth) and WebSocket handling.

Every state change for a session happens under that session's asyncio.Lock and is written
to the DB before it is broadcast, so the research record and what clients saw agree.
Phase timers, voting and AI agents are added in later steps.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import models, ws_schema
from app.config import ExperimentConfig
from app.lobby import JoinResult
from app.logic import Phase, is_chat, is_vote, phase_duration, round_of
from app.models import utcnow

log = logging.getLogger("uvicorn.error")

# Custom close codes (4000-4999 are application-defined).
CLOSE_INVALID_TOKEN = 4401
CLOSE_REPLACED = 4409  # same participant connected again (new tab / reconnect)


@dataclass
class LiveParticipant:
    id: uuid.UUID
    label: str
    kind: str  # server-side only; never serialised to clients
    token: str | None


@dataclass
class LiveSession:
    id: uuid.UUID
    seed: int
    phase: Phase
    deadline: datetime | None = None
    participants: dict[uuid.UUID, LiveParticipant] = field(default_factory=dict)
    messages: list[dict] = field(default_factory=list)  # ws_schema.chat_entry dicts, in order
    connections: dict[uuid.UUID, WebSocket] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def labels(self) -> list[str]:
        return sorted(p.label for p in self.participants.values())


class SessionManager:
    def __init__(self, config: ExperimentConfig, sessionmaker: async_sessionmaker[AsyncSession]):
        self.config = config
        self._db = sessionmaker
        self._sessions: dict[uuid.UUID, LiveSession] = {}
        self._by_token: dict[str, tuple[LiveSession, LiveParticipant]] = {}
        self._load_lock = asyncio.Lock()

    # --- lifecycle -------------------------------------------------------------

    async def on_join(self, result: JoinResult) -> None:
        session = await self._load(result.session_id)
        if result.session_full:
            await self.start(session)

    async def _load(self, session_id: uuid.UUID) -> LiveSession:
        """Create or refresh the in-memory session from the DB (picks up newly joined tokens).
        Restarts abort unfinished sessions, so every live session was created by this process."""
        async with self._load_lock:
            async with self._db() as db:
                row = await db.get(models.ExperimentSession, session_id)
                rows = (
                    await db.scalars(select(models.Participant).where(models.Participant.session_id == session_id))
                ).all()

            session = self._sessions.get(session_id)
            if session is None:
                session = LiveSession(id=row.id, seed=row.seed, phase=Phase(row.phase))
                self._sessions[session_id] = session
            for p in rows:
                live = session.participants.get(p.id)
                if live is None:
                    live = LiveParticipant(id=p.id, label=p.label, kind=p.kind, token=p.token)
                    session.participants[p.id] = live
                live.token = p.token
                if p.token:
                    self._by_token[p.token] = (session, live)
            return session

    async def start(self, session: LiveSession) -> None:
        async with session.lock:
            if session.phase != Phase.WAITING:
                return  # idempotent
            await self._enter_phase(session, Phase.ROUND_1_CHAT)

    async def _enter_phase(self, session: LiveSession, phase: Phase) -> None:
        """Caller holds session.lock. Persist, then broadcast. (Step 6 adds the timer here.)"""
        now = utcnow()
        duration = phase_duration(phase, self.config.timing)
        deadline = now + timedelta(seconds=duration) if duration is not None else None

        async with self._db() as db, db.begin():
            row = await db.get(models.ExperimentSession, session.id)
            row.phase = phase.value
            if phase == Phase.ROUND_1_CHAT:
                row.started_at = now
            db.add(models.PhaseEvent(session_id=session.id, phase=phase.value, started_at=now, deadline=deadline))

        session.phase, session.deadline = phase, deadline
        log.info("Session %s -> %s", session.id, phase.value)
        await self._broadcast(session, ws_schema.phase_changed(phase.value, round_of(phase), deadline, now))

    # --- chat ------------------------------------------------------------------

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

    # --- websocket -------------------------------------------------------------

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
            await ws.send_json(self._snapshot(session, me))

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
            if code:
                await ws.send_json(ws_schema.error(code, "chat is not open right now"))
        elif isinstance(msg, ws_schema.CastVote):
            if not is_vote(session.phase):
                await ws.send_json(ws_schema.error("voting_closed", "voting is not open right now"))
            # Recording votes is added in step 6.

    def _snapshot(self, session: LiveSession, me: LiveParticipant) -> dict:
        return ws_schema.state_snapshot(
            you=me.label,
            players=session.labels,
            phase=session.phase.value,
            round=round_of(session.phase),
            deadline=session.deadline,
            server_time=utcnow(),
            messages=list(session.messages),
            my_vote=None,  # step 6
            tallies={},  # step 6
        )

    async def _broadcast(self, session: LiveSession, frame: dict) -> None:
        for participant_id, ws in list(session.connections.items()):
            try:
                await ws.send_json(frame)
            except Exception:  # client vanished mid-send; its handler cleans up
                if session.connections.get(participant_id) is ws:
                    del session.connections[participant_id]

    @staticmethod
    async def _safe_close(ws: WebSocket, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:
            pass
