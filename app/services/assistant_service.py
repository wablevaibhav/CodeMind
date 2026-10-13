"""Orchestrates one assistant request: prompt -> inference -> validation -> result envelope."""

import threading
import uuid
from collections.abc import Callable
from typing import TypeVar

from app.core.config import Settings
from app.core.exceptions import CodeMindError, RequestCancelledError
from app.core.logging import Metrics, get_logger, request_id_var
from app.schemas.requests import UserQuestion
from app.schemas.responses import (
    AssistantResponse,
    AssistantResult,
    LearningResponse,
    LearningResult,
)
from app.services.ollama_client import LLMClient
from app.services.prompt_builder import PromptBuilder
from app.services.retry_service import GenerationParams, StructuredGenerator, TransportRetryPolicy
from app.utils.json_parser import partial_string_field
from app.utils.timing import Deadline, Stopwatch

R = TypeVar("R", bound=AssistantResponse)
log = get_logger("codemind.assistant")


class ModelSelector:
    """Thread-safe holder for the model used when a request does not name one."""

    def __init__(self, initial: str) -> None:
        self._lock = threading.Lock()
        self._model = initial

    def get(self) -> str:
        with self._lock:
            return self._model

    def set(self, model: str) -> None:
        with self._lock:
            self._model = model


class AssistantService:
    def __init__(
        self,
        llm: LLMClient,
        settings: Settings,
        selector: ModelSelector | None = None,
        *,
        prompts: PromptBuilder | None = None,
        generator: StructuredGenerator | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.selector = selector or ModelSelector(settings.ollama_model)
        self.metrics = metrics or Metrics()
        self.prompts = prompts or PromptBuilder(
            max_previous_output_chars=settings.max_previous_output_chars,
            max_feedback_chars=settings.max_feedback_chars,
        )
        self.generator = generator or StructuredGenerator(
            llm,
            self.prompts,
            max_generation_retries=settings.max_generation_retries,
            transport=TransportRetryPolicy(
                max_retries=settings.max_transport_retries,
                max_delay=settings.max_retry_backoff_seconds,
            ),
            strict_schema=settings.strict_schema,
        )

    def _run(
        self,
        question: UserQuestion,
        response_model: type[R],
        cancel: threading.Event | None,
        on_partial: Callable[[str], None] | None = None,
    ) -> tuple[R, str, str, object]:
        request_id = request_id_var.get() or uuid.uuid4().hex[:12]
        model = question.model or self.selector.get()
        params = GenerationParams(
            model=model,
            temperature=(
                question.temperature
                if question.temperature is not None
                else self.settings.default_temperature
            ),
            max_tokens=question.max_output_tokens or self.settings.max_output_tokens,
            call_timeout=self.settings.ollama_timeout_seconds,
        )
        deadline = Deadline(self.settings.request_deadline_seconds)
        watch = Stopwatch()
        fields = {"model": model, "mode": question.mode.value}

        on_token = None
        if on_partial is not None:
            last_sent = ""

            def on_token(text: str) -> None:
                nonlocal last_sent
                if cancel is not None and cancel.is_set():
                    raise RequestCancelledError()
                partial = partial_string_field(text, "answer")
                if partial and partial != last_sent:
                    last_sent = partial
                    on_partial(partial)

        try:
            response, retry = self.generator.generate(
                question, response_model, params, deadline, cancel, on_token
            )
        except CodeMindError as exc:
            category = str(exc.category) if exc.category else exc.code
            retry_meta = exc.retry
            outcome = "exhausted" if exc.code == "generation_failed" else "failed"
            if retry_meta is not None and retry_meta.history:
                last = retry_meta.history[-1].failure
                category = str(last) if last else category
            self.metrics.record(
                mode=question.mode.value,
                outcome=outcome,
                failure=category,
                elapsed_ms=watch.elapsed_ms(),
            )
            log.warning(
                "request_failed",
                extra={
                    **fields,
                    "request_id": request_id,
                    "validation_result": "invalid" if outcome == "exhausted" else "n/a",
                    "attempts": retry_meta.attempts if retry_meta else 0,
                    "failure_category": category,
                    "outcome": outcome,
                    "latency_ms": watch.elapsed_ms(),
                },
            )
            raise

        # Model-supplied sources are never trusted; only user-provided ones are returned.
        response = response.model_copy(update={"sources": list(question.sources)})
        outcome = retry.outcome.value
        self.metrics.record(
            mode=question.mode.value, outcome=outcome, failure=None, elapsed_ms=watch.elapsed_ms()
        )
        log.info(
            "request_completed",
            extra={
                **fields,
                "request_id": request_id,
                "validation_result": "valid",
                "attempts": retry.attempts,
                "transport_retries": retry.transport_retries,
                "outcome": outcome,
                "latency_ms": watch.elapsed_ms(),
            },
        )
        return response, request_id, model, retry

    def answer(
        self,
        question: UserQuestion,
        *,
        cancel: threading.Event | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> AssistantResult:
        """`on_partial` receives the answer text so far while the model streams (display only;
        the returned result is always fully validated)."""
        watch = Stopwatch()
        response, request_id, model, retry = self._run(
            question, AssistantResponse, cancel, on_partial
        )
        return AssistantResult(
            request_id=request_id,
            mode=question.mode.value,
            model=model,
            response=response,
            retry=retry,  # type: ignore[arg-type]
            elapsed_ms=watch.elapsed_ms(),
        )

    def answer_learning(
        self,
        question: UserQuestion,
        *,
        cancel: threading.Event | None = None,
        on_partial: Callable[[str], None] | None = None,
    ) -> LearningResult:
        watch = Stopwatch()
        response, request_id, model, retry = self._run(
            question, LearningResponse, cancel, on_partial
        )
        return LearningResult(
            request_id=request_id,
            mode=question.mode.value,
            model=model,
            response=response,
            retry=retry,  # type: ignore[arg-type]
            elapsed_ms=watch.elapsed_ms(),
        )
