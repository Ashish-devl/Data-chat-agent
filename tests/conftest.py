import os

import pytest
from cryptography.fernet import Fernet

# Set before anything reads settings, so session-wide fixtures see the same values.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://datachat:datachat@localhost:5432/datachat_test"
)
os.environ.update(
    {
        "DATABASE_URL": TEST_DATABASE_URL,
        "SECRET_KEY": Fernet.generate_key().decode(),
        "LLM_MODEL": "test-model",
        "EMBED_PROVIDER": "hash",  # no model download in tests
    }
)

from datachat_agent.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
