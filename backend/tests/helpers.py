from pathlib import Path

from app.config import ExperimentConfig, load_config

_BACKEND = Path(__file__).resolve().parent.parent
# configs/ is bind-mounted at /app/configs in the container, and lives at ../configs locally.
CONFIGS_DIR = next(p for p in (_BACKEND / "configs", _BACKEND.parent / "configs") if p.is_dir())


def preset(name: str) -> ExperimentConfig:
    return load_config(CONFIGS_DIR / f"{name}.yml")
