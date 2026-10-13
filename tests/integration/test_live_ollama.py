"""Optional tests against a real local Ollama. Run with:  pytest -m integration

They are skipped automatically when Ollama or the configured model is unavailable.
"""

import pytest

from app.core.config import get_settings
from app.main import build_service
from app.schemas.requests import Mode, UserQuestion

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live():
    settings = get_settings()
    service, ollama, _, _ = build_service(settings)
    if not ollama.is_reachable():
        pytest.skip("Ollama is not reachable")
    if not ollama.has_model(settings.ollama_model):
        pytest.skip(f"model {settings.ollama_model} is not installed")
    yield service, ollama
    ollama.close()


def test_structured_answer_is_schema_valid(live):
    service, _ = live
    result = service.answer(
        UserQuestion(question="In Python, how do I read a file line by line?", mode=Mode.ASK)
    )
    assert result.schema_valid
    assert result.response.answer
    assert result.retry.attempts <= 1 + service.settings.max_generation_retries


def test_models_listing(live):
    _, ollama = live
    assert ollama.list_models()
