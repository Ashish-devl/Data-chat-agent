from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://datachat:datachat@localhost:5432/datachat"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = ""

    # Bring your own key: the user's provider, never the author's.
    llm_provider: Literal["openai_compatible", "anthropic"] = "openai_compatible"
    llm_base_url: str | None = None
    llm_api_key: str = ""
    llm_model: str = ""

    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, v: str) -> str:
        # Railway and most hosts hand out postgres:// or postgresql:// URLs.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix):]
        return v

    @field_validator("llm_base_url")
    @classmethod
    def empty_is_none(cls, v: str | None) -> str | None:
        return v or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
