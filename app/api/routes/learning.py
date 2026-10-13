from fastapi import APIRouter

from app.api.deps import ERROR_RESPONSES, ServiceDep
from app.api.streaming import sse_response
from app.schemas.requests import LearnRequest
from app.schemas.responses import LearningResult
from app.services import learning_service as build

router = APIRouter(tags=["learning"], responses=ERROR_RESPONSES)


@router.post(
    "/learn",
    response_model=LearningResult,
    summary="Structured lesson, exercises and quiz (AI-generated, unverified)",
)
def learn(body: LearnRequest, service: ServiceDep) -> LearningResult:
    return service.answer_learning(build.from_learn(body))


@router.post("/learn/stream", summary="Same as /learn, streamed as server-sent events")
def learn_stream(body: LearnRequest, service: ServiceDep):  # noqa: ANN201
    question = build.from_learn(body)
    return sse_response(
        lambda on_partial, cancel: service.answer_learning(
            question, cancel=cancel, on_partial=on_partial
        )
    )
