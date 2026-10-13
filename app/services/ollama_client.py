"""Ollama HTTP client. Maps transport/HTTP failures onto application exceptions."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from app.core.exceptions import (
    InferenceError,
    InferenceTimeoutError,
    ModelNotFoundError,
    OllamaUnavailableError,
    TransientOllamaError,
)
from app.schemas.responses import ModelInformation

# JSON-Schema `format` for /api/chat landed in Ollama 0.5.0.
STRUCTURED_OUTPUT_MIN_VERSION = (0, 5, 0)
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    total_duration_ms: int | None = None
    eval_count: int | None = None


class LLMClient(Protocol):
    """What the generation pipeline needs from an inference backend."""

    def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        response_schema: dict[str, Any] | None,
        temperature: float,
        max_tokens: int,
        timeout: float | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> ChatResult: ...


def parse_version(text: str) -> tuple[int, ...]:
    m = re.match(r"v?(\d+)\.(\d+)(?:\.(\d+))?", text.strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(g) if g else 0 for g in m.groups())


def _same_model(wanted: str, installed: str) -> bool:
    if wanted == installed:
        return True
    # "llama3" means "llama3:latest" in Ollama.
    return ":" not in wanted and installed == f"{wanted}:latest"


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        *,
        timeout: float = 120.0,
        num_ctx: int = 8192,
        keep_alive: str = "30m",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_timeout = timeout
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self._http = httpx.Client(base_url=self.base_url, transport=transport)
        self._version: str | None = None

    def close(self) -> None:
        self._http.close()

    # -- low level ---------------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        try:
            resp = self._http.request(
                method, path, json=json, timeout=timeout or self.default_timeout
            )
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(f"cannot connect to {self.base_url}") from exc
        except httpx.ConnectTimeout as exc:  # pragma: no cover - subclass ordering guard
            raise OllamaUnavailableError(f"connect timeout to {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(
                f"timeout after {timeout or self.default_timeout}s"
            ) from exc
        except httpx.TransportError as exc:
            raise TransientOllamaError(f"transport error: {type(exc).__name__}") from exc

        if resp.status_code >= 400:
            self._raise_for_status(resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise InferenceError("Ollama returned a non-JSON body") from exc
        if not isinstance(data, dict):
            raise InferenceError("Ollama returned an unexpected body")
        return data

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        try:
            body = resp.json()
            detail = str(body.get("error", "")) if isinstance(body, dict) else ""
        except ValueError:
            detail = ""
        status = resp.status_code
        if status == 404 and "model" in detail.lower():
            raise ModelNotFoundError(detail[:200])
        if status in _TRANSIENT_STATUS:
            raise TransientOllamaError(f"HTTP {status}: {detail[:200]}")
        raise InferenceError(f"HTTP {status}: {detail[:200]}")

    # -- queries -------------------------------------------------------------------------------
    def version(self) -> str:
        if self._version is None:
            data = self._request("GET", "/api/version", timeout=10)
            self._version = str(data.get("version", "0.0.0"))
        return self._version

    def is_reachable(self) -> bool:
        try:
            self._request("GET", "/api/version", timeout=5)
            return True
        except (OllamaUnavailableError, InferenceTimeoutError, TransientOllamaError):
            return False

    def supports_structured_output(self) -> bool:
        return parse_version(self.version()) >= STRUCTURED_OUTPUT_MIN_VERSION

    def list_models(self) -> list[ModelInformation]:
        data = self._request("GET", "/api/tags", timeout=10)
        models: list[ModelInformation] = []
        for item in data.get("models", []):
            details = item.get("details") or {}
            modified: datetime | None = None
            if item.get("modified_at"):
                try:
                    modified = datetime.fromisoformat(str(item["modified_at"]))
                except ValueError:
                    modified = None
            models.append(
                ModelInformation(
                    name=item.get("name") or item.get("model") or "",
                    size_bytes=item.get("size"),
                    modified_at=modified,
                    family=details.get("family"),
                    parameter_size=details.get("parameter_size"),
                    quantization_level=details.get("quantization_level"),
                )
            )
        return models

    def has_model(self, name: str) -> bool:
        return any(_same_model(name, m.name) for m in self.list_models())

    def pull_model(self, name: str, *, timeout: float = 3600.0) -> None:
        """Download a model. Only ever called on explicit user request."""
        self._request("POST", "/api/pull", json={"model": name, "stream": False}, timeout=timeout)

    # -- inference -----------------------------------------------------------------------------
    def warmup(self, model: str) -> None:
        """Load a model into memory (same context size, so it is reused) without generating."""
        payload = {
            "model": model,
            "messages": [],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx},
        }
        self._request("POST", "/api/chat", json=payload, timeout=120)

    def _stream_chat(
        self,
        payload: dict[str, Any],
        model: str,
        timeout: float | None,
        on_token: Callable[[str], None],
    ) -> ChatResult:
        parts: list[str] = []
        final: dict[str, Any] = {}
        try:
            with self._http.stream(
                "POST", "/api/chat", json=payload, timeout=timeout or self.default_timeout
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._raise_for_status(resp)
                for line in resp.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError as exc:
                        raise InferenceError("Ollama sent a malformed stream chunk") from exc
                    if chunk.get("error"):
                        raise InferenceError(str(chunk["error"])[:200])
                    piece = (chunk.get("message") or {}).get("content", "")
                    if piece:
                        parts.append(piece)
                        on_token("".join(parts))
                    if chunk.get("done"):
                        final = chunk
                        break
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(f"cannot connect to {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError("timeout while streaming") from exc
        except httpx.TransportError as exc:
            raise TransientOllamaError(f"transport error: {type(exc).__name__}") from exc
        total = final.get("total_duration")
        return ChatResult(
            content="".join(parts),
            model=str(final.get("model", model)),
            total_duration_ms=int(total / 1_000_000) if isinstance(total, int | float) else None,
            eval_count=final.get("eval_count"),
        )

    def chat(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        response_schema: dict[str, Any] | None,
        temperature: float,
        max_tokens: int,
        timeout: float | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> ChatResult:
        """Run one chat completion. With `on_token`, the reply is streamed and the callback
        receives the cumulative text after every chunk (it may raise to abort the stream)."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": on_token is not None,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        if response_schema is not None:
            # Constrained decoding when supported; otherwise plain JSON mode (schema is still
            # in the prompt and the response is validated either way).
            payload["format"] = response_schema if self.supports_structured_output() else "json"
        if on_token is not None:
            return self._stream_chat(payload, model, timeout, on_token)
        data = self._request("POST", "/api/chat", json=payload, timeout=timeout)
        message = data.get("message") or {}
        total = data.get("total_duration")
        return ChatResult(
            content=str(message.get("content", "")),
            model=str(data.get("model", model)),
            total_duration_ms=int(total / 1_000_000) if isinstance(total, int | float) else None,
            eval_count=data.get("eval_count"),
        )
