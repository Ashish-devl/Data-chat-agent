import pytest
from cryptography.fernet import Fernet

from datachat_agent.config import get_settings


@pytest.fixture(autouse=True)
def settings_env(monkeypatch: pytest.MonkeyPatch):
    """Isolate tests from the developer's own .env."""
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("LLM_MODEL", "test-model")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
