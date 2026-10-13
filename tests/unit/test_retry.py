import threading

import pytest

from app.core.exceptions import (
    DeadlineExceededError,
    GenerationFailedError,
    InferenceError,
    InferenceTimeoutError,
    ModelNotFoundError,
    OllamaUnavailableError,
    RequestCancelledError,
    TransientOllamaError,
)
from app.schemas.errors import AttemptKind, FailureCategory, RetryOutcome
from app.schemas.requests import Mode, UserQuestion
from app.services.retry_service import TransportRetryPolicy
from tests.conftest import valid_json, valid_payload

Q = UserQuestion(question="How do I square a list?", mode=Mode.ASK)


def test_first_attempt_success(make_service):
    service, llm = make_service([valid_json()])
    result = service.answer(Q)
    assert len(llm.calls) == 1
    assert result.retry.outcome is RetryOutcome.SUCCESS
    assert result.retry.attempts == 1
    assert result.schema_valid and result.ai_generated and not result.verified


def test_schema_is_passed_to_the_model(make_service):
    service, llm = make_service([valid_json()])
    service.answer(Q)
    assert llm.calls[0]["schema"]["title"] == "AssistantResponse"
    assert llm.calls[0]["model"] == "test-model:1b"


def test_recovers_after_invalid_json_by_regenerating(make_service):
    service, llm = make_service(["not json at all", valid_json()])
    result = service.answer(Q)
    assert len(llm.calls) == 2
    assert result.retry.outcome is RetryOutcome.RECOVERED
    kinds = [h.kind for h in result.retry.history]
    assert kinds == [AttemptKind.GENERATE, AttemptKind.REGENERATE]
    assert result.retry.history[0].failure is FailureCategory.INVALID_JSON
    # regenerate = original system+user, then rejected output, then feedback
    roles = [m.role for m in llm.calls[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "not valid JSON" in llm.calls[1]["messages"][-1].content


def test_schema_violation_uses_separate_repair_prompt(make_service):
    bad = valid_json(status="maybe")
    service, llm = make_service([bad, valid_json()])
    result = service.answer(Q)
    assert result.retry.history[1].kind is AttemptKind.REPAIR
    repair_msgs = llm.calls[1]["messages"]
    assert [m.role for m in repair_msgs] == ["system", "user"]
    assert "status" in repair_msgs[1].content
    assert "Previous reply" in repair_msgs[1].content


def test_recovers_after_multiple_invalid_responses(make_service):
    service, llm = make_service(["", valid_json(status="nope"), valid_json()])
    result = service.answer(Q)
    assert len(llm.calls) == 3
    assert result.retry.attempts == 3
    assert result.retry.outcome is RetryOutcome.RECOVERED


def test_retry_exhaustion_is_a_controlled_failure(make_service):
    service, llm = make_service(["bad", "bad", "bad", valid_json()])
    with pytest.raises(GenerationFailedError) as info:
        service.answer(Q)
    assert len(llm.calls) == 3  # initial + 2 retries, never the 4th scripted item
    meta = info.value.retry
    assert meta.outcome is RetryOutcome.EXHAUSTED
    assert meta.attempts == 3 and meta.max_attempts == 3
    assert info.value.http_status == 502


def test_zero_generation_retries(make_service):
    service, llm = make_service(["bad", valid_json()], max_generation_retries=0)
    with pytest.raises(GenerationFailedError):
        service.answer(Q)
    assert len(llm.calls) == 1


def test_model_not_found_is_not_retried(make_service):
    service, llm = make_service([ModelNotFoundError("x"), valid_json()])
    with pytest.raises(ModelNotFoundError) as info:
        service.answer(Q)
    assert len(llm.calls) == 1
    assert info.value.retry.outcome is RetryOutcome.FAILED


def test_permanent_inference_error_is_not_retried(make_service):
    service, llm = make_service([InferenceError("HTTP 400"), valid_json()])
    with pytest.raises(InferenceError):
        service.answer(Q)
    assert len(llm.calls) == 1


def test_transient_failure_then_success(make_service):
    service, llm = make_service([TransientOllamaError("503"), valid_json()])
    result = service.answer(Q)
    assert len(llm.calls) == 2
    assert result.retry.transport_retries == 1
    assert result.retry.attempts == 1  # a transport retry is not a generation attempt
    assert result.retry.outcome is RetryOutcome.SUCCESS


def test_unavailable_is_retried_a_bounded_number_of_times(make_service):
    errors = [OllamaUnavailableError("down") for _ in range(10)]
    service, llm = make_service(errors)
    with pytest.raises(OllamaUnavailableError) as info:
        service.answer(Q)
    assert len(llm.calls) == 3  # 1 + max_transport_retries(2)
    assert info.value.retry.transport_retries == 2
    assert info.value.http_status == 503


def test_timeout_is_retried_then_surfaces(make_service):
    service, llm = make_service([InferenceTimeoutError("t")] * 5)
    with pytest.raises(InferenceTimeoutError) as info:
        service.answer(Q)
    assert len(llm.calls) == 3
    assert info.value.http_status == 504


def test_transport_retries_sleep_with_bounded_backoff(make_service):
    service, _ = make_service([TransientOllamaError("x")] * 3)
    sleeps: list[float] = []
    service.generator._sleep = sleeps.append
    service.generator.transport = TransportRetryPolicy(max_retries=2, base_delay=0.5, max_delay=1.0)
    with pytest.raises(TransientOllamaError):
        service.answer(Q)
    assert len(sleeps) == 2
    assert 0.25 <= sleeps[0] <= 0.5
    assert 0.5 <= sleeps[1] <= 1.0


def test_backoff_policy_is_capped_and_jittered():
    p = TransportRetryPolicy(max_retries=5, base_delay=1.0, max_delay=4.0)
    assert p.delay(0, rand=lambda: 0.0) == 0.5
    assert p.delay(0, rand=lambda: 1.0) == 1.0
    assert p.delay(10, rand=lambda: 1.0) == 4.0  # capped
    assert p.delay(10, rand=lambda: 0.0) == 2.0


def test_transport_and_generation_retries_are_independent(make_service):
    service, llm = make_service([TransientOllamaError("x"), "bad", valid_json()])
    result = service.answer(Q)
    assert len(llm.calls) == 3
    assert result.retry.attempts == 2
    assert result.retry.transport_retries == 1


def test_deadline_stops_retries(make_service):
    service, llm = make_service(["bad", valid_json()], request_deadline_seconds=0.001)
    import time

    time.sleep(0.01)
    with pytest.raises(DeadlineExceededError):
        service.answer(Q)
    assert llm.calls == []


def test_cancellation_before_call(make_service):
    service, llm = make_service([valid_json()])
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RequestCancelledError):
        service.answer(Q, cancel=cancel)
    assert llm.calls == []


def test_model_sources_are_discarded(make_service):
    fake = valid_json(sources=[{"title": "Made up", "url": "https://invented.example"}])
    service, _ = make_service([fake])
    assert service.answer(Q).response.sources == []


def test_user_sources_are_returned(make_service):
    from app.schemas.responses import Source

    q = Q.model_copy(update={"sources": [Source(title="My notes")]})
    service, _ = make_service([valid_json()])
    assert service.answer(q).response.sources[0].title == "My notes"


def test_request_overrides(make_service):
    service, llm = make_service([valid_json()])
    q = Q.model_copy(update={"model": "other:3b", "temperature": 0.9, "max_output_tokens": 256})
    service.answer(q)
    call = llm.calls[0]
    assert (call["model"], call["temperature"], call["max_tokens"]) == ("other:3b", 0.9, 256)


def test_retry_prompt_is_bounded(make_service):
    huge = valid_payload(answer="x" * 50000)  # invalid: over max_length
    import json

    service, llm = make_service([json.dumps(huge), valid_json()])
    service.answer(Q)
    repair = llm.calls[1]["messages"][1].content
    assert len(repair) < 5000
    assert "[truncated]" in repair
