import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.security import Scope, generate_api_key, normalize_domain
from datachat_agent.server.auth import Principal, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.models import ApiKey

router = APIRouter(prefix="/keys", tags=["keys"])
admin = require_scope(Scope.ADMIN)


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Scope] = Field(default=[Scope.ASK], min_length=1)
    type: Literal["secret", "publishable"] = Field(
        default="secret",
        description="publishable: for a web page (the chat widget); ask-only, tied to websites",
    )
    allowed_domains: list[str] = Field(
        default_factory=list,
        max_length=50,
        description='Websites a publishable key works on, e.g. ["erp.example.com", "*.x.com"]',
    )

    @model_validator(mode="after")
    def check_publishable(self) -> "KeyCreate":
        self.allowed_domains = sorted(
            {normalize_domain(d) for d in self.allowed_domains if d.strip()}
        )
        if self.type == "publishable":
            if not self.allowed_domains:
                raise ValueError("A publishable key needs at least one allowed domain")
            self.scopes = [Scope.ASK]  # never more, whatever was asked for
        elif self.allowed_domains:
            raise ValueError("allowed_domains is only for publishable keys")
        return self


class KeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    key_type: str
    allowed_domains: list[str]
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None


class KeyCreated(KeyOut):
    key: str = Field(description="The full key. Shown once; store it now.")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=KeyCreated)
async def create_key(
    body: KeyCreate,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> KeyCreated:
    """Create a key. The full key is in this response only. A publishable key (type `publishable`)
    is ask-only and works only from `allowed_domains`.
    """
    publishable = body.type == "publishable"
    key, prefix, key_hash = generate_api_key(publishable=publishable)
    row = ApiKey(
        tenant_id=principal.tenant_id,
        name=body.name,
        key_hash=key_hash,
        prefix=prefix,
        scopes=sorted({s.value for s in body.scopes}),
        key_type=body.type,
        allowed_domains=body.allowed_domains,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return KeyCreated(**KeyOut.model_validate(row).model_dump(), key=key)


@router.get("", response_model=list[KeyOut])
async def list_keys(
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKey]:
    """API keys of this account, newest first. The keys themselves are never shown again."""
    rows = await session.scalars(
        select(ApiKey)
        .where(ApiKey.tenant_id == principal.tenant_id)
        .order_by(ApiKey.created_at.desc())
    )
    return list(rows)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke a key. Anything using it stops working at once."""
    row = await session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == principal.tenant_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    row.revoked = True
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
