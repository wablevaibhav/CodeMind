"""Typed, validated configuration (environment variables and optional .env file)."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Chosen for latency: ~1.9 GB, runs entirely on a 4 GB GPU (~40 tok/s). Larger models are
# better but spill to CPU on small GPUs and take 30 s+ per answer.
DEFAULT_MODEL = "qwen2.5-coder:3b"
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = DEFAULT_MODEL
    ollama_timeout_seconds: float = Field(default=120, gt=0, le=3600)
    ollama_num_ctx: int = Field(default=8192, ge=512, le=131072)
    ollama_keep_alive: str = "30m"  # how long Ollama keeps the model in memory after a request
    warmup_on_start: bool = True  # load the selected model (if installed) when the API starts

    max_generation_retries: int = Field(default=2, ge=0, le=5)
    max_transport_retries: int = Field(default=2, ge=0, le=5)
    max_retry_backoff_seconds: float = Field(default=8, ge=0, le=120)
    request_deadline_seconds: float = Field(default=300, gt=0, le=7200)

    default_temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=1024, ge=64, le=32768)

    strict_schema: bool = True
    max_feedback_chars: int = Field(default=1200, ge=100, le=10000)
    max_previous_output_chars: int = Field(default=2000, ge=100, le=20000)

    log_level: str = "INFO"

    @field_validator("ollama_base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v

    @field_validator("ollama_model", mode="before")
    @classmethod
    def _blank_model_uses_default(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return DEFAULT_MODEL
        return v.strip() if isinstance(v, str) else v

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in _LOG_LEVELS:
            raise ValueError(f"must be one of {sorted(_LOG_LEVELS)}")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
