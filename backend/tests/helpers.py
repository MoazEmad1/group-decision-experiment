import asyncio
import time
from pathlib import Path

from app.config import ExperimentConfig, load_config

_BACKEND = Path(__file__).resolve().parent.parent
# configs/ is bind-mounted at /app/configs in the container, and lives at ../configs locally.
CONFIGS_DIR = next(p for p in (_BACKEND / "configs", _BACKEND.parent / "configs") if p.is_dir())

NO_AI_JOIN_DELAY = {"join_delay_min_seconds": 0, "join_delay_max_seconds": 0}


def preset(name: str, *, timing: dict | None = None, ai: dict | None = None) -> ExperimentConfig:
    """Load a shipped preset, optionally overriding timing / ai fields (e.g. tiny durations)."""
    config = load_config(CONFIGS_DIR / f"{name}.yml")
    if not (timing or ai):
        return config
    data = config.snapshot()
    data["timing"].update(timing or {})
    data["ai"].update(ai or {})
    return ExperimentConfig.model_validate(data)


class FakeWS:
    """Stands in for a participant's WebSocket; records every frame the server sends."""

    def __init__(self):
        self.frames: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    def types(self) -> list[str]:
        return [f["type"] for f in self.frames]

    def of_type(self, type_: str) -> list[dict]:
        return [f["payload"] for f in self.frames if f["type"] == type_]


async def wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)
