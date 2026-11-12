import os
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.logic import Phase
from app.models import Base, ExperimentSession, PhaseEvent, utcnow

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://experiment:experiment@localhost:5432/experiment",
)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as db:
        yield db


async def init_db(bind: AsyncEngine) -> None:
    # create_all instead of migrations: fine for a prototype, see README.
    async with bind.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def abort_unfinished_sessions(db: AsyncSession) -> int:
    """Live session state is in memory, so sessions interrupted by a restart cannot resume.
    Mark them ABORTED (keeps their data, and /join never hands out a slot in a dead session)."""
    now = utcnow()
    terminal = (Phase.RESULTS.value, Phase.ABORTED.value)
    async with db.begin():
        stale = (await db.scalars(select(ExperimentSession).where(ExperimentSession.phase.not_in(terminal)))).all()
        for session in stale:
            session.phase = Phase.ABORTED.value
            session.ended_at = now
            db.add(PhaseEvent(session_id=session.id, phase=Phase.ABORTED.value, started_at=now))
    return len(stale)
