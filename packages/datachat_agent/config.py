from functools import lru_cache
from pathlib import Path
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
    # Free tiers can be slow or overloaded; never let one call hang a request.
    llm_timeout_seconds: float = 30
    llm_max_retries: int = 1
    # Comma-separated models to try, in order, when LLM_MODEL fails (same provider and key).
    llm_fallback_models: str = ""

    # "local" runs on CPU with no key; "hash" is a tiny fake for tests only.
    embed_provider: Literal["local", "openai_compatible", "hash"] = "local"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    embed_base_url: str | None = None
    embed_api_key: str = ""
    models_cache_dir: Path = Path.home() / ".cache" / "datachat-agent" / "models"

    # Documents and background work. sync: process inside the upload request (tests, library use);
    # inline: in the background of the API process; arq: a separate worker via Redis.
    max_pdf_mb: int = 20
    jobs_backend: Literal["sync", "inline", "arq"] = "inline"

    # Guards on every query against a customer database.
    sql_timeout_seconds: int = 10
    sql_max_rows: int = 500
    sql_max_response_bytes: int = 5_000_000

    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, v: str) -> str:
        # Railway and most hosts hand out postgres:// or postgresql:// URLs.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix) :]
        return v

    @field_validator("llm_base_url", "embed_base_url")
    @classmethod
    def empty_is_none(cls, v: str | None) -> str | None:
        return v or None

    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.llm_fallback_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
