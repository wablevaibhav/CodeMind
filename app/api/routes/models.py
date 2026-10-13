from fastapi import APIRouter

from app.api.deps import ERROR_RESPONSES, OllamaDep, SelectorDep
from app.core.exceptions import ModelNotFoundError
from app.schemas.requests import SelectModelRequest
from app.schemas.responses import ModelsResponse

router = APIRouter(tags=["models"], responses=ERROR_RESPONSES)


def _build(ollama: OllamaDep, selector: SelectorDep) -> ModelsResponse:
    models = ollama.list_models()
    selected = selector.get()
    return ModelsResponse(
        models=models,
        selected_model=selected,
        selected_model_installed=any(
            m.name == selected or m.name == f"{selected}:latest" for m in models
        ),
    )


@router.get("/models", response_model=ModelsResponse, summary="Locally installed Ollama models")
def list_models(ollama: OllamaDep, selector: SelectorDep) -> ModelsResponse:
    return _build(ollama, selector)


@router.post(
    "/models/select",
    response_model=ModelsResponse,
    summary="Select the default model (must already be installed; never pulls)",
)
def select_model(
    body: SelectModelRequest, ollama: OllamaDep, selector: SelectorDep
) -> ModelsResponse:
    if not ollama.has_model(body.model):
        raise ModelNotFoundError(f"model {body.model!r} is not installed")
    selector.set(body.model)
    return _build(ollama, selector)
