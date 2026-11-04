"""Console sign-in, users, and the query log the console shows."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from datachat_agent.security import (
    DUMMY_PASSWORD_HASH,
    Scope,
    generate_session_token,
    hash_password,
    password_problem,
    verify_password,
)
from datachat_agent.server.auth import Principal, authenticate, require_scope
from datachat_agent.server.db import get_session
from datachat_agent.server.limits import enforce_login_limit
from datachat_agent.server.models import ConsoleSession, QueryLog, Tenant, User

router = APIRouter(tags=["console"])
admin = require_scope(Scope.ADMIN)
SESSION_HOURS = 12

Role = Literal["owner", "member"]


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    role: str
    disabled: bool
    last_login_at: datetime | None
    created_at: datetime


class Me(UserOut):
    tenant_id: uuid.UUID
    tenant_name: str


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class LoginOut(BaseModel):
    token: str
    expires_at: datetime
    user: Me


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str | None = Field(default=None, max_length=100)
    role: Role = "member"
    password: str = Field(max_length=200)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    role: Role | None = None
    disabled: bool | None = None


def _user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        name=u.name,
        role=u.role,
        disabled=u.disabled,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
    )


async def _me(session: AsyncSession, user: User) -> Me:
    tenant = await session.get(Tenant, user.tenant_id)
    return Me(
        **_user_out(user).model_dump(),
        tenant_id=user.tenant_id,
        tenant_name=tenant.name if tenant else "",
    )


def _check_password(password: str) -> None:
    problem = password_problem(password)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Weak password. {problem}")


# --- sign in ---------------------------------------------------------------------------


@router.post("/auth/login", response_model=LoginOut)
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)) -> LoginOut:
    """Sign in to the console with email and password. Returns a session token valid for 12 hours.
    No key needed.
    """
    email = body.email.strip().lower()
    await enforce_login_limit(email)
    user = await session.scalar(select(User).where(User.email == email))
    # Always check a hash, so an unknown email answers as slowly as a wrong password.
    ok = verify_password(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not ok or user.disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    token, token_hash = generate_session_token()
    expires = datetime.now(UTC) + timedelta(hours=SESSION_HOURS)
    session.add(ConsoleSession(user_id=user.id, token_hash=token_hash, expires_at=expires))
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    return LoginOut(token=token, expires_at=expires, user=await _me(session, user))


def _require_user(principal: Principal) -> uuid.UUID:
    if principal.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign in to the console to use this.")
    return principal.user_id


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    principal: Principal = Depends(authenticate), session: AsyncSession = Depends(get_session)
) -> Response:
    """End this console session."""
    _require_user(principal)
    await session.execute(
        update(ConsoleSession).where(ConsoleSession.id == principal.session_id).values(revoked=True)
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me", response_model=Me)
async def me(
    principal: Principal = Depends(authenticate), session: AsyncSession = Depends(get_session)
) -> Me:
    """The signed-in console user."""
    return await _me(session, await session.get(User, _require_user(principal)))


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange,
    principal: Principal = Depends(authenticate),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Change your password. Your other sessions are signed out."""
    user = await session.get(User, _require_user(principal))
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The current password is wrong")
    _check_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    # Sign out every other session (e.g. a lost laptop); keep this one.
    await session.execute(
        update(ConsoleSession)
        .where(ConsoleSession.user_id == user.id, ConsoleSession.id != principal.session_id)
        .values(revoked=True)
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- users (owners and admin keys) -----------------------------------------------------


async def _owned_user(user_id: uuid.UUID, principal: Principal, session: AsyncSession) -> User:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.tenant_id == principal.tenant_id)
    )
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


async def _active_owners(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count()).where(
            User.tenant_id == tenant_id, User.role == "owner", User.disabled.is_(False)
        )
    )


@router.get("/users", response_model=list[UserOut])
async def list_users(
    principal: Principal = Depends(admin), session: AsyncSession = Depends(get_session)
) -> list[UserOut]:
    """Console users of this account."""
    rows = await session.scalars(
        select(User).where(User.tenant_id == principal.tenant_id).order_by(User.created_at)
    )
    return [_user_out(u) for u in rows]


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def create_user(
    body: UserCreate,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Add a console user (owner or member) with a password that meets the rules."""
    _check_password(body.password)
    user = User(
        tenant_id=principal.tenant_id,
        email=body.email.strip().lower(),
        name=body.name,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That email already has an account") from None
    await session.refresh(user)
    return _user_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Change a user's name or role, or disable them (which signs them out). At least one active
    owner is kept.
    """
    user = await _owned_user(user_id, principal, session)
    losing_owner = (
        user.role == "owner"
        and not user.disabled
        and (body.role == "member" or body.disabled is True)
    )
    if losing_owner and await _active_owners(session, principal.tenant_id) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "Keep at least one active owner")
    if body.disabled is True and user.id == principal.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot disable yourself")
    if body.name is not None:
        user.name = body.name
    if body.role is not None:
        user.role = body.role
    if body.disabled is not None:
        user.disabled = body.disabled
        if body.disabled:
            await session.execute(
                update(ConsoleSession).where(ConsoleSession.user_id == user.id).values(revoked=True)
            )
    await session.commit()
    await session.refresh(user)
    return _user_out(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove a console user. You cannot remove yourself or the last owner."""
    user = await _owned_user(user_id, principal, session)
    if user.id == principal.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete yourself")
    if (
        user.role == "owner"
        and not user.disabled
        and await _active_owners(session, principal.tenant_id) <= 1
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Keep at least one active owner")
    await session.delete(user)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- query log --------------------------------------------------------------------------


class LogEntry(BaseModel):
    id: uuid.UUID
    question: str
    route: str | None
    sql: str | None
    row_count: int | None
    answer: str | None
    latency_ms: int | None
    tokens: int
    feedback: str | None
    feedback_comment: str | None
    error: str | None
    created_at: datetime


class LogPage(BaseModel):
    total: int
    items: list[LogEntry]


@router.get("/query-log", response_model=LogPage)
async def query_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    feedback: Literal["helpful", "wrong", "any"] | None = None,
    route: str | None = Query(None, max_length=20),
    errors: bool = False,
    q: str | None = Query(None, max_length=200, description="Search questions and answers"),
    principal: Principal = Depends(admin),
    session: AsyncSession = Depends(get_session),
) -> LogPage:
    """Every question asked, newest first: what was asked, the SQL, the answer and its rating."""
    where = [QueryLog.tenant_id == principal.tenant_id]
    if feedback == "any":
        where.append(QueryLog.feedback.is_not(None))
    elif feedback:
        where.append(QueryLog.feedback == feedback)
    if route:
        where.append(QueryLog.route == route)
    if errors:
        where.append(QueryLog.error.is_not(None))
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            or_(
                QueryLog.question.ilike(pattern, escape="\\"),
                QueryLog.answer.ilike(pattern, escape="\\"),
            )
        )
    total = await session.scalar(select(func.count()).select_from(QueryLog).where(*where))
    rows = await session.scalars(
        select(QueryLog)
        .where(*where)
        .order_by(QueryLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return LogPage(
        total=total or 0,
        items=[
            LogEntry(
                id=r.id,
                question=r.question,
                route=r.route,
                sql=r.sql,
                row_count=r.row_count,
                answer=r.answer,
                latency_ms=r.latency_ms,
                tokens=r.tokens,
                feedback=r.feedback,
                feedback_comment=r.feedback_comment,
                error=r.error,
                created_at=r.created_at,
            )
            for r in rows
        ],
    )
