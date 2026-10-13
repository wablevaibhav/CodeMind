import json
from typing import Any

import pytest

from app.core.config import Settings
from app.services.assistant_service import AssistantService
from app.services.ollama_client import ChatMessage, ChatResult


def valid_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": "success",
        "intent": "coding_question",
        "answer": "Use a list comprehension.",
        "code_blocks": [{"language": "python", "code": "[x * x for x in xs]"}],
        "steps": ["Iterate", "Square"],
    }
    data.update(overrides)
    return data


def valid_json(**overrides: Any) -> str:
    return json.dumps(valid_payload(**overrides))


class FakeLLM:
    """Scripted LLMClient. Each script item is a str (content) or an Exception to raise."""

    def __init__(self, script: list[str | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        response_schema: dict[str, Any] | None,
        temperature: float,
        max_tokens: int,
        timeout: float | None = None,
        on_token: Any = None,
    ) -> ChatResult:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "schema": response_schema,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        if not self.script:
            raise AssertionError("FakeLLM script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if on_token is not None:  # emulate streaming in 7-char chunks
            for end in range(7, len(item) + 7, 7):
                on_token(item[:end])
        return ChatResult(content=item, model=model)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        ollama_model="test-model:1b",
        max_generation_retries=2,
        max_transport_retries=2,
        max_retry_backoff_seconds=1,
        request_deadline_seconds=30,
    )


@pytest.fixture
def make_service(settings: Settings):
    def _make(script: list[str | Exception], **setting_overrides: Any):
        s = settings.model_copy(update=setting_overrides) if setting_overrides else settings
        llm = FakeLLM(script)
        service = AssistantService(llm, s)
        # no real sleeping in tests
        service.generator._sleep = lambda _s: None
        return service, llm

    return _make
