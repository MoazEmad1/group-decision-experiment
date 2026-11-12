from pathlib import Path

import pytest

from app.config import ConfigError, ExperimentConfig, load_config

BACKEND_DIR = Path(__file__).resolve().parent.parent


def repo_file(rel: str) -> Path:
    """Find a repo-root file both locally (backend/../) and in the container (/app, bind-mounted)."""
    for base in (BACKEND_DIR, BACKEND_DIR.parent):
        candidate = base / rel
        if candidate.exists():
            return candidate
    pytest.fail(f"{rel} not found next to or above {BACKEND_DIR}")


VALID_YAML = """
condition: test
participants:
  - type: human
  - type: ai
  - type: ai
"""


def write(tmp_path: Path, content: str, name: str = "config.yml") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


# --- presets ---------------------------------------------------------------


@pytest.mark.parametrize(
    "preset, humans, ais",
    [
        ("configs/3_humans.yml", 3, 0),
        ("configs/2_humans_1_ai.yml", 2, 1),
        ("configs/1_human_2_ai.yml", 1, 2),
    ],
)
def test_presets_load_with_expected_composition(preset, humans, ais):
    config = load_config(repo_file(preset))
    assert config.human_count == humans
    assert config.ai_count == ais
    assert config.condition == Path(preset).stem
    assert config.timing.chat_seconds == 60
    assert config.timing.vote_seconds == 15
    assert config.ai.mode == "rule_based"


def test_root_config_matches_1_human_2_ai_preset():
    assert load_config(repo_file("config.yml")) == load_config(repo_file("configs/1_human_2_ai.yml"))


def test_llm_preset_differs_from_1_human_2_ai_only_in_mode_and_condition():
    llm = load_config(repo_file("configs/1_human_2_ai_llm.yml"))
    base = load_config(repo_file("configs/1_human_2_ai.yml"))
    assert llm.ai.mode == "llm"
    assert llm.ai.llm_model == "claude-haiku-5-5"
    assert llm.model_copy(update={"condition": base.condition, "ai": base.ai}) == base
    assert llm.ai.model_copy(update={"mode": "rule_based"}) == base.ai


# --- path resolution -------------------------------------------------------


def test_config_path_env_override(tmp_path, monkeypatch):
    path = write(tmp_path, VALID_YAML.replace("condition: test", "condition: from_env"), "other.yml")
    monkeypatch.setenv("CONFIG_PATH", str(path))
    assert load_config().condition == "from_env"


def test_default_path_is_config_yml_in_cwd(tmp_path, monkeypatch):
    write(tmp_path, VALID_YAML)
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    assert load_config().condition == "test"


def test_explicit_path_beats_env(tmp_path, monkeypatch):
    explicit = write(tmp_path, VALID_YAML.replace("condition: test", "condition: explicit"), "a.yml")
    env = write(tmp_path, VALID_YAML.replace("condition: test", "condition: env"), "b.yml")
    monkeypatch.setenv("CONFIG_PATH", str(env))
    assert load_config(explicit).condition == "explicit"


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yml")


# --- defaults and snapshot -------------------------------------------------


def test_defaults_applied_when_sections_omitted(tmp_path):
    config = load_config(write(tmp_path, VALID_YAML))
    assert config.timing.chat_seconds == 60
    assert config.timing.vote_seconds == 15
    assert config.ai.mode == "rule_based"
    assert config.ai.reply_delay_min_seconds <= config.ai.reply_delay_max_seconds
    assert (config.ai.join_delay_min_seconds, config.ai.join_delay_max_seconds) == (3, 10)
    assert config.ai.llm_timeout_seconds == 8


def test_snapshot_round_trips():
    config = load_config(repo_file("configs/2_humans_1_ai.yml"))
    snapshot = config.snapshot()
    assert isinstance(snapshot["participants"], list)  # JSON-safe, ready for a jsonb column
    assert ExperimentConfig.model_validate(snapshot) == config


# --- invalid configs -------------------------------------------------------


@pytest.mark.parametrize(
    "content, error_fragment",
    [
        ("condition: x\nparticipants:\n  - type: human\n  - type: ai\n", "exactly 3 participants"),
        (
            "condition: x\nparticipants:\n  - type: human\n  - type: ai\n  - type: ai\n  - type: ai\n",
            "exactly 3 participants",
        ),
        ("condition: x\nparticipants:\n  - type: ai\n  - type: ai\n  - type: ai\n", "at least one human"),
        ("condition: x\nparticipants:\n  - type: human\n  - type: robot\n  - type: ai\n", "type"),
        (VALID_YAML + "timing:\n  chat_seconds: 0\n", "chat_seconds"),
        (VALID_YAML + "timing:\n  vote_seconds: -5\n", "vote_seconds"),
        (VALID_YAML + "ai:\n  mode: gpt\n", "mode"),
        (
            VALID_YAML + "ai:\n  reply_delay_min_seconds: 8\n  reply_delay_max_seconds: 3\n",
            "reply_delay_min_seconds must be <=",
        ),
        (
            VALID_YAML + "ai:\n  join_delay_min_seconds: 10\n  join_delay_max_seconds: 3\n",
            "join_delay_min_seconds must be <=",
        ),
        (VALID_YAML + "ai:\n  join_delay_min_seconds: -1\n", "join_delay_min_seconds"),
        (VALID_YAML + "timming:\n  chat_seconds: 30\n", "timming"),  # typo in key is rejected
        (VALID_YAML + "timing:\n  chat_secnds: 30\n", "chat_secnds"),
        ("participants:\n  - type: human\n  - type: ai\n  - type: ai\n", "condition"),
        ("condition: ''\n" + VALID_YAML.split("\n", 2)[2], "condition"),
    ],
)
def test_invalid_configs_rejected(tmp_path, content, error_fragment):
    with pytest.raises(ConfigError, match=error_fragment):
        load_config(write(tmp_path, content))


@pytest.mark.parametrize("content", ["- just\n- a list\n", "", "just a string\n"])
def test_non_mapping_rejected(tmp_path, content):
    with pytest.raises(ConfigError, match="mapping"):
        load_config(write(tmp_path, content))


def test_malformed_yaml_rejected(tmp_path):
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write(tmp_path, "condition: [unclosed\n"))
