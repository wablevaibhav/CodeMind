import json

import httpx
import pytest

from app.core.exceptions import (
    InferenceError,
    InferenceTimeoutError,
    ModelNotFoundError,
    OllamaUnavailableError,
    TransientOllamaError,
)
from app.services.ollama_client import ChatMessage, OllamaClient, parse_version

SCHEMA = {"type": "object", "title": "T"}


def client(handler) -> OllamaClient:
    return OllamaClient("http://ollama.test", num_ctx=4096, transport=httpx.MockTransport(handler))


def chat(c: OllamaClient):
    return c.chat(
        model="m",
        messages=[ChatMessage("user", "hi")],
        response_schema=SCHEMA,
        temperature=0.1,
        max_tokens=100,
    )


def ok_handler(version: str = "0.6.2"):
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": version})
        if req.url.path == "/api/chat":
            seen.append(json.loads(req.content))
            return httpx.Response(
                200, json={"model": "m", "message": {"content": "{}"}, "total_duration": 2_000_000}
            )
        if req.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen2.5-coder:7b",
                            "size": 4_700_000_000,
                            "details": {"parameter_size": "7B"},
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "nope"})

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def test_parse_version():
    assert parse_version("0.5.1") == (0, 5, 1)
    assert parse_version("v0.12.3-rc1") == (0, 12, 3)
    assert parse_version("garbage") == (0, 0, 0)


def test_chat_sends_schema_and_options():
    h = ok_handler()
    result = chat(client(h))
    body = h.seen[0]
    assert body["format"] == SCHEMA
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.1, "num_predict": 100, "num_ctx": 4096}
    assert result.content == "{}" and result.total_duration_ms == 2


def test_old_ollama_falls_back_to_json_mode():
    h = ok_handler(version="0.4.7")
    chat(client(h))
    assert h.seen[0]["format"] == "json"


def test_list_models_and_has_model():
    c = client(ok_handler())
    assert c.list_models()[0].parameter_size == "7B"
    assert c.has_model("qwen2.5-coder:7b")
    assert not c.has_model("llama3")


def test_latest_tag_matching():
    def handler(req):
        return httpx.Response(200, json={"models": [{"name": "llama3:latest"}]})

    assert client(handler).has_model("llama3")


def test_reachability():
    assert client(ok_handler()).is_reachable()

    def boom(req):
        raise httpx.ConnectError("refused")

    assert not client(boom).is_reachable()


def test_connection_failure_maps_to_unavailable():
    def boom(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(OllamaUnavailableError):
        client(boom).list_models()


def test_timeout_maps_to_inference_timeout():
    def slow(req):
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.6.0"})
        raise httpx.ReadTimeout("slow")

    with pytest.raises(InferenceTimeoutError):
        chat(client(slow))


def test_missing_model_maps_to_model_not_found():
    def handler(req):
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.6.0"})
        return httpx.Response(404, json={"error": "model 'm' not found, try pulling it first"})

    with pytest.raises(ModelNotFoundError):
        chat(client(handler))


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses(status):
    def handler(req):
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.6.0"})
        return httpx.Response(status, json={"error": "busy"})

    with pytest.raises(TransientOllamaError):
        chat(client(handler))


def test_client_error_is_permanent():
    def handler(req):
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.6.0"})
        return httpx.Response(400, json={"error": "bad request"})

    with pytest.raises(InferenceError):
        chat(client(handler))


def test_non_json_body():
    def handler(req):
        return httpx.Response(200, text="<html>")

    with pytest.raises(InferenceError):
        client(handler).list_models()
