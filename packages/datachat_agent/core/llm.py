"""Provider switch: Claude or any OpenAI-compatible endpoint.

Groq, Gemini, OpenRouter, Ollama and OpenAI all speak the OpenAI API.
"""

from langchain_core.language_models import BaseChatModel

from datachat_agent.config import Settings, get_settings


def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    s = settings or get_settings()
    if not s.llm_model:
        raise RuntimeError("LLM_MODEL is not set. Copy .env.example to .env and pick a provider.")

    if s.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=s.llm_model, api_key=s.llm_api_key, temperature=0)

    from langchain_openai import ChatOpenAI

    # Local servers such as Ollama accept any key, but the client requires one.
    return ChatOpenAI(
        model=s.llm_model,
        base_url=s.llm_base_url,
        api_key=s.llm_api_key or "not-needed",
        temperature=0,
    )
