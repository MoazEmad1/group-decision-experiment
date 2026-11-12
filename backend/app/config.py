"""Experiment configuration: YAML file -> validated, immutable pydantic model.

Path resolution order: explicit argument > CONFIG_PATH env var > ./config.yml.
"""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_CONFIG_PATH = "config.yml"
PARTICIPANTS_PER_SESSION = 3


class ConfigError(Exception):
    """Raised when the config file is missing, unparseable or invalid."""


class _Strict(BaseModel):
    # extra="forbid" turns typos (e.g. "chat_secnds") into errors instead of silent defaults.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParticipantConfig(_Strict):
    type: Literal["human", "ai"]


class TimingConfig(_Strict):
    chat_seconds: float = Field(default=60, gt=0)
    vote_seconds: float = Field(default=15, gt=0)


class AIConfig(_Strict):
    mode: Literal["rule_based", "llm"] = "rule_based"
    llm_model: str = Field(default="claude-haiku-5-5", min_length=1)
    reply_delay_min_seconds: float = Field(default=2, ge=0)
    reply_delay_max_seconds: float = Field(default=6, ge=0)
    # Each AI "joins" the waiting room after a random delay in this range, so a lone human
    # waits like they would for other humans (blind condition). 0/0 = join instantly.
    join_delay_min_seconds: float = Field(default=3, ge=0)
    join_delay_max_seconds: float = Field(default=10, ge=0)
    # Hard cap per AI per round; also what stops two AIs from replying to each other forever.
    max_messages_per_round: int = Field(default=3, ge=0)
    # Simulated typing time (jittered), so longer messages take longer to appear.
    typing_seconds_per_char: float = Field(default=0.06, ge=0)

    @model_validator(mode="after")
    def _delay_ranges(self):
        if self.reply_delay_min_seconds > self.reply_delay_max_seconds:
            raise ValueError("reply_delay_min_seconds must be <= reply_delay_max_seconds")
        if self.join_delay_min_seconds > self.join_delay_max_seconds:
            raise ValueError("join_delay_min_seconds must be <= join_delay_max_seconds")
        return self


class ExperimentConfig(_Strict):
    condition: str = Field(min_length=1)
    participants: tuple[ParticipantConfig, ...]
    timing: TimingConfig = TimingConfig()
    ai: AIConfig = AIConfig()

    @model_validator(mode="after")
    def _composition(self):
        if len(self.participants) != PARTICIPANTS_PER_SESSION:
            raise ValueError(
                f"exactly {PARTICIPANTS_PER_SESSION} participants required, got {len(self.participants)}"
            )
        if self.human_count < 1:
            raise ValueError("at least one human participant required")
        return self

    @property
    def human_count(self) -> int:
        return sum(p.type == "human" for p in self.participants)

    @property
    def ai_count(self) -> int:
        return sum(p.type == "ai" for p in self.participants)

    def snapshot(self) -> dict:
        """JSON-safe dict stored with each session for reproducibility."""
        return self.model_dump(mode="json")


def resolve_config_path(path: str | os.PathLike | None = None) -> Path:
    return Path(path or os.environ.get("CONFIG_PATH") or DEFAULT_CONFIG_PATH)


def load_config(path: str | os.PathLike | None = None) -> ExperimentConfig:
    resolved = resolve_config_path(path)
    if not resolved.is_file():
        raise ConfigError(f"Config file not found: {resolved}")

    try:
        data = yaml.safe_load(resolved.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"Config file {resolved} is not valid YAML: {e}") from e

    if not isinstance(data, dict):
        raise ConfigError(f"Config file {resolved} must contain a YAML mapping at the top level")

    try:
        return ExperimentConfig.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"Invalid config {resolved}:\n{e}") from e
