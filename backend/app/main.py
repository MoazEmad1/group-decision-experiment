import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import ExperimentConfig, load_config, resolve_config_path
from app.db import DATABASE_URL, abort_unfinished_sessions, get_db, init_db
from app.lobby import Lobby
from app.session_manager import SessionManager

log = logging.getLogger("uvicorn.error")
router = APIRouter()


class JoinResponse(BaseModel):
    # Deliberately minimal: no session id, no participant kind, no slot counts (blind condition).
    token: str
    label: str


@router.get("/api/health")
async def health(request: Request):
    async with request.app.state.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}


@router.post("/api/join", response_model=JoinResponse)
async def join(request: Request, db: AsyncSession = Depends(get_db)):
    result = await request.app.state.lobby.join(db)
    await request.app.state.manager.on_join(result)
    return JoinResponse(token=result.token, label=result.label)


@router.websocket("/ws")
async def websocket(ws: WebSocket, token: str = ""):
    # Token as query param (browsers can't set WS headers). Session-scoped, so acceptable here.
    await ws.app.state.manager.handle_connection(ws, token)


def create_app(config: ExperimentConfig | None = None, engine: AsyncEngine | None = None) -> FastAPI:
    """Production: no arguments (config from CONFIG_PATH, Postgres from DATABASE_URL).
    Tests inject a config and a SQLite engine."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Fail fast: an invalid config stops the backend at startup, not mid-session.
        cfg = config or load_config()
        # Logged server-side only. Never exposed via an endpoint (blind condition).
        log.info(
            "Loaded config %s: condition=%s humans=%d ais=%d ai_mode=%s",
            resolve_config_path() if config is None else "<injected>",
            cfg.condition,
            cfg.human_count,
            cfg.ai_count,
            cfg.ai.mode,
        )

        eng = engine or create_async_engine(DATABASE_URL)
        sessionmaker = async_sessionmaker(eng, expire_on_commit=False)
        await init_db(eng)
        async with sessionmaker() as db:
            aborted = await abort_unfinished_sessions(db)
        if aborted:
            log.info("Marked %d unfinished session(s) from a previous run as ABORTED", aborted)

        app.state.config = cfg
        app.state.engine = eng
        app.state.sessionmaker = sessionmaker
        app.state.lobby = Lobby(cfg)
        app.state.manager = SessionManager(cfg, sessionmaker)
        yield
        await eng.dispose()

    app = FastAPI(title="Group Decision Experiment", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
