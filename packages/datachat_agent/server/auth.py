import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.security import Scope, hash_api_key
from datachat_agent.server.db import get_session
from datachat_agent.server.limits import enforce_rate_limit
from datachat_agent.server.models import ApiKey, Tenant

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    key_id: uuid.UUID
    scopes: frozenset[str]
    monthly_token_limit: int | None = None


async def authenticate(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
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
    await enforce_rate_limit(key.id, per_minute)
    key.last_used_at = func.now()
    await session.commit()
    return Principal(
        tenant_id=key.tenant_id,
        key_id=key.id,
        scopes=frozenset(key.scopes),
        monthly_token_limit=monthly_tokens,
    )


def require_scope(scope: Scope) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Principal = Depends(authenticate)) -> Principal:
        if Scope.ADMIN.value not in principal.scopes and scope.value not in principal.scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"API key lacks the '{scope.value}' scope"
            )
        return principal

    return dependency
