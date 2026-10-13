import pytest

from app.schemas.errors import FailureCategory, ValidationFailure, ValidationIssue
from app.schemas.requests import (
    ChatRequest,
    DebugRequest,
    ExplainRequest,
    GenerateRequest,
    LearnRequest,
    Mode,
    ReviewRequest,
    UserQuestion,
)
from app.schemas.responses import AssistantResponse, Intent, LearningResponse
from app.services import learning_service as build
from app.services.prompt_builder import MODE_INTENT, PromptBuilder, detect_mode, truncate
from tests.conftest import valid_json

SCHEMA = AssistantResponse.model_json_schema()


def test_system_prompt_embeds_schema_and_intent():
    msgs = PromptBuilder().initial(UserQuestion(question="q", mode=Mode.DEBUG), SCHEMA)
    system = msgs[0].content
    assert '"AssistantResponse"' in system
    assert '"intent" must be "debugging"' in system
    assert "Never invent API names" in system


def test_user_code_is_fenced_and_labelled_untrusted():
    q = UserQuestion(question="why?", code="x = '```'\nprint(1)", language="python")
    user = PromptBuilder().initial(q, SCHEMA)[1].content
    assert "untrusted data" in user
    assert "````" in user  # fence longer than any backtick run in the code
    assert "Language: python" in user


def test_retry_messages_include_feedback_and_truncate_previous():
    failure = ValidationFailure(
        category=FailureCategory.SCHEMA_VIOLATION,
        summary="bad",
        issues=[ValidationIssue(path="status", message="invalid enum", error_type="enum")],
    )
    pb = PromptBuilder(max_previous_output_chars=100)
    q = UserQuestion(question="q")
    msgs = pb.regenerate(q, SCHEMA, "y" * 1000, failure)
    assert msgs[-2].role == "assistant" and len(msgs[-2].content) <= 100
    assert "status: invalid enum" in msgs[-1].content
    rep = pb.repair(q, SCHEMA, "y" * 1000, failure)
    assert "status: invalid enum" in rep[1].content
    assert rep[0].role == "system"


def test_history_is_replayed_between_system_and_question():
    from app.schemas.requests import HistoryTurn

    q = UserQuestion(
        question="make it faster",
        history=[
            HistoryTurn(role="assistant", content="stray leading assistant turn"),
            HistoryTurn(role="user", content="write a sort"),
            HistoryTurn(role="assistant", content="Use sorted()."),
        ],
    )
    msgs = PromptBuilder().initial(q, SCHEMA)
    assert [m.role for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1].content == "write a sort"
    # assistant turns are replayed as JSON so the model keeps the output contract
    assert '"answer": "Use sorted()."' in msgs[2].content
    assert "make it faster" in msgs[3].content


def test_history_respects_character_budget():
    from app.schemas.requests import HistoryTurn

    pb = PromptBuilder()
    pb.max_history_chars = 100
    q = UserQuestion(
        question="q",
        history=[
            HistoryTurn(role="user", content="old " * 100),
            HistoryTurn(role="assistant", content="a" * 10),
            HistoryTurn(role="user", content="recent"),
        ],
    )
    roles_contents = [(m.role, m.content[:6]) for m in pb.initial(q, SCHEMA)[1:-1]]
    assert ("user", "recent") in roles_contents
    assert all("old" not in c for _, c in roles_contents)


def test_truncate():
    assert truncate("abc", 10) == "abc"
    assert len(truncate("a" * 100, 30)) <= 30


def test_every_mode_has_template_and_intent():
    pb = PromptBuilder()
    assert set(MODE_INTENT) == set(Mode)
    for mode in Mode:
        user = pb.initial(UserQuestion(question="hello world", mode=mode), SCHEMA)[1].content
        assert "hello world" in user
        assert f"Mode: {mode.name}" in user


@pytest.mark.parametrize(
    ("text", "code", "expected"),
    [
        ("How do I reverse a list?", None, Mode.ASK),
        ("I get Traceback (most recent call last)", None, Mode.DEBUG),
        ("Please review this", "x=1", Mode.REVIEW),
        ("explain what does this do", "x=1", Mode.EXPLAIN),
        ("teach me docker", None, Mode.LEARN),
        ("write a function that sorts", None, Mode.GENERATE),
    ],
)
def test_detect_mode(text, code, expected):
    assert detect_mode(text, code) is expected


def test_request_to_question_mapping():
    assert build.from_chat(ChatRequest(question="hi")).mode is Mode.ASK
    assert build.from_chat(ChatRequest(question="hi", mode=Mode.REVIEW)).mode is Mode.REVIEW
    d = build.from_debug(DebugRequest(error_output="KeyError"))
    assert d.mode is Mode.DEBUG and d.error_output == "KeyError" and d.question
    e = build.from_explain(ExplainRequest(code="x", detail="line_by_line"))
    assert "line by line" in e.hints
    g = build.from_generate(GenerateRequest(question="sort", include_tests=True))
    assert "unit tests" in g.hints
    r = build.from_review(ReviewRequest(code="x", focus=["security"]))
    assert "security" in r.hints
    ln = build.from_learn(LearnRequest(topic="Docker", difficulty="advanced"))
    assert ln.mode is Mode.LEARN and "Docker" in ln.question and "advanced" in ln.hints


@pytest.mark.parametrize(
    ("factory", "mode"),
    [
        (lambda: build.from_chat(ChatRequest(question="hi")), Mode.ASK),
        (lambda: build.from_debug(DebugRequest(error_output="boom")), Mode.DEBUG),
        (lambda: build.from_explain(ExplainRequest(code="x=1")), Mode.EXPLAIN),
        (lambda: build.from_generate(GenerateRequest(question="sort")), Mode.GENERATE),
        (lambda: build.from_review(ReviewRequest(code="x=1")), Mode.REVIEW),
    ],
)
def test_all_modes_run_through_shared_pipeline(make_service, factory, mode):
    service, llm = make_service([valid_json(intent=MODE_INTENT[mode].value)])
    result = service.answer(factory())
    assert result.mode == mode.value
    assert result.response.intent is MODE_INTENT[mode]
    assert f"Mode: {mode.name}" in llm.calls[0]["messages"][1].content


def test_learn_mode_returns_learning_contract(make_service):
    raw = valid_json(
        intent="learning",
        quiz=[{"question": "Q", "answer": "A"}],
        exercises=[{"prompt": "P", "solution": "S"}],
        common_mistakes=["m"],
    )
    service, llm = make_service([raw])
    result = service.answer_learning(build.from_learn(LearnRequest(topic="SQL")))
    assert isinstance(result.response, LearningResponse)
    assert result.response.intent is Intent.LEARNING
    assert result.ai_generated and not result.verified
    assert llm.calls[0]["schema"]["title"] == "LearningResponse"


def test_request_validation_rules():
    with pytest.raises(ValueError):
        DebugRequest()
    with pytest.raises(ValueError):
        ChatRequest(question="   ")
    with pytest.raises(ValueError):
        ChatRequest(question="ok", temperature=5)
    with pytest.raises(ValueError):
        ChatRequest(question="ok", unknown_field=1)
    with pytest.raises(ValueError):
        ReviewRequest(code="")
