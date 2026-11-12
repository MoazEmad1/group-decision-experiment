import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.config import load_config, resolve_config_path
from app.db import engine

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
    yield


app = FastAPI(title="Group Decision Experiment", lifespan=lifespan)


@app.get("/api/health")
async def health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
