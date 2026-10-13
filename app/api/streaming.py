"""Server-sent-events bridge: live answer text first, then the fully validated result."""

import contextvars
import json
import queue
import threading
from collections.abc import Callable, Iterator

from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.exceptions import CodeMindError
from app.core.logging import get_logger, request_id_var
from app.schemas.errors import ErrorDetail, ErrorResponse

log = get_logger("codemind.stream")
_DONE = object()


def _event(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def sse_response(
    run: Callable[[Callable[[str], None], threading.Event], BaseModel],
) -> StreamingResponse:
    """Run `run(on_partial, cancel)` in a worker thread and stream its progress.

    Events (one JSON object per `data:` line):
      {"type": "delta",  "text": "<answer so far>"}   display only, replaces previous text
      {"type": "result", "data": <AssistantResult>}   validated; this is the real response
      {"type": "error",  "status": int, "data": <ErrorResponse>}
    Closing the connection cancels the generation.
    """
    events: queue.Queue[object] = queue.Queue()
    cancel = threading.Event()

    def worker() -> None:
        try:
            result = run(lambda text: events.put({"type": "delta", "text": text}), cancel)
            events.put({"type": "result", "data": result.model_dump(mode="json")})
        except CodeMindError as exc:
            body = ErrorResponse(
                error=ErrorDetail(code=exc.code, message=exc.public_message),
                request_id=request_id_var.get(),
                retry=exc.retry,
            )
            events.put(
                {"type": "error", "status": exc.http_status, "data": body.model_dump(mode="json")}
            )
        except Exception as exc:  # never leak internals to the client
            log.error("stream_error", extra={"exc_type": type(exc).__name__})
            body = ErrorResponse(
                error=ErrorDetail(code="internal_error", message="An internal error occurred."),
                request_id=request_id_var.get(),
            )
            events.put({"type": "error", "status": 500, "data": body.model_dump(mode="json")})
        finally:
            events.put(_DONE)

    ctx = contextvars.copy_context()  # keeps the request/correlation ID in the worker
    threading.Thread(target=ctx.run, args=(worker,), daemon=True).start()

    def stream() -> Iterator[str]:
        try:
            while True:
                try:
                    item = events.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if item is _DONE:
                    return
                yield _event(item)  # type: ignore[arg-type]
        finally:
            cancel.set()  # client went away (or finished): stop any in-flight generation

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
