"""Structured JSON logging, request correlation IDs and lightweight in-process metrics.

Prompts, code and raw model output are never logged.
"""

import json
import logging
import threading
from collections import Counter
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger("codemind")
    root.setLevel(level)
    if not any(getattr(h, "_codemind", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._codemind = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.propagate = False


def get_logger(name: str = "codemind") -> logging.Logger:
    return logging.getLogger(name)


class Metrics:
    """Counters only; safe to expose. Contains no user content."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter[str] = Counter()
        self._latency_total_ms = 0
        self._requests = 0

    def record(self, *, mode: str, outcome: str, failure: str | None, elapsed_ms: int) -> None:
        with self._lock:
            self._requests += 1
            self._latency_total_ms += elapsed_ms
            self._counts[f"outcome.{outcome}"] += 1
            self._counts[f"mode.{mode}"] += 1
            if failure:
                self._counts[f"failure.{failure}"] += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            avg = self._latency_total_ms / self._requests if self._requests else 0.0
            return {
                "requests_total": self._requests,
                "avg_latency_ms": round(avg, 1),
                "counters": dict(sorted(self._counts.items())),
            }
