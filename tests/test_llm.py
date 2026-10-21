import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda

from datachat_agent.config import get_settings
from datachat_agent.core.llm import LLMUnavailableError, describe_llm_error, get_chat_model
from datachat_agent.sql.generator import SQLRequest, generate_sql

REQ = SQLRequest(
    question="How many buyers?", schema_text="TABLE public.buyers (id int)", dialect="postgresql"
)


def failing(exc: Exception) -> RunnableLambda:
    def _raise(_):
        raise exc

    return RunnableLambda(_raise)


def test_client_gets_timeout_and_retries(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
    get_settings.cache_clear()
    model = get_chat_model()
    assert model.request_timeout == 12 and model.max_retries == 0


def test_fallback_models_are_configured_in_order(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "main")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", " backup-1, ,backup-2 ")
    get_settings.cache_clear()
    chain = get_chat_model()
    assert chain.runnable.model_name == "main"
    assert [f.model_name for f in chain.fallbacks] == ["backup-1", "backup-2"]


def test_no_fallbacks_gives_plain_model(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "")
    get_settings.cache_clear()
    assert get_chat_model().model_name == "test-model"


def test_fallback_answers_when_primary_fails():
    llm = failing(TimeoutError("timed out")).with_fallbacks(
        [FakeListChatModel(responses=["```sql\nSELECT COUNT(*) FROM public.buyers\n```"])]
    )
    result = asyncio.run(generate_sql(llm, REQ))
    assert result.sql == "SELECT COUNT(*) FROM public.buyers"


def test_model_failure_becomes_clear_error():
    with pytest.raises(LLMUnavailableError, match="did not respond in time"):
        asyncio.run(generate_sql(failing(TimeoutError("Request timed out.")), REQ))


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Request timed out.", "did not respond in time"),
        ("Error code: 429 - rate limit", "rate limit"),
        ("Error code: 503 - high demand", "overloaded"),
        ("Error code: 401 - invalid api key", "rejected the API key"),
        ("Error code: 404 - model not found", "Check LLM_MODEL"),
        ("something odd", "request failed"),
    ],
)
def test_error_messages(message, expected):
    assert expected in describe_llm_error(RuntimeError(message))


def test_error_message_never_contains_the_key():
    key = "AQ.secret-key-value"
    assert key not in describe_llm_error(RuntimeError(f"401 bad key {key}"))
