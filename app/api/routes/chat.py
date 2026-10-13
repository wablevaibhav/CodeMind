from fastapi import APIRouter

from app.api.deps import ERROR_RESPONSES, ServiceDep
from app.api.streaming import sse_response
from app.schemas.requests import ChatRequest
from app.schemas.responses import AssistantResult
from app.services import learning_service as build

router = APIRouter(tags=["assistant"], responses=ERROR_RESPONSES)


@router.post("/chat", response_model=AssistantResult, summary="Answer a coding question")
def chat(body: ChatRequest, service: ServiceDep) -> AssistantResult:
    return service.answer(build.from_chat(body))


@router.post(
    "/chat/stream",
    summary="Same as /chat, streamed as server-sent events (live text, then the validated result)",
    response_description="text/event-stream of delta / result / error events",
)
def chat_stream(body: ChatRequest, service: ServiceDep):  # noqa: ANN201
    question = build.from_chat(body)
    return sse_response(
        lambda on_partial, cancel: service.answer(question, cancel=cancel, on_partial=on_partial)
    )
