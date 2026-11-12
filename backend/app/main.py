from fastapi import FastAPI
from sqlalchemy import text

from app.db import engine

app = FastAPI(title="Group Decision Experiment")


@app.get("/api/health")
async def health():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
