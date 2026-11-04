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


def test_stream_usage_takes_running_total_for_openai_style():
    from datachat_agent.core.llm import StreamUsage

    u = StreamUsage("openai_compatible")
    for total in (407, 431, 433, 433, None):
        u.update({"total_tokens": total} if total else None)
    assert u.total == 433


def test_stream_usage_adds_parts_for_anthropic():
    from datachat_agent.core.llm import StreamUsage

    u = StreamUsage("anthropic")
    u.update({"total_tokens": 120})  # input, reported at the start
    u.update({"total_tokens": 35})  # output, reported at the end
    assert u.total == 155


def test_backup_models_do_not_get_reasoning_effort(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "main")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "backup")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "none")
    get_settings.cache_clear()
    chain = get_chat_model()
    assert chain.runnable.reasoning_effort == "none"
    assert chain.fallbacks[0].reasoning_effort is None


# --- quota-aware fallback chain -------------------------------------------------------------

DAILY_429 = (
    "Error code: 429 - [{'error': {'code': 429, 'message': 'You exceeded your current quota. "
    "* Quota exceeded for metric: generate_content_free_tier_requests, limit: 20, model: m, "
    "description: Generate content requests per day limit exceeded on Free Tier. "
    "Please retry in 22h49m14.659s.', 'details': [{'quotaId': "
    "'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}, {'retryDelay': '82154s'}]}}]"
)


class Counting(FakeListChatModel):
    """A fake model that can be told to fail, and counts its calls."""

    fail_with: str | None = None
    calls: int = 0
    model_name: str = "fake"

    def _call(self, *args, **kwargs):
        self.calls += 1
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        return super()._call(*args, **kwargs)

    async def _astream(self, *args, **kwargs):
        self.calls += 1
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        async for chunk in super()._astream(*args, **kwargs):
            yield chunk


@pytest.fixture
def chain():
    from datachat_agent.core import llm as L

    L._resting.clear()
    main = Counting(responses=["main"], fail_with=DAILY_429, model_name="main")
    backup = Counting(responses=["backup"], model_name="backup")
    yield L.ModelChain(main, [backup]), main, backup
    L._resting.clear()


def test_quota_rest_reads_the_providers_delay():
    from datachat_agent.core.llm import quota_rest_seconds

    assert quota_rest_seconds(RuntimeError(DAILY_429)) == 82154
    assert quota_rest_seconds(RuntimeError("429 rate limit, retry in 1m30s")) == 90
    assert quota_rest_seconds(RuntimeError("Error code: 429 - Too many requests")) == 60
    assert quota_rest_seconds(RuntimeError("Error code: 503 - overloaded")) is None
    assert quota_rest_seconds(RuntimeError("Request timed out.")) is None


def test_a_spent_model_is_skipped_until_its_quota_resets(chain):
    llm, main, backup = chain
    assert asyncio.run(llm.ainvoke("hi")).content == "backup"
    assert asyncio.run(llm.ainvoke("hi")).content == "backup"
    assert llm.invoke("hi").content == "backup"
    assert main.calls == 1  # asked once, then left alone
    assert backup.calls == 3


def test_other_errors_do_not_bench_a_model(chain):
    llm, main, backup = chain
    main.fail_with = "Error code: 503 - overloaded"
    llm.invoke("hi")
    llm.invoke("hi")
    assert main.calls == 2


def test_a_rested_model_is_used_again(chain):
    from datachat_agent.core import llm as L

    llm, main, backup = chain
    llm.invoke("hi")
    main.fail_with = None
    L._resting.clear()  # the quota reset
    assert llm.invoke("hi").content == "main"


def test_when_every_model_is_resting_they_are_still_tried(chain):
    llm, main, backup = chain
    backup.fail_with = DAILY_429
    with pytest.raises(RuntimeError):
        llm.invoke("hi")
    backup.fail_with = None
    assert llm.invoke("hi").content == "backup"


def test_streaming_switches_models_before_the_first_chunk(chain):
    llm, main, backup = chain

    async def collect():
        return "".join([c.content async for c in llm.astream("hi")])

    assert asyncio.run(collect()) == "backup"
