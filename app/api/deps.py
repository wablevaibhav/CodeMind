from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings
from app.core.logging import Metrics
from app.schemas.errors import ErrorResponse
from app.services.assistant_service import AssistantService, ModelSelector
from app.services.ollama_client import OllamaClient

# Documented on every assistant endpoint.
ERROR_RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "Model not installed"},
    422: {"model": ErrorResponse, "description": "Invalid request payload"},
    502: {"model": ErrorResponse, "description": "Model failed or produced no valid response"},
    503: {"model": ErrorResponse, "description": "Ollama unreachable"},
    504: {"model": ErrorResponse, "description": "Model timed out / request deadline exceeded"},
}


def _state(request: Request, name: str):  # noqa: ANN202
    return getattr(request.app.state, name)


def get_service(request: Request) -> AssistantService:
    return _state(request, "service")


def get_settings_dep(request: Request) -> Settings:
    return _state(request, "settings")


def get_ollama(request: Request) -> OllamaClient:
    return _state(request, "ollama")


def get_selector(request: Request) -> ModelSelector:
    return _state(request, "selector")


def get_metrics(request: Request) -> Metrics:
    return _state(request, "metrics")


ServiceDep = Annotated[AssistantService, Depends(get_service)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
OllamaDep = Annotated[OllamaClient, Depends(get_ollama)]
SelectorDep = Annotated[ModelSelector, Depends(get_selector)]
MetricsDep = Annotated[Metrics, Depends(get_metrics)]
