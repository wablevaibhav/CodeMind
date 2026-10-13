import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_MODEL, Settings


def make(**kw):
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_defaults():
    s = make()
    assert s.ollama_base_url == "http://localhost:11434"
    assert s.max_generation_retries == 2
    assert s.ollama_model == DEFAULT_MODEL
    assert s.strict_schema is True


def test_blank_model_uses_default(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "")
    assert make().ollama_model == DEFAULT_MODEL


def test_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    monkeypatch.setenv("MAX_GENERATION_RETRIES", "1")
    s = make()
    assert (s.ollama_model, s.max_generation_retries) == ("llama3.2:3b", 1)


@pytest.mark.parametrize(
    "bad",
    [
        {"ollama_base_url": "localhost:11434"},
        {"max_generation_retries": -1},
        {"default_temperature": 5},
        {"ollama_timeout_seconds": 0},
        {"log_level": "LOUD"},
    ],
)
def test_invalid_config_rejected(bad):
    with pytest.raises(ValidationError):
        make(**bad)


def test_trailing_slash_stripped():
    assert make(ollama_base_url="http://x:1/").ollama_base_url == "http://x:1"
