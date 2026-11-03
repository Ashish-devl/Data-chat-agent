"""Publishable (browser) keys: ask-only, tied to websites, cannot claim a user context."""

import pytest

from datachat_agent.security import domain_allowed, normalize_domain
from datachat_agent.server.routes.ask import llm_dependency
from tests.fakes import ScriptedChat, sql
from tests.integration.conftest import PG_READONLY, scanned_connection

COUNT = sql("SELECT COUNT(*) AS buyers FROM public.buyers")
SITE = {"Origin": "https://erp.example.com"}


@pytest.fixture
def use_llm(client):
    def _install(*responses):
        llm = ScriptedChat(list(responses))
        client.app.dependency_overrides[llm_dependency] = lambda: llm
        return llm

    yield _install
    client.app.dependency_overrides.pop(llm_dependency, None)


def pub_key(client, admin, domains=("erp.example.com",), **extra):
    r = client.post(
        "/v1/keys",
        headers=admin,
        json={"name": "widget", "type": "publishable", "allowed_domains": list(domains), **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


def auth(key, origin=SITE):
    return {"Authorization": f"Bearer {key['key']}", **(origin or {})}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Shop.Example.com/path", "shop.example.com"),
        ("example.com:8443", "example.com"),
        ("*.Example.com", "*.example.com"),
        (" erp.example.com. ", "erp.example.com"),
    ],
)
def test_normalize_domain(value, expected):
    assert normalize_domain(value) == expected


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("erp.example.com", True),
        ("a.b.example.com", True),
        ("example.com", False),
        ("evil-example.com", False),
        ("erp.example.com.evil.io", False),
        (None, False),
    ],
)
def test_domain_allowed(host, allowed):
    assert domain_allowed(host, ["erp.example.com", "*.example.com"]) is allowed


def test_wildcard_does_not_match_the_bare_domain():
    assert domain_allowed("shop.example.com", ["*.example.com"])
    assert not domain_allowed("example.com", ["*.example.com"])
    assert not domain_allowed("badexample.com", ["*.example.com"])


def test_creating_a_publishable_key(client, admin):
    key = pub_key(
        client,
        admin,
        domains=["https://ERP.example.com/", "*.example.com"],
        scopes=["admin", "docs"],
    )
    assert key["key"].startswith("dc_pub_") and key["key_type"] == "publishable"
    assert key["scopes"] == ["ask"]  # whatever was asked for
    assert key["allowed_domains"] == ["*.example.com", "erp.example.com"]


def test_publishable_key_needs_domains_and_secret_keys_refuse_them(client, admin):
    r = client.post("/v1/keys", headers=admin, json={"name": "w", "type": "publishable"})
    assert r.status_code == 422
    r = client.post("/v1/keys", headers=admin, json={"name": "s", "allowed_domains": ["x.com"]})
    assert r.status_code == 422


def test_works_only_from_allowed_websites(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    key = pub_key(client, admin)
    body = {"question": "How many buyers?", "connection_id": conn["id"]}
    use_llm(COUNT, "Six buyers.")
    assert client.post("/v1/ask", headers=auth(key), json=body).status_code == 200
    for origin in ({"Origin": "https://evil.io"}, None, {"Origin": "null"}):
        r = client.post("/v1/ask", headers=auth(key, origin), json=body)
        assert r.status_code == 403 and "not allowed on this website" in r.json()["detail"]


def test_publishable_key_is_ask_only(client, admin):
    key = pub_key(client, admin)
    h = auth(key)
    assert client.get("/v1/documents", headers=h).status_code == 403
    assert client.get("/v1/keys", headers=h).status_code == 403
    assert client.get("/v1/connections", headers=h).status_code == 403
    assert (
        client.post("/v1/keys", headers=h, json={"name": "x", "scopes": ["admin"]}).status_code
        == 403
    )


def test_publishable_key_cannot_claim_a_user_context(client, admin, use_llm):
    conn = scanned_connection(
        client, admin, PG_READONLY, row_filters={"styles": "buyer_id = :buyer"}
    )
    key = pub_key(client, admin)
    r = client.post(
        "/v1/ask",
        headers=auth(key),
        json={"question": "q", "connection_id": conn["id"], "user_context": {"buyer": 2}},
    )
    assert r.status_code == 403 and "user_context" in r.json()["detail"]
    r = client.post(
        "/v1/ask/stream",
        headers=auth(key),
        json={"question": "q", "connection_id": conn["id"], "user_context": {"buyer": 2}},
    )
    assert r.status_code == 403
    # Without a context the row filter fails closed: no rows from a filtered table.
    use_llm(
        sql("SELECT DISTINCT buyer_id FROM styles"), sql("SELECT DISTINCT buyer_id FROM styles")
    )
    body = client.post(
        "/v1/ask",
        headers=auth(key),
        json={"question": "Which buyers?", "connection_id": conn["id"]},
    ).json()
    assert body["rows"] == [] and "buyer" in body["error"]


def test_widget_sessions_are_separate_and_private(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    key = pub_key(client, admin)
    use_llm(COUNT, "Secret internal answer.")
    client.post(
        "/v1/ask",
        headers=admin,
        json={"question": "internal q", "connection_id": conn["id"], "session_id": "s1"},
    )

    llm = use_llm(COUNT, "Six.")
    body = client.post(
        "/v1/ask",
        headers=auth(key),
        json={"question": "widget q", "connection_id": conn["id"], "session_id": "s1"},
    ).json()
    assert body["session_id"] == "s1"
    assert "internal q" not in llm.prompt_text(0)  # the app's own s1 is not shared
    assert client.get("/v1/sessions/s1", headers=auth(key)).status_code == 403
    turns = client.get("/v1/sessions/s1", headers=admin).json()["turns"]
    assert [t["question"] for t in turns] == ["internal q"]


def test_widget_feedback_only_on_its_own_answers(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    key = pub_key(client, admin)
    use_llm(COUNT, "Six.")
    internal = client.post(
        "/v1/ask", headers=admin, json={"question": "q", "connection_id": conn["id"]}
    ).json()["query_id"]
    use_llm(COUNT, "Six.")
    own = client.post(
        "/v1/ask", headers=auth(key), json={"question": "q", "connection_id": conn["id"]}
    ).json()["query_id"]
    fb = {"rating": "wrong"}
    assert (
        client.post("/v1/feedback", headers=auth(key), json={"query_id": own, **fb}).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/feedback", headers=auth(key), json={"query_id": internal, **fb}
        ).status_code
        == 404
    )


def test_cors_preflight_from_a_website(client):
    r = client.options(
        "/v1/ask",
        headers={
            "Origin": "https://erp.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in ("*", "https://erp.example.com")
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
