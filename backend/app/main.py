import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import load_config, resolve_config_path
from app.db import SessionLocal, abort_unfinished_sessions, engine, get_db, init_db
from app.lobby import Lobby

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast: an invalid config stops the backend at startup, not mid-session.
    config = load_config()
    app.state.config = config
    # Logged server-side only. Never exposed via an endpoint (blind condition).
    log.info(
        "Loaded config %s: condition=%s humans=%d ais=%d ai_mode=%s",
        resolve_config_path(),
        config.condition,
        config.human_count,
        config.ai_count,
        config.ai.mode,
    )

    await init_db(engine)
    async with SessionLocal() as db:
        aborted = await abort_unfinished_sessions(db)
    if aborted:
        log.info("Marked %d unfinished session(s) from a previous run as ABORTED", aborted)

    app.state.lobby = Lobby(config)
    yield
    await engine.dispose()


app = FastAPI(title="Group Decision Experiment", lifespan=lifespan)


class JoinResponse(BaseModel):
    # Deliberately minimal: no session id, no participant kind, no slot counts (blind condition).
    token: str
    label: str


@app.get("/api/health")
async def health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}


@app.post("/api/join", response_model=JoinResponse)
async def join(request: Request, db: AsyncSession = Depends(get_db)):
    result = await request.app.state.lobby.join(db)
    return JoinResponse(token=result.token, label=result.label)
