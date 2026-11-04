import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.security import SESSION_PREFIX, Scope, domain_allowed, hash_api_key
from datachat_agent.server.db import get_session
from datachat_agent.server.limits import enforce_rate_limit
from datachat_agent.server.models import ApiKey, ConsoleSession, Tenant, User

_bearer = HTTPBearer(auto_error=False)


ROLE_SCOPES = {"owner": [Scope.ADMIN.value], "member": [Scope.ASK.value, Scope.DOCS.value]}


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    key_id: uuid.UUID | None  # None when signed in to the console instead of using a key
    scopes: frozenset[str]
    monthly_token_limit: int | None = None
    publishable: bool = False
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None


async def authenticate(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if creds.credentials.startswith(SESSION_PREFIX):
        return await _from_console_session(creds.credentials, session)
    row = (
        await session.execute(
            select(ApiKey, Tenant.rate_limit_per_minute, Tenant.monthly_token_limit)
            .join(Tenant, Tenant.id == ApiKey.tenant_id)
            .where(ApiKey.key_hash == hash_api_key(creds.credentials))
        )
    ).first()
    if row is None or row[0].revoked:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key, per_minute, monthly_tokens = row
    publishable = key.key_type == "publishable"
    if publishable:
        # Browsers set Origin themselves; a page on another website cannot use this key.
        origin = request.headers.get("origin")
        host = urlparse(origin).hostname if origin else None
        if not domain_allowed(host, list(key.allowed_domains or [])):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "This publishable key is not allowed on this website.",
            )
    await enforce_rate_limit(key.id, per_minute)
    key.last_used_at = func.now()
    await session.commit()
    return Principal(
        tenant_id=key.tenant_id,
        key_id=key.id,
        scopes=frozenset(key.scopes),
        monthly_token_limit=monthly_tokens,
        publishable=publishable,
    )


async def _from_console_session(token: str, session: AsyncSession) -> Principal:
    row = (
        await session.execute(
            select(ConsoleSession, User, Tenant.rate_limit_per_minute, Tenant.monthly_token_limit)
            .join(User, User.id == ConsoleSession.user_id)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(ConsoleSession.token_hash == hash_api_key(token))
        )
    ).first()
    if row is None:
        raise _unauthorized("Invalid or expired session")
    console_session, user, per_minute, monthly = row
    if console_session.revoked or user.disabled or console_session.expires_at <= datetime.now(UTC):
        raise _unauthorized("Invalid or expired session")
    await enforce_rate_limit(user.id, per_minute)
    return Principal(
        tenant_id=user.tenant_id,
        key_id=None,
        scopes=frozenset(ROLE_SCOPES.get(user.role, [])),
        monthly_token_limit=monthly,
        user_id=user.id,
        session_id=console_session.id,
    )


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, message, headers={"WWW-Authenticate": "Bearer"}
    )


async def secret_key_only(principal: Principal) -> None:
    if principal.publishable:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Publishable keys cannot use this endpoint.")


def require_scope(scope: Scope) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Principal = Depends(authenticate)) -> Principal:
        if Scope.ADMIN.value not in principal.scopes and scope.value not in principal.scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"API key lacks the '{scope.value}' scope"
            )
        return principal

    return dependency
