from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import __version__
from app.api.deps import MetricsDep, OllamaDep, SelectorDep
from app.core.exceptions import CodeMindError
from app.schemas.responses import HealthResponse, OllamaHealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Application health")
def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@router.get(
    "/health/ollama",
    response_model=OllamaHealthResponse,
    summary="Ollama connectivity and selected-model availability",
    responses={503: {"model": OllamaHealthResponse, "description": "Ollama unreachable"}},
)
def health_ollama(ollama: OllamaDep, selector: SelectorDep):  # noqa: ANN201
    model = selector.get()
    try:
        version = ollama.version()
        installed = ollama.has_model(model)
        body = OllamaHealthResponse(
            reachable=True,
            base_url=ollama.base_url,
            ollama_version=version,
            selected_model=model,
            model_installed=installed,
            structured_output_supported=ollama.supports_structured_output(),
            detail=None if installed else f"Model not installed. Run: ollama pull {model}",
        )
        return body
    except CodeMindError as exc:
        body = OllamaHealthResponse(
            reachable=False,
            base_url=ollama.base_url,
            selected_model=model,
            detail=exc.public_message,
        )
        return JSONResponse(status_code=503, content=body.model_dump(mode="json"))


@router.get("/metrics", summary="Operational counters (no user content)")
def metrics(metrics: MetricsDep) -> dict:
    return metrics.snapshot()
