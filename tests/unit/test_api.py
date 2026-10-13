import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ModelNotFoundError
from app.main import create_app
from app.schemas.responses import AssistantResult, LearningResult
from app.services.ollama_client import OllamaClient
from tests.conftest import FakeLLM, valid_json


def make_client(settings, script, ollama_handler=None) -> tuple[TestClient, FakeLLM]:
    llm = FakeLLM(script)
    app = create_app(settings, llm)
    app.state.service.generator._sleep = lambda _s: None
    if ollama_handler:
        app.state.ollama = OllamaClient(
            "http://ollama.test", transport=httpx.MockTransport(ollama_handler)
        )
    return TestClient(app, raise_server_exceptions=False), llm


def ollama_ok(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/version":
        return httpx.Response(200, json={"version": "0.6.2"})
    if req.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "test-model:1b"}, {"name": "b:1"}]})
    return httpx.Response(404, json={"error": "x"})


def test_web_ui_is_served_with_restrictive_headers(settings):
    c, _ = make_client(settings, [])
    r = c.get("/")
    assert r.status_code == 200 and "<title>CodeMind</title>" in r.text
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert "innerHTML" not in r.text  # model output must only be rendered as text


def test_health(settings):
    c, _ = make_client(settings, [])
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_health_ollama_ok_and_down(settings):
    c, _ = make_client(settings, [], ollama_ok)
    body = c.get("/health/ollama").json()
    assert body["reachable"] and body["model_installed"] and body["structured_output_supported"]

    def down(req):
        raise httpx.ConnectError("refused")

    c, _ = make_client(settings, [], down)
    r = c.get("/health/ollama")
    assert r.status_code == 503 and r.json()["reachable"] is False


def test_models_and_select(settings):
    c, _ = make_client(settings, [], ollama_ok)
    body = c.get("/models").json()
    assert [m["name"] for m in body["models"]] == ["test-model:1b", "b:1"]
    assert body["selected_model"] == "test-model:1b" and body["selected_model_installed"]
    r = c.post("/models/select", json={"model": "b:1"})
    assert r.status_code == 200 and r.json()["selected_model"] == "b:1"
    r = c.post("/models/select", json={"model": "missing:9b"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"
    assert c.get("/models").json()["selected_model"] == "b:1"  # unchanged by failed select


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/chat", {"question": "How do I loop?"}),
        ("/debug", {"error_output": "KeyError: 'a'", "code": "d['a']"}),
        ("/explain", {"code": "print(1)"}),
        ("/generate", {"question": "fizzbuzz", "language": "python"}),
        ("/review", {"code": "x = 1", "focus": ["security"]}),
    ],
)
def test_assistant_endpoints_share_the_response_contract(settings, path, payload):
    c, llm = make_client(settings, [valid_json()])
    r = c.post(path, json=payload)
    assert r.status_code == 200, r.text
    body = AssistantResult.model_validate(r.json())
    assert body.schema_valid and body.ai_generated and body.verified is False
    assert body.retry.attempts == 1
    assert body.response.answer
    assert r.headers["x-request-id"] == body.request_id or r.headers["x-request-id"]
    assert len(llm.calls) == 1


def test_learn_endpoint(settings):
    raw = valid_json(intent="learning", quiz=[{"question": "Q", "answer": "A"}])
    c, _ = make_client(settings, [raw])
    r = c.post("/learn", json={"topic": "FastAPI", "difficulty": "beginner"})
    assert r.status_code == 200
    assert LearningResult.model_validate(r.json()).response.quiz[0].answer == "A"


def test_recovered_response_reports_retry_metadata(settings):
    c, _ = make_client(settings, ["garbage", valid_json()])
    body = c.post("/chat", json={"question": "hi"}).json()
    assert body["retry"]["outcome"] == "recovered" and body["retry"]["attempts"] == 2


def test_retry_exhaustion_returns_structured_502(settings):
    c, _ = make_client(settings, ["bad"] * 3)
    r = c.post("/chat", json={"question": "hi"})
    assert r.status_code == 502
    body = r.json()
    assert body["error"]["code"] == "generation_failed"
    assert body["retry"]["outcome"] == "exhausted" and body["retry"]["attempts"] == 3
    assert "bad" not in body["error"]["message"]


def test_invalid_payloads_are_422_without_echoing_input(settings):
    c, llm = make_client(settings, [])
    r = c.post("/chat", json={"question": "", "code": "TOP-SECRET"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
    assert "TOP-SECRET" not in r.text
    assert c.post("/chat", json={"nope": 1}).status_code == 422
    assert c.post("/debug", json={}).status_code == 422
    assert (
        c.post(
            "/chat", content="not json", headers={"content-type": "application/json"}
        ).status_code
        == 422
    )
    assert llm.calls == []


def test_missing_model_is_404_not_retried(settings):
    c, llm = make_client(settings, [ModelNotFoundError("model 'x' secret path C:\\Users")])
    r = c.post("/chat", json={"question": "hi"})
    assert r.status_code == 404
    assert "C:\\Users" not in r.text
    assert len(llm.calls) == 1


def test_unexpected_error_is_sanitised(settings):
    c, _ = make_client(settings, [RuntimeError("secret internals /home/me/key")])
    r = c.post("/chat", json={"question": "hi"})
    assert r.status_code == 500
    assert "secret" not in r.text and "Traceback" not in r.text


def test_unknown_route_uses_error_contract(settings):
    c, _ = make_client(settings, [])
    r = c.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_openapi_documents_all_endpoints(settings):
    c, _ = make_client(settings, [])
    paths = c.get("/openapi.json").json()["paths"]
    for p in (
        "/health",
        "/health/ollama",
        "/models",
        "/models/select",
        "/chat",
        "/debug",
        "/explain",
        "/learn",
        "/generate",
        "/review",
    ):
        assert p in paths


def test_metrics_contain_counters_only(settings):
    c, _ = make_client(settings, [valid_json()])
    c.post("/chat", json={"question": "private question text"})
    r = c.get("/metrics")
    assert r.json()["requests_total"] == 1
    assert "private" not in r.text
