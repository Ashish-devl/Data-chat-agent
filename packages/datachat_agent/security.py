import hashlib
import secrets
from enum import StrEnum

from cryptography.fernet import Fernet

from datachat_agent.config import get_settings

LIVE_KEY_PREFIX = "dc_live_"


class Scope(StrEnum):
    ASK = "ask"
    DOCS = "docs"
    ADMIN = "admin"  # implies every other scope


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Return (full key, display prefix, SHA-256 hash). Only the hash is stored."""
    key = LIVE_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[: len(LIVE_KEY_PREFIX) + 4], hash_api_key(key)


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise RuntimeError("SECRET_KEY is not set. Generate one with: datachat-agent gen-secret")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
