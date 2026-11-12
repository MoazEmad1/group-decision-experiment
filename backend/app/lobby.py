"""Waiting-room slot assignment for POST /api/join.

All participant rows (humans and AI) are created with the session, labels already shuffled.
A join claims the next unclaimed human row. One asyncio.Lock serialises joins, so two
simultaneous requests never get the same slot and a 4th human always lands in a new session.
That is sufficient because the backend runs a single worker (see Dockerfile).
"""

import asyncio
import random
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ExperimentConfig
from app.logic import Phase
from app.models import ExperimentSession, Participant, PhaseEvent, utcnow

LABELS = ("A", "B", "C")
SEED_RANGE = 2**31


@dataclass(frozen=True)
class JoinResult:
    token: str
    label: str
    session_id: uuid.UUID
    session_full: bool  # every human slot filled: the session can start (wired up in step 6)


class Lobby:
    def __init__(self, config: ExperimentConfig):
        self.config = config
        self._lock = asyncio.Lock()
        self._rng = random.SystemRandom()

    async def join(self, db: AsyncSession) -> JoinResult:
        async with self._lock, db.begin():
            slot = await self._next_free_human_slot(db)
            if slot is None:
                await self._create_session(db)
                slot = await self._next_free_human_slot(db)

            slot.token = secrets.token_urlsafe(32)
            slot.joined_at = utcnow()
            await db.flush()

            free_left = await db.scalar(
                select(func.count())
                .select_from(Participant)
                .where(
                    Participant.session_id == slot.session_id,
                    Participant.kind == "human",
                    Participant.token.is_(None),
                )
            )
            return JoinResult(
                token=slot.token,
                label=slot.label,
                session_id=slot.session_id,
                session_full=free_left == 0,
            )

    async def _next_free_human_slot(self, db: AsyncSession) -> Participant | None:
        return await db.scalar(
            select(Participant)
            .join(ExperimentSession, Participant.session_id == ExperimentSession.id)
            .where(
                ExperimentSession.phase == Phase.WAITING.value,
                Participant.kind == "human",
                Participant.token.is_(None),
            )
            .order_by(ExperimentSession.created_at, Participant.slot_index)
            .limit(1)
        )

    async def _create_session(self, db: AsyncSession) -> ExperimentSession:
        now = utcnow()
        labels = list(LABELS)
        # Labels are shuffled across slots, so neither label nor join order reveals who is AI.
        self._rng.shuffle(labels)

        session = ExperimentSession(
            id=uuid.uuid4(),
            condition=self.config.condition,
            config_snapshot=self.config.snapshot(),
            seed=secrets.randbelow(SEED_RANGE),
            phase=Phase.WAITING.value,
            created_at=now,
        )
        db.add(session)
        for slot_index, (participant, label) in enumerate(zip(self.config.participants, labels)):
            is_ai = participant.type == "ai"
            db.add(
                Participant(
                    session_id=session.id,
                    slot_index=slot_index,
                    kind=participant.type,
                    label=label,
                    joined_at=now if is_ai else None,
                    created_at=now,
                )
            )
        db.add(PhaseEvent(session_id=session.id, phase=Phase.WAITING.value, started_at=now))
        await db.flush()
        return session
