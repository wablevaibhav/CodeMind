"""Application exceptions. `public_message` is safe to show to API clients."""

from typing import Any

from app.schemas.errors import FailureCategory


class CodeMindError(Exception):
    code = "internal_error"
    http_status = 500
    public_message = "An internal error occurred."
    category: FailureCategory | None = None
    retryable = False

    def __init__(self, message: str | None = None) -> None:
        # `message` is for logs/developers; clients only ever see `public_message`.
        super().__init__(message or self.public_message)
        self.retry: Any = None  # RetryMetadata, attached by the generation pipeline


class ConfigurationError(CodeMindError):
    code = "configuration_error"
    public_message = "The application is misconfigured."


class OllamaUnavailableError(CodeMindError):
    code = "ollama_unavailable"
    http_status = 503
    public_message = "The local Ollama server is not reachable."
    category = FailureCategory.UNAVAILABLE
    retryable = True


class ModelNotFoundError(CodeMindError):
    code = "model_not_found"
    http_status = 404
    public_message = "The requested model is not installed in Ollama."
    category = FailureCategory.MODEL_NOT_FOUND


class InferenceTimeoutError(CodeMindError):
    code = "inference_timeout"
    http_status = 504
    public_message = "The model did not respond in time."
    category = FailureCategory.TIMEOUT
    retryable = True


class TransientOllamaError(CodeMindError):
    code = "ollama_transient_error"
    http_status = 502
    public_message = "Ollama returned a temporary error."
    category = FailureCategory.TRANSIENT_HTTP
    retryable = True


class InferenceError(CodeMindError):
    code = "inference_error"
    http_status = 502
    public_message = "The model failed to produce a response."
    category = FailureCategory.INFERENCE_ERROR


class DeadlineExceededError(CodeMindError):
    code = "deadline_exceeded"
    http_status = 504
    public_message = "The request exceeded its time limit."
    category = FailureCategory.DEADLINE_EXCEEDED


class RequestCancelledError(CodeMindError):
    code = "request_cancelled"
    http_status = 499
    public_message = "The request was cancelled."
    category = FailureCategory.CANCELLED


class GenerationFailedError(CodeMindError):
    """Retries exhausted without a schema-valid response."""

    code = "generation_failed"
    http_status = 502
    public_message = "The model could not produce a valid structured response."
