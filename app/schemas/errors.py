"""Failure, retry and error contracts. Free of imports from other app modules."""

from enum import StrEnum

from pydantic import BaseModel, Field


class FailureCategory(StrEnum):
    # Model-output problems (handled by regeneration / repair)
    INVALID_JSON = "invalid_json"
    EMPTY_RESPONSE = "empty_response"
    SCHEMA_VIOLATION = "schema_violation"
    # Transport / environment problems
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    TRANSIENT_HTTP = "transient_http"
    MODEL_NOT_FOUND = "model_not_found"
    INFERENCE_ERROR = "inference_error"
    # Request-level problems
    DEADLINE_EXCEEDED = "deadline_exceeded"
    CANCELLED = "cancelled"


class AttemptKind(StrEnum):
    GENERATE = "generate"
    REGENERATE = "regenerate"
    REPAIR = "repair"


class RetryOutcome(StrEnum):
    SUCCESS = "success"  # valid on the first attempt
    RECOVERED = "recovered"  # valid after one or more retries
    EXHAUSTED = "exhausted"  # retries used up, output never valid
    FAILED = "failed"  # permanent failure, not retried


class AttemptRecord(BaseModel):
    attempt: int
    kind: AttemptKind
    transport_retries: int = 0
    failure: FailureCategory | None = None
    elapsed_ms: int = 0


class RetryMetadata(BaseModel):
    attempts: int
    max_attempts: int
    transport_retries: int = 0
    outcome: RetryOutcome
    elapsed_ms: int
    history: list[AttemptRecord] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    """One schema problem. Never contains the offending input value."""

    path: str
    message: str
    error_type: str


class ValidationFailure(BaseModel):
    category: FailureCategory
    summary: str
    issues: list[ValidationIssue] = Field(default_factory=list)

    def feedback(self, max_chars: int = 1200) -> str:
        """Concise, bounded text suitable for inclusion in a retry prompt."""
        lines = [self.summary]
        lines.extend(f"- {i.path or '<root>'}: {i.message}" for i in self.issues)
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 15].rstrip() + "\n...[truncated]"
        return text


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """Body of every non-2xx API response."""

    error: ErrorDetail
    request_id: str | None = None
    retry: RetryMetadata | None = None
