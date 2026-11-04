import hashlib
import secrets
from enum import StrEnum

from cryptography.fernet import Fernet

from datachat_agent.config import get_settings

LIVE_KEY_PREFIX = "dc_live_"
PUBLISHABLE_KEY_PREFIX = "dc_pub_"


class Scope(StrEnum):
    ASK = "ask"
    DOCS = "docs"
    ADMIN = "admin"  # implies every other scope


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key(publishable: bool = False) -> tuple[str, str, str]:
    """Return (full key, display prefix, SHA-256 hash). Only the hash is stored."""
    head = PUBLISHABLE_KEY_PREFIX if publishable else LIVE_KEY_PREFIX
    key = head + secrets.token_urlsafe(32)
    return key, key[: len(head) + 4], hash_api_key(key)


SESSION_PREFIX = "dc_sess_"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    """scrypt with a random salt, stored as scrypt$n$r$p$salt$hash (hex)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        check = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(check.hex(), digest)


# Checked against when the email is unknown, so a wrong email takes as long as a wrong password.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Use at least 10 characters."
    if password.lower() == password or password.upper() == password:
        return "Mix upper- and lower-case letters."
    if not any(c.isdigit() for c in password):
        return "Include at least one digit."
    return None


def generate_session_token() -> tuple[str, str]:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    return token, hash_api_key(token)


def normalize_domain(value: str) -> str:
    """'https://Shop.Example.com/x' -> 'shop.example.com'; '*.example.com' stays a wildcard."""
    v = value.strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    return v.split("/", 1)[0].split(":", 1)[0].rstrip(".")


def domain_allowed(host: str | None, allowed: list[str]) -> bool:
    """Exact host, or '*.example.com' for any subdomain (not example.com itself)."""
    if not host:
        return False
    host = host.lower().rstrip(".")
    for pattern in allowed:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and host != pattern[2:]:
                return True
        elif host == pattern:
            return True
    return False


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise RuntimeError("SECRET_KEY is not set. Generate one with: datachat-agent gen-secret")
    try:
        return Fernet(key.encode())
    except ValueError:
        raise RuntimeError(
            "SECRET_KEY is not a valid key. Generate one with: datachat-agent gen-secret"
        ) from None


def check_secret_key() -> None:
    """Raise a clear error at startup instead of failing on the first stored password."""
    _fernet()


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
