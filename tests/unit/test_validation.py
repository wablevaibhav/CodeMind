import json

import pytest

from app.schemas.errors import FailureCategory
from app.schemas.responses import AssistantResponse, LearningResponse
from app.services.response_validator import ResponseValidationError, validate_response
from app.utils.json_parser import JSONExtractionError, extract_json_text
from tests.conftest import valid_json, valid_payload


def fail(raw: str, **kw):
    with pytest.raises(ResponseValidationError) as info:
        validate_response(raw, AssistantResponse, **kw)
    return info.value.failure


def test_valid_json_passes():
    r = validate_response(valid_json(), AssistantResponse)
    assert r.answer.startswith("Use a list")
    assert r.code_blocks[0].language == "python"


def test_fenced_and_prose_wrapped_json_is_extracted():
    assert validate_response(f"```json\n{valid_json()}\n```", AssistantResponse)
    assert validate_response(f"Here you go: {valid_json()} hope it helps", AssistantResponse)


def test_invalid_json():
    f = fail('{"status": "success", "answer": ')
    assert f.category is FailureCategory.INVALID_JSON


def test_not_an_object():
    assert fail("[1, 2, 3]").category is FailureCategory.INVALID_JSON


def test_empty_response():
    assert fail("   ").category is FailureCategory.EMPTY_RESPONSE


def test_missing_required_field():
    payload = valid_payload()
    del payload["answer"]
    f = fail(json.dumps(payload))
    assert f.category is FailureCategory.SCHEMA_VIOLATION
    assert any(i.path == "answer" and i.error_type == "missing" for i in f.issues)


def test_invalid_enum_value():
    f = fail(valid_json(status="maybe"))
    assert f.category is FailureCategory.SCHEMA_VIOLATION
    assert any(i.path == "status" for i in f.issues)


def test_incorrect_field_type():
    f = fail(valid_json(steps="not a list"))
    assert any(i.path == "steps" for i in f.issues)


def test_nested_field_errors_have_paths():
    f = fail(valid_json(code_blocks=[{"language": "python"}]))
    assert any(i.path == "code_blocks.0.code" for i in f.issues)


def test_extra_fields_rejected_in_strict_mode():
    f = fail(valid_json(surprise=1), strict=True)
    assert any(i.path == "surprise" for i in f.issues)


def test_extra_fields_dropped_in_lenient_mode():
    payload = valid_payload(surprise=1, code_blocks=[{"language": "py", "code": "x", "extra": 1}])
    r = validate_response(json.dumps(payload), AssistantResponse, strict=False)
    assert not hasattr(r, "surprise")
    assert r.code_blocks[0].code == "x"


def test_needs_clarification_requires_questions():
    assert (
        fail(valid_json(status="needs_clarification")).category is FailureCategory.SCHEMA_VIOLATION
    )
    ok = validate_response(
        valid_json(status="needs_clarification", follow_up_questions=["Which DB?"]),
        AssistantResponse,
    )
    assert ok.follow_up_questions == ["Which DB?"]


def test_error_status_requires_error_object():
    assert fail(valid_json(status="error")).category is FailureCategory.SCHEMA_VIOLATION


def test_feedback_never_contains_submitted_values():
    secret = "SUPER-SECRET-VALUE-123"
    f = fail(valid_json(steps=secret, answer=123))
    assert secret not in f.feedback()


def test_feedback_is_bounded():
    payload = valid_payload(steps=[1] * 30)  # 30 type errors, capped at 10 issues
    f = fail(json.dumps(payload))
    assert len(f.issues) <= 10
    assert len(f.feedback(max_chars=200)) <= 200


def test_learning_response_extends_contract():
    raw = valid_json(
        intent="learning",
        quiz=[{"question": "Q?", "answer": "A", "options": ["A", "B"]}],
        exercises=[{"prompt": "Do X"}],
    )
    r = validate_response(raw, LearningResponse)
    assert r.quiz[0].answer == "A"
    # The base contract does not allow learning fields.
    assert fail(raw).category is FailureCategory.SCHEMA_VIOLATION


def test_json_schema_generated_from_models():
    schema = AssistantResponse.model_json_schema()
    assert set(schema["required"]) == {"status", "intent", "answer"}
    assert schema["additionalProperties"] is False
    assert "code_blocks" in schema["properties"]
    assert schema["$defs"]["ResponseStatus"]["enum"] == ["success", "needs_clarification", "error"]


def test_extract_json_text_errors():
    with pytest.raises(JSONExtractionError):
        extract_json_text("no braces here")
    assert extract_json_text('x {"a": "}"} y') == '{"a": "}"}'
