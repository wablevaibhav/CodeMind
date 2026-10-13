import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import InferenceError, ModelNotFoundError
from app.main import create_app
from app.services.ollama_client import ChatMessage, OllamaClient
from app.utils.json_parser import partial_string_field
from tests.conftest import FakeLLM, valid_json


def test_partial_answer_extraction():
    assert partial_string_field('{"status":"success","ans', "answer") is None
    assert partial_string_field('{"answer": "Hel', "answer") == "Hel"
    assert partial_string_field('{"answer": "a\\nb \\"q\\" \\u00e9"', "answer") == 'a\nb "q" é'
    # unfinished escapes at the tail are dropped, not mis-decoded
    assert partial_string_field('{"answer": "ab\\', "answer") == "ab"
    assert partial_string_field('{"answer": "ab\\u00', "answer") == "ab"
    # stops at the closing quote
    assert partial_string_field('{"answer": "done", "summary": "x"}', "answer") == "done"


def _stream_transport(lines: list[dict], status: int = 200):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.6.0"})
        body = req.content and json.loads(req.content)
        assert body["stream"] is True and body["keep_alive"] == "30m"
        text = "\n".join(json.dumps(x) for x in lines)
        return httpx.Response(status, content=text.encode())

    return httpx.MockTransport(handler)


def _chat(client: OllamaClient, seen: list[str]):
    return client.chat(
        model="m",
        messages=[ChatMessage("user", "hi")],
        response_schema=None,
        temperature=0,
        max_tokens=10,
        on_token=seen.append,
    )


def test_ollama_streaming_accumulates_and_reports_cumulative_text():
    chunks = [
        {"message": {"content": '{"ans'}, "done": False},
        {"message": {"content": 'wer": "x"}'}, "done": False},
        {"message": {"content": ""}, "done": True, "model": "m", "eval_count": 5},
    ]
    client = OllamaClient("http://o.test", transport=_stream_transport(chunks))
    seen: list[str] = []
    result = _chat(client, seen)
    assert result.content == '{"answer": "x"}'
    assert seen == ['{"ans', '{"answer": "x"}']
    assert result.eval_count == 5


def test_ollama_stream_error_chunk_and_status():
    client = OllamaClient("http://o.test", transport=_stream_transport([{"error": "boom"}]))
    with pytest.raises(InferenceError):
        _chat(client, [])
    client = OllamaClient(
        "http://o.test",
        transport=_stream_transport([{"error": "model 'm' not found"}], status=404),
    )
    with pytest.raises(ModelNotFoundError):
        _chat(client, [])


def _parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_chat_stream_endpoint_emits_deltas_then_validated_result(settings):
    llm = FakeLLM([valid_json(answer="Use a list comprehension to square items.")])
    client = TestClient(create_app(settings, llm))
    r = client.post("/chat/stream", json={"question": "square a list"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[-1] == "result" and "delta" in types
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert deltas[-1].startswith("Use a list comprehension")
    result = events[-1]["data"]
    assert result["schema_valid"] is True and result["response"]["answer"].startswith("Use a")


def test_chat_stream_error_event(settings):
    llm = FakeLLM([ModelNotFoundError("secret detail")])
    client = TestClient(create_app(settings, llm))
    events = _parse_sse(client.post("/chat/stream", json={"question": "hi"}).text)
    assert events[-1]["type"] == "error"
    assert events[-1]["status"] == 404
    assert events[-1]["data"]["error"]["code"] == "model_not_found"
    assert "secret" not in json.dumps(events)


def test_stream_validation_failure_still_retries(settings):
    llm = FakeLLM(["not json", valid_json()])
    client = TestClient(create_app(settings, llm))
    events = _parse_sse(client.post("/chat/stream", json={"question": "hi"}).text)
    assert events[-1]["type"] == "result"
    assert events[-1]["data"]["retry"]["outcome"] == "recovered"
