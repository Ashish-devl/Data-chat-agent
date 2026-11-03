"""Provider switch: Claude or any OpenAI-compatible endpoint.

Groq, Gemini, OpenRouter, Ollama and OpenAI all speak the OpenAI API.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

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
    return primary.with_fallbacks(fallbacks) if fallbacks else primary


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
    if "429" in text or "rate" in text or "quota" in text:
        return "The language model's rate limit or quota was reached. Please try again later."
    if "503" in text or "overloaded" in text or "high demand" in text or "unavailable" in text:
        return "The language model is overloaded right now. Please try again shortly."
    if "401" in text or "403" in text or "api key" in text or "permission" in text:
        return "The language model rejected the API key. Check LLM_API_KEY."
    if "404" in text or "not found" in text:
        return "The language model was not found. Check LLM_MODEL."
    return "The language model request failed. Please try again."
