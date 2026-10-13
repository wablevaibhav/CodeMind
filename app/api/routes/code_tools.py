from fastapi import APIRouter

from app.api.deps import ERROR_RESPONSES, ServiceDep
from app.schemas.requests import DebugRequest, ExplainRequest, GenerateRequest, ReviewRequest
from app.schemas.responses import AssistantResult
from app.services import learning_service as build

router = APIRouter(tags=["code tools"], responses=ERROR_RESPONSES)


@router.post("/debug", response_model=AssistantResult, summary="Analyze an error or code issue")
def debug(body: DebugRequest, service: ServiceDep) -> AssistantResult:
    return service.answer(build.from_debug(body))


@router.post("/explain", response_model=AssistantResult, summary="Explain user-provided code")
def explain(body: ExplainRequest, service: ServiceDep) -> AssistantResult:
    return service.answer(build.from_explain(body))


@router.post("/generate", response_model=AssistantResult, summary="Generate code")
def generate(body: GenerateRequest, service: ServiceDep) -> AssistantResult:
    return service.answer(build.from_generate(body))


@router.post("/review", response_model=AssistantResult, summary="Review user-provided code")
def review(body: ReviewRequest, service: ServiceDep) -> AssistantResult:
    return service.answer(build.from_review(body))
