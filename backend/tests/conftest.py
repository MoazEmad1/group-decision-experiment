import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import init_db
from app.lobby import Lobby
from app.session_manager import LiveSession, SessionManager


@pytest.fixture
async def env(tmp_path):
    """Lobby + SessionManager on a temp SQLite DB, driven directly (no HTTP/WebSocket)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'env.db'}")
    await init_db(engine)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    managers: list[SessionManager] = []

    class Env:
        db = sessionmaker

        def setup(self, config, agent_factory=None):
            self.lobby = Lobby(config)
            self.manager = SessionManager(config, sessionmaker, agent_factory=agent_factory)
            managers.append(self.manager)
            return self

        async def join(self) -> LiveSession:
            async with sessionmaker() as db:
                result = await self.lobby.join(db)
            await self.manager.on_join(result)
            return self.manager.get(result.session_id)

        async def rows(self, model, session: LiveSession, order_by=None):
            async with sessionmaker() as db:
                query = select(model).where(model.session_id == session.id)
                if order_by is not None:
                    query = query.order_by(order_by)
                return (await db.scalars(query)).all()

    yield Env()
    for manager in managers:
        await manager.close()
    await engine.dispose()
