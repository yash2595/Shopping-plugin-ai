import pytest
from pydantic import ValidationError

from shopping_ai import AIConfig


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "   "},
        {"provider": "gemini"},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": True},
        {"max_steps": True},
        {"max_steps": 1.5},
        {"max_steps": 31},
        {"max_output_tokens": 99},
        {"max_context_chars": 999},
    ],
)
def test_invalid_runtime_config_is_rejected(changes):
    with pytest.raises(ValidationError):
        AIConfig.model_validate(changes)


def test_environment_limits_are_parsed_and_model_is_trimmed(monkeypatch):
    settings = {
        "AI_PROVIDER": "gemini",
        "AI_MODEL": "  account-model  ",
        "AI_TIMEOUT_SECONDS": "12.5",
        "AI_MAX_CONTEXT_CHARS": "12000",
        "AI_MAX_SNAPSHOT_AGE_SECONDS": "60",
        "AI_MAX_STEPS": "4",
        "AI_MAX_OUTPUT_TOKENS": "500",
        "AI_MAX_CONCURRENT_CALLS": "3",
        "AI_CAPACITY_WAIT_SECONDS": "0.5",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    config = AIConfig.from_env()
    assert config.model == "account-model"
    assert config.timeout_seconds == 12.5
    assert config.max_context_chars == 12000
    assert config.max_snapshot_age_seconds == 60
    assert config.max_steps == 4
    assert config.max_output_tokens == 500
    assert config.max_concurrent_calls == 3
    assert config.capacity_wait_seconds == 0.5


@pytest.mark.parametrize("value", ["", "true", "1.5", "private-invalid-value"])
def test_bad_integer_environment_is_not_echoed(monkeypatch, value):
    monkeypatch.setenv("AI_MAX_STEPS", value)
    with pytest.raises(ValueError, match="AI_MAX_STEPS") as error:
        AIConfig.from_env()
    if value:
        assert value not in str(error.value)


def test_environment_still_enforces_bounds(monkeypatch):
    monkeypatch.setenv("AI_MAX_STEPS", "999")
    with pytest.raises(ValidationError):
        AIConfig.from_env()
