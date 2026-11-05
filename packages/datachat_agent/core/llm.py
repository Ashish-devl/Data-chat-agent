"""Provider switch: Claude or any OpenAI-compatible endpoint.

Groq, Gemini, OpenRouter, Ollama and OpenAI all speak the OpenAI API.
"""

import re
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableConfig

from datachat_agent.config import Settings, get_settings


class LLMUnavailableError(RuntimeError):
    """The model timed out, was overloaded or refused the request. Safe to show to users."""


def _build(s: Settings, model: str, reasoning: bool = True) -> BaseChatModel:
    if s.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            api_key=s.llm_api_key,
            temperature=0,
            timeout=s.llm_timeout_seconds,
            max_retries=s.llm_max_retries,
        )

    from langchain_openai import ChatOpenAI

    # Local servers such as Ollama accept any key, but the client requires one.
    return ChatOpenAI(
        model=model,
        base_url=s.llm_base_url,
        api_key=s.llm_api_key or "not-needed",
        temperature=0,
        timeout=s.llm_timeout_seconds,
        max_retries=s.llm_max_retries,
        stream_usage=True,
        **(
            {"reasoning_effort": s.llm_reasoning_effort}
            if reasoning and s.llm_reasoning_effort
            else {}
        ),
    )


def get_chat_model(settings: Settings | None = None) -> BaseChatModel | Runnable:
    """The configured model, falling back to LLM_FALLBACK_MODELS when it fails."""
    s = settings or get_settings()
    if not s.llm_model:
        raise RuntimeError("LLM_MODEL is not set. Copy .env.example to .env and pick a provider.")
    primary = _build(s, s.llm_model)
    # Backups exist to keep answers flowing, so they use plain defaults: some models reject
    # reasoning_effort (e.g. Gemini 3.5 Flash Lite answers 400 to "none").
    fallbacks = [_build(s, m, reasoning=False) for m in s.fallback_models]
    return ModelChain(primary, fallbacks) if fallbacks else primary


# Models that answered "quota used up", and until when (time.monotonic()) to leave them alone.
# Shared by every chain in the process: one refusal spares all later requests the round trip.
_resting: dict[tuple[str, str], float] = {}

_RETRY_IN = re.compile(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.IGNORECASE)
_RETRY_DELAY = re.compile(r"retryDelay'?\"?:\s*'?\"?(\d+)s")


def quota_rest_seconds(e: Exception) -> float | None:
    """How long to skip a model after this error, or None when the error is not about quota.

    A daily quota is skipped until the provider says it resets (at most a day); a per-minute
    limit for a minute.
    """
    text = str(e)
    lower = text.lower()
    if "429" not in text and "quota" not in lower and "rate limit" not in lower:
        return None
    wait = None
    if m := _RETRY_DELAY.search(text):
        wait = float(m.group(1))
    elif (m := _RETRY_IN.search(text)) and any(m.groups()):
        h, mins, secs = (float(x) if x else 0.0 for x in m.groups())
        wait = h * 3600 + mins * 60 + secs
    daily = "per day" in lower or "perday" in lower
    if wait is None:
        wait = 3600.0 if daily else 60.0
    return min(max(wait, 1.0), 86400.0)


def _key(model: Any) -> tuple[str, str]:
    return (
        str(getattr(model, "openai_api_base", "") or getattr(model, "anthropic_api_url", "")),
        str(getattr(model, "model_name", "") or getattr(model, "model", "") or id(model)),
    )


class ModelChain(Runnable):
    """The main model with backups, skipping any model whose quota has run out.

    LangChain's with_fallbacks asks every model in turn on every call, so once the main model's
    free daily quota is spent each request first waits for a refusal (twice, with a retry).
    This chain remembers the refusal until the quota resets and goes straight to a model that
    still works. Streaming switches models only before the first chunk.
    """

    def __init__(self, runnable: Runnable, fallbacks: list[Runnable]) -> None:
        self.runnable = runnable
        self.fallbacks = list(fallbacks)

    @property
    def models(self) -> list[Runnable]:
        return [self.runnable, *self.fallbacks]

    def _order(self) -> list[Runnable]:
        now = time.monotonic()
        ready = [m for m in self.models if _resting.get(_key(m), 0) <= now]
        return ready or self.models  # all resting: try anyway, a quota may have come back

    @staticmethod
    def _failed(model: Runnable, e: Exception) -> None:
        if (rest := quota_rest_seconds(e)) is not None:
            _resting[_key(model)] = time.monotonic() + rest

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        error: Exception | None = None
        for model in self._order():
            try:
                return model.invoke(input, config, **kwargs)
            except Exception as e:  # noqa: BLE001 - try the next model
                self._failed(model, e)
                error = e
        raise error  # type: ignore[misc]

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        error: Exception | None = None
        for model in self._order():
            try:
                return await model.ainvoke(input, config, **kwargs)
            except Exception as e:  # noqa: BLE001 - try the next model
                self._failed(model, e)
                error = e
        raise error  # type: ignore[misc]

    def stream(
        self, input: Any, config: RunnableConfig | None = None, **kwargs: Any
    ) -> Iterator[Any]:
        error: Exception | None = None
        for model in self._order():
            started = False
            try:
                for chunk in model.stream(input, config, **kwargs):
                    started = True
                    yield chunk
                return
            except Exception as e:  # noqa: BLE001 - switch models only before any output
                self._failed(model, e)
                if started:
                    raise
                error = e
        raise error  # type: ignore[misc]

    async def astream(
        self, input: Any, config: RunnableConfig | None = None, **kwargs: Any
    ) -> AsyncIterator[Any]:
        error: Exception | None = None
        for model in self._order():
            started = False
            try:
                async for chunk in model.astream(input, config, **kwargs):
                    started = True
                    yield chunk
                return
            except Exception as e:  # noqa: BLE001 - switch models only before any output
                self._failed(model, e)
                if started:
                    raise
                error = e
        raise error  # type: ignore[misc]


class StreamUsage:
    """Token count for a streamed reply.

    OpenAI-compatible providers report a running total (Gemini on every chunk, OpenAI on the
    last one), so the last total is the answer. Anthropic reports input and output in separate
    chunks, so those are added up.
    """

    def __init__(self, provider: str) -> None:
        self._add = provider == "anthropic"
        self.total = 0

    def update(self, usage: dict | None) -> None:
        if not usage:
            return
        value = int(usage.get("total_tokens", 0))
        self.total = self.total + value if self._add else max(self.total, value)


def describe_llm_error(e: Exception) -> str:
    """A short, key-free explanation of why a model call failed."""
    text = f"{type(e).__name__}: {e}".lower()
    if "timeout" in text or "timed out" in text:
        return "The language model did not respond in time. Please try again."
    if "per day" in text or "perday" in text:
        return "The language model's daily quota is used up. Please try again tomorrow."
    if "429" in text or "rate" in text or "quota" in text:
        return "The language model's rate limit was reached. Please try again in a minute."
    if "503" in text or "overloaded" in text or "high demand" in text or "unavailable" in text:
        return "The language model is overloaded right now. Please try again shortly."
    if "401" in text or "403" in text or "api key" in text or "permission" in text:
        return "The language model rejected the API key. Check LLM_API_KEY."
    if "404" in text or "not found" in text:
        return "The language model was not found. Check LLM_MODEL."
    return "The language model request failed. Please try again."
