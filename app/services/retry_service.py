"""Reliable structured generation: transport retries + validation-guided regeneration/repair.

Two independent policies:
  * transport: retry transient failures (connection, timeout, 429/5xx) with bounded,
    jittered exponential backoff. Permanent failures (model missing, 4xx) are never retried.
  * generation: when output is not schema-valid, regenerate with concise feedback (bad JSON)
    or send a separate repair prompt (valid JSON, wrong shape). Bounded by
    `max_generation_retries`.
Schema validity says nothing about technical correctness.
"""

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.exceptions import (
    CodeMindError,
    DeadlineExceededError,
    GenerationFailedError,
    RequestCancelledError,
)
from app.core.logging import get_logger
from app.schemas.errors import (
    AttemptKind,
    AttemptRecord,
    FailureCategory,
    RetryMetadata,
    RetryOutcome,
    ValidationFailure,
)
from app.schemas.requests import UserQuestion
from app.services.ollama_client import ChatMessage, ChatResult, LLMClient
from app.services.prompt_builder import PromptBuilder
from app.services.response_validator import ResponseValidationError, validate_response
from app.utils.timing import Deadline, Stopwatch

T = TypeVar("T", bound=BaseModel)
log = get_logger("codemind.retry")


@dataclass(frozen=True)
class TransportRetryPolicy:
    max_retries: int = 2
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay(self, retry_index: int, rand: Callable[[], float] = random.random) -> float:
        """Exponential backoff capped at max_delay, with equal jitter (50-100% of the cap)."""
        cap = min(self.max_delay, self.base_delay * (2**retry_index))
        return cap / 2 + rand() * cap / 2


@dataclass(frozen=True)
class GenerationParams:
    model: str
    temperature: float
    max_tokens: int
    call_timeout: float


class StructuredGenerator:
    def __init__(
        self,
        llm: LLMClient,
        prompts: PromptBuilder,
        *,
        max_generation_retries: int = 2,
        transport: TransportRetryPolicy | None = None,
        strict_schema: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.llm = llm
        self.prompts = prompts
        self.max_generation_retries = max_generation_retries
        self.transport = transport or TransportRetryPolicy()
        self.strict_schema = strict_schema
        self._sleep = sleep
        self._rand = rand

    # -- helpers -----------------------------------------------------------------------------
    @staticmethod
    def _check(deadline: Deadline, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise RequestCancelledError()
        if deadline.expired():
            raise DeadlineExceededError()

    def _call(
        self,
        messages: list[ChatMessage],
        schema: dict[str, Any],
        params: GenerationParams,
        deadline: Deadline,
        cancel: threading.Event | None,
        stats: dict[str, int],
        on_token: Callable[[str], None] | None = None,
    ) -> ChatResult:
        """One logical inference call with transport-level retries."""
        retry = 0
        while True:
            self._check(deadline, cancel)
            timeout = max(0.1, min(params.call_timeout, deadline.remaining()))
            try:
                return self.llm.chat(
                    model=params.model,
                    messages=messages,
                    response_schema=schema,
                    temperature=params.temperature,
                    max_tokens=params.max_tokens,
                    timeout=timeout,
                    on_token=on_token,
                )
            except CodeMindError as exc:
                if not exc.retryable or retry >= self.transport.max_retries:
                    raise
                delay = self.transport.delay(retry, self._rand)
                if delay >= deadline.remaining():
                    raise  # cannot afford to wait; fail with the real cause
                retry += 1
                stats["transport"] += 1
                stats["attempt_transport"] += 1
                log.warning(
                    "transport_retry",
                    extra={
                        "failure_category": exc.category,
                        "retry": retry,
                        "delay_s": round(delay, 2),
                    },
                )
                self._sleep(delay)

    # -- main --------------------------------------------------------------------------------
    def generate(
        self,
        question: UserQuestion,
        model_cls: type[T],
        params: GenerationParams,
        deadline: Deadline,
        cancel: threading.Event | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> tuple[T, RetryMetadata]:
        schema = model_cls.model_json_schema()
        max_attempts = 1 + self.max_generation_retries
        clock = Stopwatch()
        history: list[AttemptRecord] = []
        stats = {"transport": 0, "attempt_transport": 0}
        previous: str | None = None
        failure: ValidationFailure | None = None

        def metadata(outcome: RetryOutcome) -> RetryMetadata:
            return RetryMetadata(
                attempts=len(history),
                max_attempts=max_attempts,
                transport_retries=stats["transport"],
                outcome=outcome,
                elapsed_ms=clock.elapsed_ms(),
                history=history,
            )

        for attempt in range(1, max_attempts + 1):
            if attempt == 1 or previous is None or failure is None:
                kind = AttemptKind.GENERATE if attempt == 1 else AttemptKind.REGENERATE
                messages = self.prompts.initial(question, schema)
            elif failure.category is FailureCategory.SCHEMA_VIOLATION:
                kind = AttemptKind.REPAIR
                messages = self.prompts.repair(question, schema, previous, failure)
            else:
                kind = AttemptKind.REGENERATE
                messages = self.prompts.regenerate(question, schema, previous, failure)

            watch = Stopwatch()
            stats["attempt_transport"] = 0
            try:
                result = self._call(messages, schema, params, deadline, cancel, stats, on_token)
            except CodeMindError as exc:
                history.append(
                    AttemptRecord(
                        attempt=attempt,
                        kind=kind,
                        transport_retries=stats["attempt_transport"],
                        failure=exc.category,
                        elapsed_ms=watch.elapsed_ms(),
                    )
                )
                exc.retry = metadata(RetryOutcome.FAILED)
                raise

            try:
                response = validate_response(result.content, model_cls, strict=self.strict_schema)
            except ResponseValidationError as bad:
                failure, previous = bad.failure, result.content
                history.append(
                    AttemptRecord(
                        attempt=attempt,
                        kind=kind,
                        transport_retries=stats["attempt_transport"],
                        failure=failure.category,
                        elapsed_ms=watch.elapsed_ms(),
                    )
                )
                log.info(
                    "validation_failed",
                    extra={"attempt": attempt, "failure_category": failure.category},
                )
                continue

            history.append(
                AttemptRecord(
                    attempt=attempt,
                    kind=kind,
                    transport_retries=stats["attempt_transport"],
                    elapsed_ms=watch.elapsed_ms(),
                )
            )
            outcome = RetryOutcome.SUCCESS if attempt == 1 else RetryOutcome.RECOVERED
            return response, metadata(outcome)

        error = GenerationFailedError(
            f"no schema-valid response after {max_attempts} attempts "
            f"(last failure: {failure.category if failure else 'unknown'})"
        )
        error.retry = metadata(RetryOutcome.EXHAUSTED)
        raise error
