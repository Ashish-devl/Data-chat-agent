"""Console users: sign-in, roles, sessions, password changes and the query log."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from datachat_agent.server.limits import reset_memory_limiter
from datachat_agent.server.routes.ask import llm_dependency
from tests.fakes import ScriptedChat, sql
from tests.integration.conftest import PG_READONLY, make_tenant, scanned_connection

GOOD = "Correct-Horse-42"


def add_user(client, admin, role="member", email=None, password=GOOD):
    email = email or f"{uuid.uuid4().hex[:8]}@example.test"
    r = client.post(
        "/v1/users",
        headers=admin,
        json={"email": email, "role": role, "password": password, "name": "Asha"},
    )
    assert r.status_code == 201, r.text
    return email


def login(client, email, password=GOOD):
    return client.post("/v1/auth/login", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def fresh_login_limits():
    reset_memory_limiter()
    yield
    reset_memory_limiter()


def test_sign_in_and_me(client, admin):
    email = add_user(client, admin, role="owner", email="Owner.One@Example.test")
    r = login(client, "  owner.one@example.TEST ")  # emails are case-insensitive
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"].startswith("dc_sess_") and body["user"]["role"] == "owner"
    me = client.get("/v1/auth/me", headers=bearer(body["token"])).json()
    assert me["email"] == email.lower() and me["tenant_name"].startswith("t-")
    assert me["last_login_at"] is not None


def test_wrong_email_and_wrong_password_look_the_same(client, admin):
    email = add_user(client, admin)
    wrong_password = login(client, email, "Nope-Nope-42")
    unknown = login(client, "nobody@example.test")
    assert wrong_password.status_code == unknown.status_code == 401
    assert wrong_password.json() == unknown.json() == {"detail": "Wrong email or password"}


def test_sign_in_attempts_are_limited(client, admin):
    email = add_user(client, admin)
    codes = [login(client, email, "Bad-Guess-1").status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    assert login(client, email).status_code == 429  # even the right password, for a minute


def test_member_and_owner_permissions(client, admin):
    member, owner = add_user(client, admin), add_user(client, admin, role="owner")
    m = bearer(login(client, member).json()["token"])
    o = bearer(login(client, owner).json()["token"])
    assert client.get("/v1/documents", headers=m).status_code == 200
    for path in ("/v1/keys", "/v1/users", "/v1/connections", "/v1/query-log", "/v1/usage"):
        assert client.get(path, headers=m).status_code == 403, path
        assert client.get(path, headers=o).status_code == 200, path


def test_logout_ends_only_that_session(client, admin):
    email = add_user(client, admin)
    first, second = login(client, email).json()["token"], login(client, email).json()["token"]
    assert client.post("/v1/auth/logout", headers=bearer(first)).status_code == 204
    assert client.get("/v1/auth/me", headers=bearer(first)).status_code == 401
    assert client.get("/v1/auth/me", headers=bearer(second)).status_code == 200


def test_expired_session_is_refused(client, admin):
    from sqlalchemy import update

    from datachat_agent.security import hash_api_key
    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import ConsoleSession

    token = login(client, add_user(client, admin)).json()["token"]

    async def _expire():
        async with get_sessionmaker()() as s:
            await s.execute(
                update(ConsoleSession)
                .where(ConsoleSession.token_hash == hash_api_key(token))
                .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )
            await s.commit()

    client.portal.call(_expire)
    r = client.get("/v1/auth/me", headers=bearer(token))
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_password_change_signs_out_other_sessions(client, admin):
    email = add_user(client, admin)
    here, elsewhere = login(client, email).json()["token"], login(client, email).json()["token"]
    bad = client.post(
        "/v1/auth/password",
        headers=bearer(here),
        json={"current_password": "wrong", "new_password": "New-Pass-1234"},
    )
    assert bad.status_code == 403
    weak = client.post(
        "/v1/auth/password",
        headers=bearer(here),
        json={"current_password": GOOD, "new_password": "short"},
    )
    assert weak.status_code == 422 and "Weak password" in weak.json()["detail"]
    ok = client.post(
        "/v1/auth/password",
        headers=bearer(here),
        json={"current_password": GOOD, "new_password": "New-Pass-1234"},
    )
    assert ok.status_code == 204
    assert client.get("/v1/auth/me", headers=bearer(here)).status_code == 200
    assert client.get("/v1/auth/me", headers=bearer(elsewhere)).status_code == 401
    assert login(client, email, "New-Pass-1234").status_code == 200


def test_api_keys_cannot_use_session_only_endpoints(client, admin):
    assert client.get("/v1/auth/me", headers=admin).status_code == 403


def test_user_management_rules(client):
    admin = make_tenant(client)
    owner = add_user(client, admin, role="owner")
    o = bearer(login(client, owner).json()["token"])
    users = {u["email"]: u for u in client.get("/v1/users", headers=o).json()}
    me_id = users[owner]["id"]

    # The only owner cannot be demoted, disabled or deleted, nor delete or disable themselves.
    assert client.patch(f"/v1/users/{me_id}", headers=o, json={"role": "member"}).status_code == 409
    assert client.patch(f"/v1/users/{me_id}", headers=o, json={"disabled": True}).status_code == 409
    assert client.delete(f"/v1/users/{me_id}", headers=o).status_code == 409

    member = add_user(client, o)
    member_id = {u["email"]: u for u in client.get("/v1/users", headers=o).json()}[member]["id"]
    token = login(client, member).json()["token"]
    r = client.patch(f"/v1/users/{member_id}", headers=o, json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"]
    assert client.get("/v1/auth/me", headers=bearer(token)).status_code == 401  # signed out
    assert login(client, member).status_code == 401
    assert client.delete(f"/v1/users/{member_id}", headers=o).status_code == 204

    dup = client.post("/v1/users", headers=o, json={"email": owner, "password": GOOD})
    assert dup.status_code == 409
    weak = client.post("/v1/users", headers=o, json={"email": "w@x.test", "password": "weakpass"})
    assert weak.status_code == 422


def test_users_are_private_to_their_company(client, admin):
    add_user(client, admin)
    other = make_tenant(client)
    other_user = add_user(client, other)
    other_id = client.get("/v1/users", headers=other).json()[0]["id"]
    assert other_user not in [u["email"] for u in client.get("/v1/users", headers=admin).json()]
    assert (
        client.patch(f"/v1/users/{other_id}", headers=admin, json={"name": "x"}).status_code == 404
    )
    assert client.delete(f"/v1/users/{other_id}", headers=admin).status_code == 404


def test_query_log_filters_and_privacy(client, admin):
    conn = scanned_connection(client, admin, PG_READONLY)
    for question, answer in [("How many buyers?", "Six."), ("Buyers in Germany?", "One.")]:
        client.app.dependency_overrides[llm_dependency] = lambda a=answer: ScriptedChat(
            [sql("SELECT COUNT(*) FROM buyers"), a]
        )
        client.post(
            "/v1/ask", headers=admin, json={"question": question, "connection_id": conn["id"]}
        )
    client.app.dependency_overrides.pop(llm_dependency, None)
    page = client.get("/v1/query-log", headers=admin).json()
    assert page["total"] == 2 and page["items"][0]["question"] == "Buyers in Germany?"
    first = page["items"][1]["id"]
    client.post("/v1/feedback", headers=admin, json={"query_id": first, "rating": "wrong"})

    rated = client.get("/v1/query-log?feedback=wrong", headers=admin).json()
    assert [i["question"] for i in rated["items"]] == ["How many buyers?"]
    found = client.get("/v1/query-log?q=germany", headers=admin).json()
    assert found["total"] == 1
    assert (
        client.get("/v1/query-log?limit=1&offset=1", headers=admin).json()["items"][0]["id"]
        == first
    )
    assert client.get("/v1/query-log", headers=make_tenant(client)).json()["total"] == 0


def test_cli_password_tools(client):
    from datachat_agent.cli import _new_password
    from datachat_agent.security import password_problem

    assert all(password_problem(_new_password()) is None for _ in range(20))
