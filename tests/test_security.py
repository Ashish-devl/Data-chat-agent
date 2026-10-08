import pytest

from datachat_agent.config import Settings, get_settings
from datachat_agent.security import (
    LIVE_KEY_PREFIX,
    decrypt,
    encrypt,
    generate_api_key,
    hash_api_key,
)


def test_generated_key_matches_its_hash_and_prefix():
    key, prefix, key_hash = generate_api_key()
    assert key.startswith(LIVE_KEY_PREFIX)
    assert key.startswith(prefix)
    assert hash_api_key(key) == key_hash
    assert len(key_hash) == 64


def test_generated_keys_are_unique():
    assert generate_api_key()[0] != generate_api_key()[0]


def test_encrypt_round_trip_hides_plaintext():
    dsn = "postgresql://readonly:s3cret@host/erp"
    token = encrypt(dsn)
    assert "s3cret" not in token
    assert decrypt(token) == dsn


def test_encrypt_without_secret_key_fails_clearly(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="gen-secret"):
        encrypt("x")


@pytest.mark.parametrize(
    "url",
    ["postgres://u:p@h/db", "postgresql://u:p@h/db", "postgresql+asyncpg://u:p@h/db"],
)
def test_database_url_uses_async_driver(url):
    assert Settings(database_url=url).database_url == "postgresql+asyncpg://u:p@h/db"
