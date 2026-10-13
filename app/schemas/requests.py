"""Request contracts. Every endpoint validates its payload with one of these."""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.responses import Source

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
MAX_QUESTION = 4000
MAX_CODE = 20000
MAX_ERROR = 10000


class Mode(StrEnum):
    ASK = "ask"
    DEBUG = "debug"
    EXPLAIN = "explain"
    LEARN = "learn"
    GENERATE = "generate"
    REVIEW = "review"


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class LearningAspect(StrEnum):
    CONCEPTS = "concepts"
    EXAMPLES = "examples"
    TUTORIAL = "tutorial"
    EXERCISES = "exercises"
    QUIZ = "quiz"
    COMMON_MISTAKES = "common_mistakes"
    PROJECT_IDEAS = "project_ideas"
    SUMMARY = "summary"


class ReviewFocus(StrEnum):
    CORRECTNESS = "correctness"
    READABILITY = "readability"
    MAINTAINABILITY = "maintainability"
    PERFORMANCE = "performance"
    SECURITY = "security"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationOptions(_Request):
    """Per-request overrides shared by every assistant endpoint."""

    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=64, le=32768)


class HistoryTurn(_Request):
    """A previous message in the conversation (client-held; the server stores nothing)."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class UserQuestion(GenerationOptions):
    """Canonical internal request used by the assistant service for every mode."""

    mode: Mode = Mode.ASK
    question: Text = Field(max_length=MAX_QUESTION)
    code: str | None = Field(default=None, max_length=MAX_CODE)
    error_output: str | None = Field(default=None, max_length=MAX_ERROR)
    language: str | None = Field(default=None, max_length=40)
    framework: str | None = Field(default=None, max_length=80)
    hints: str | None = Field(default=None, max_length=1000, description="Mode-specific guidance.")
    sources: list[Source] = Field(default_factory=list, max_length=20)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=40)


class ChatRequest(GenerationOptions):
    question: Text = Field(max_length=MAX_QUESTION)
    code: str | None = Field(default=None, max_length=MAX_CODE)
    language: str | None = Field(default=None, max_length=40)
    framework: str | None = Field(default=None, max_length=80)
    mode: Mode | None = Field(default=None, description="Omit to auto-detect.")
    sources: list[Source] = Field(default_factory=list, max_length=20)
    history: list[HistoryTurn] = Field(
        default_factory=list, max_length=40, description="Earlier turns, oldest first."
    )


class DebugRequest(GenerationOptions):
    error_output: str | None = Field(default=None, max_length=MAX_ERROR)
    code: str | None = Field(default=None, max_length=MAX_CODE)
    question: str | None = Field(default=None, max_length=MAX_QUESTION)
    language: str | None = Field(default=None, max_length=40)
    framework: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _need_something(self) -> Self:
        if not any((self.error_output, self.code, self.question)):
            raise ValueError("provide at least one of error_output, code or question")
        return self


class ExplainRequest(GenerationOptions):
    code: Text = Field(max_length=MAX_CODE)
    question: str | None = Field(default=None, max_length=MAX_QUESTION)
    detail: Literal["conceptual", "line_by_line"] = "conceptual"
    language: str | None = Field(default=None, max_length=40)


class GenerateRequest(GenerationOptions):
    question: Text = Field(max_length=MAX_QUESTION, description="What to generate.")
    language: str | None = Field(default=None, max_length=40)
    framework: str | None = Field(default=None, max_length=80)
    include_tests: bool = False
    code: str | None = Field(default=None, max_length=MAX_CODE, description="Existing context.")


class ReviewRequest(GenerationOptions):
    code: Text = Field(max_length=MAX_CODE)
    focus: list[ReviewFocus] = Field(default_factory=list, max_length=5)
    question: str | None = Field(default=None, max_length=MAX_QUESTION)
    language: str | None = Field(default=None, max_length=40)


class LearnRequest(GenerationOptions):
    topic: Text = Field(max_length=120, description="e.g. 'FastAPI', 'Docker', 'SQL joins'.")
    difficulty: Difficulty = Difficulty.BEGINNER
    aspects: list[LearningAspect] = Field(
        default_factory=lambda: [
            LearningAspect.CONCEPTS,
            LearningAspect.EXAMPLES,
            LearningAspect.EXERCISES,
            LearningAspect.QUIZ,
        ],
        min_length=1,
        max_length=8,
    )
    question: str | None = Field(default=None, max_length=MAX_QUESTION)


class SelectModelRequest(_Request):
    model: Text = Field(max_length=200)
