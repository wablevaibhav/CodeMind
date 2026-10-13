"""FastAPI application factory."""

import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routes import chat, code_tools, health, learning, models
from app.core.config import Settings, get_settings
from app.core.exceptions import CodeMindError
from app.core.logging import Metrics, configure_logging, get_logger, request_id_var
from app.schemas.errors import ErrorDetail, ErrorResponse
from app.services.assistant_service import AssistantService, ModelSelector
from app.services.ollama_client import LLMClient, OllamaClient

log = get_logger("codemind.api")
STATIC_DIR = Path(__file__).resolve().parent / "static"


def build_service(
    settings: Settings, llm: LLMClient | None = None
) -> tuple[AssistantService, OllamaClient, ModelSelector, Metrics]:
    ollama = OllamaClient(
        settings.ollama_base_url,
        timeout=settings.ollama_timeout_seconds,
        num_ctx=settings.ollama_num_ctx,
        keep_alive=settings.ollama_keep_alive,
    )
    selector = ModelSelector(settings.ollama_model)
    metrics = Metrics()
    service = AssistantService(llm or ollama, settings, selector, metrics=metrics)
    return service, ollama, selector, metrics


def _warmup(ollama: OllamaClient, selector: ModelSelector) -> None:
    try:
        model = selector.get()
        if ollama.has_model(model):
            ollama.warmup(model)
            log.info("model_warmed_up", extra={"model": model})
    except Exception as exc:  # warm-up is best effort and must never affect startup
        log.info("warmup_skipped", extra={"exc_type": type(exc).__name__})


def _error(status: int, code: str, message: str, retry: object = None) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message),
        request_id=request_id_var.get(),
        retry=retry,  # type: ignore[arg-type]
    )
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    service, ollama, selector, metrics = build_service(settings, llm)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Nothing is ever downloaded at startup. If enabled, an installed model is merely
        # loaded into memory in the background so the first request is not slow.
        if settings.warmup_on_start and llm is None:
            threading.Thread(target=_warmup, args=(ollama, selector), daemon=True).start()
        yield
        ollama.close()

    app = FastAPI(
        title="CodeMind",
        version=__version__,
        description="Local-first AI coding assistant. Inference runs on your machine via Ollama; "
        "every successful response is validated against a Pydantic/JSON Schema contract.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.service = service
    app.state.ollama = ollama
    app.state.selector = selector
    app.state.metrics = metrics

    @app.middleware("http")
    async def correlation_id(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(CodeMindError)
    async def handle_app_error(_: Request, exc: CodeMindError) -> JSONResponse:
        return _error(exc.http_status, exc.code, exc.public_message, exc.retry)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Locations and messages only; never echo submitted values (they may be private code).
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}"
            for e in exc.errors()[:10]
        )
        return _error(422, "invalid_request", f"Invalid request: {problems}"[:1000])

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(
            exc.status_code,
            code,
            "Resource not found." if exc.status_code == 404 else "Request could not be processed.",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", extra={"exc_type": type(exc).__name__})
        return _error(500, "internal_error", "An internal error occurred.")

    @app.get("/", include_in_schema=False)
    def ui() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-cache",  # always revalidate so updates show immediately
            },
        )

    for module in (health, models, chat, code_tools, learning):
        app.include_router(module.router)
    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn app.main:app_factory --factory`."""
    return create_app()
