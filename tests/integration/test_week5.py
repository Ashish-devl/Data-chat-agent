"""Streaming, feedback, rate limits, token caps, PII masking and key revocation."""

import json
import uuid

import pytest

from datachat_agent.config import get_settings
from datachat_agent.server.routes.ask import llm_dependency
from tests.fakes import ScriptedChat, sql
from tests.integration.conftest import PG_READONLY, make_tenant, scanned_connection

COUNT_BUYERS = sql("SELECT COUNT(*) AS buyers FROM public.buyers")


@pytest.fixture
def use_llm(client):
    def _install(*responses):
        llm = ScriptedChat(list(responses))
        client.app.dependency_overrides[llm_dependency] = lambda: llm
        return llm

    yield _install
    client.app.dependency_overrides.pop(llm_dependency, None)


def stream(client, headers, question, **body) -> list[tuple[str, dict]]:
    r = client.post("/v1/ask/stream", headers=headers, json={"question": question, **body})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in r.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def tenant_of(client, headers) -> uuid.UUID:
    from sqlalchemy import select

    from datachat_agent.security import hash_api_key
    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import ApiKey

    key = headers["Authorization"].split()[1]

    async def _get():
        async with get_sessionmaker()() as s:
            return await s.scalar(
                select(ApiKey.tenant_id).where(ApiKey.key_hash == hash_api_key(key))
            )

    return client.portal.call(_get)


def set_limits(client, headers, per_minute=None, monthly_tokens=None):
    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import Tenant

    tenant_id = tenant_of(client, headers)

    async def _set():
        async with get_sessionmaker()() as s:
            t = await s.get(Tenant, tenant_id)
            t.rate_limit_per_minute, t.monthly_token_limit = per_minute, monthly_tokens
            await s.commit()

    client.portal.call(_set)


# --- streaming ---------------------------------------------------------------------------


def test_stream_sends_meta_data_tokens_then_done(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    use_llm(COUNT_BUYERS, "There are six buyers in total.")
    events = stream(
        client, admin, "How many buyers?", connection_id=conn["id"], session_id="s-stream"
    )
    kinds = [e for e, _ in events]
    assert kinds[0] == "meta" and kinds[1] == "data" and kinds[-1] == "done"
    assert kinds.count("token") == 6  # one per word
    meta, data, done = events[0][1], events[1][1], events[-1][1]
    assert meta == {"session_id": "s-stream", "route": "database"}
    assert data["rows"] == [{"buyers": 6}] and data["sql"].endswith("LIMIT 500")
    streamed = "".join(d["text"] for e, d in events if e == "token")
    assert streamed == done["answer"] == "There are six buyers in total."
    assert done["tokens"] == 20 and done["query_id"] and done["rows"] == [{"buyers": 6}]

    turns = client.get("/v1/sessions/s-stream", headers=admin).json()["turns"]
    assert turns[0]["answer"] == "There are six buyers in total."


def test_stream_and_ask_give_the_same_answer(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    use_llm(COUNT_BUYERS, "Six buyers.")
    plain = client.post(
        "/v1/ask", headers=admin, json={"question": "q", "connection_id": conn["id"]}
    ).json()
    use_llm(COUNT_BUYERS, "Six buyers.")
    done = stream(client, admin, "q", connection_id=conn["id"])[-1][1]
    for field in ("answer", "route", "sql", "rows", "chart", "citations", "tokens", "error"):
        assert plain[field] == done[field], field


def test_stream_document_answer_has_citations(client, use_llm, tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "rules.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(72, 750, "Carton weight may not exceed 15 kg for any export carton.")
    c.save()
    headers = make_tenant(client)
    with path.open("rb") as f:
        client.post(
            "/v1/documents", headers=headers, files={"file": ("rules.pdf", f, "application/pdf")}
        )
    use_llm(
        '{"need_items": false, "document": null, "item": null}',
        "Cartons may weigh at most 15 kg [1].",
    )
    events = stream(client, headers, "Max carton weight?")
    done = events[-1][1]
    assert events[0][1]["route"] == "documents" and "data" not in [e for e, _ in events]
    assert done["citations"][0]["filename"] == "rules.pdf"


def test_stream_without_sources_sends_the_fixed_reply(client, use_llm):
    headers = make_tenant(client)
    use_llm()
    events = stream(client, headers, "Anything?")
    assert [e for e, _ in events] == ["meta", "token", "done"]
    assert "No data source" in events[-1][1]["answer"]


def test_stream_answer_model_failure_falls_back_to_the_data(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    use_llm(COUNT_BUYERS, RuntimeError("Error code: 503 high demand"))
    done = stream(client, admin, "How many buyers?", connection_id=conn["id"])[-1][1]
    assert done["answer"].startswith("Result: buyers: 6.") and done["rows"] == [{"buyers": 6}]


def test_stream_model_down_reports_error(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    use_llm(RuntimeError("Error code: 503"), RuntimeError("Error code: 503"))
    events = stream(client, admin, "How many buyers?", connection_id=conn["id"])
    done = events[-1][1]
    assert "overloaded" in done["error"] and done["rows"] == []


def test_stream_checks_scope_before_streaming(client, admin):
    r = client.post("/v1/keys", headers=admin, json={"name": "d", "scopes": ["docs"]})
    docs_only = {"Authorization": f"Bearer {r.json()['key']}"}
    r = client.post("/v1/ask/stream", headers=docs_only, json={"question": "q"})
    assert r.status_code == 403


# --- feedback ----------------------------------------------------------------------------


def test_feedback_is_recorded_and_can_be_changed(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    use_llm(COUNT_BUYERS, "Six.")
    qid = client.post(
        "/v1/ask", headers=admin, json={"question": "q", "connection_id": conn["id"]}
    ).json()["query_id"]
    r = client.post(
        "/v1/feedback",
        headers=admin,
        json={"query_id": qid, "rating": "wrong", "comment": " Should be 7 "},
    )
    assert r.status_code == 200 and r.json()["comment"] == "Should be 7"
    r = client.post("/v1/feedback", headers=admin, json={"query_id": qid, "rating": "helpful"})
    assert r.json()["rating"] == "helpful" and r.json()["comment"] is None

    from datachat_agent.server.db import get_sessionmaker
    from datachat_agent.server.models import QueryLog

    async def _get():
        async with get_sessionmaker()() as s:
            return await s.get(QueryLog, uuid.UUID(qid))

    log = client.portal.call(_get)
    assert log.feedback == "helpful" and log.feedback_at is not None


def test_feedback_validation_and_isolation(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    use_llm(COUNT_BUYERS, "Six.")
    qid = client.post(
        "/v1/ask", headers=admin, json={"question": "q", "connection_id": conn["id"]}
    ).json()["query_id"]
    bad = client.post("/v1/feedback", headers=admin, json={"query_id": qid, "rating": "meh"})
    assert bad.status_code == 422
    other = make_tenant(client)
    r = client.post("/v1/feedback", headers=other, json={"query_id": qid, "rating": "wrong"})
    assert r.status_code == 404
    r = client.post(
        "/v1/feedback", headers=admin, json={"query_id": str(uuid.uuid4()), "rating": "wrong"}
    )
    assert r.status_code == 404


# --- rate limits and token caps -----------------------------------------------------------


def test_requests_per_minute_limit(client):
    headers = make_tenant(client)
    set_limits(client, headers, per_minute=3)
    codes = [client.get("/v1/keys", headers=headers).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.get("/v1/keys", headers=headers)
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 60
    assert "3 requests per minute" in r.json()["detail"]


def test_limit_is_per_key(client):
    headers = make_tenant(client)
    set_limits(client, headers, per_minute=2)
    other_key = client.post("/v1/keys", headers=headers, json={"name": "k2", "scopes": ["admin"]})
    second = {"Authorization": f"Bearer {other_key.json()['key']}"}
    client.get("/v1/keys", headers=headers)
    assert client.get("/v1/keys", headers=headers).status_code == 429
    assert client.get("/v1/keys", headers=second).status_code == 200


def test_rate_limit_in_redis(client, monkeypatch):
    from datachat_agent.server.limits import reset_memory_limiter

    try:
        import redis

        redis.Redis.from_url(get_settings().redis_url).ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis not reachable")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    get_settings.cache_clear()
    reset_memory_limiter()
    headers = make_tenant(client)
    set_limits(client, headers, per_minute=2)
    codes = [client.get("/v1/keys", headers=headers).status_code for _ in range(3)]
    reset_memory_limiter()
    assert codes == [200, 200, 429]


def test_monthly_token_cap(client, use_llm):
    headers = make_tenant(client)
    conn = scanned_connection(client, headers, PG_READONLY)
    set_limits(client, headers, monthly_tokens=15)
    use_llm(COUNT_BUYERS, "Six.")
    first = client.post(
        "/v1/ask", headers=headers, json={"question": "q", "connection_id": conn["id"]}
    )
    assert first.status_code == 200 and first.json()["tokens"] == 20
    second = client.post(
        "/v1/ask", headers=headers, json={"question": "q", "connection_id": conn["id"]}
    )
    assert second.status_code == 429 and "monthly limit" in second.json()["detail"]
    assert (
        client.post(
            "/v1/ask/stream", headers=headers, json={"question": "q", "connection_id": conn["id"]}
        ).status_code
        == 429
    )
    usage = client.get("/v1/usage", headers=headers).json()
    assert usage["tokens"] == 20 and usage["monthly_token_limit"] == 15
    assert usage["tokens_remaining"] == 0 and usage["requests"] == 1


def test_usage_shows_server_defaults(client):
    headers = make_tenant(client)
    usage = client.get("/v1/usage", headers=headers).json()
    assert usage["monthly_token_limit"] is None and usage["tokens_remaining"] is None
    assert usage["rate_limit_per_minute"] == get_settings().default_rate_limit_per_minute


# --- PII masking ------------------------------------------------------------------------


def test_pii_is_masked_for_the_model_and_restored_for_the_user(client, use_llm, monkeypatch):
    monkeypatch.setenv("MASK_PII", "true")
    get_settings.cache_clear()
    headers = make_tenant(client)
    conn = scanned_connection(client, headers, PG_READONLY)
    llm = use_llm(
        sql("SELECT name, phone FROM employees WHERE emp_code = 'E1001'"),
        "You can reach them on [PHONE_1].",
    )
    body = client.post(
        "/v1/ask",
        headers=headers,
        json={"question": "Phone of employee E1001?", "connection_id": conn["id"]},
    ).json()
    phone = body["rows"][0]["phone"]
    assert body["answer"] == f"You can reach them on {phone}."
    sql_prompt, answer_prompt = llm.prompt_text(0), llm.prompt_text(1)
    assert phone not in answer_prompt and "[PHONE_1]" in answer_prompt
    assert phone not in sql_prompt  # sample values in the schema are masked too
    assert "phone varchar" in sql_prompt.lower() or "phone character varying" in sql_prompt.lower()


def test_pii_restored_in_streamed_tokens(client, use_llm, monkeypatch):
    monkeypatch.setenv("MASK_PII", "true")
    get_settings.cache_clear()
    headers = make_tenant(client)
    conn = scanned_connection(client, headers, PG_READONLY)
    use_llm(sql("SELECT phone FROM employees WHERE emp_code = 'E1002'"), "Call [PHONE_1] today.")
    events = stream(client, headers, "Phone of E1002?", connection_id=conn["id"])
    phone = events[1][1]["rows"][0]["phone"]
    streamed = "".join(d["text"] for e, d in events if e == "token")
    assert streamed == f"Call {phone} today." == events[-1][1]["answer"]
    assert "[PHONE" not in streamed


def test_masking_is_off_by_default(client, admin, use_llm):
    conn = scanned_connection(client, admin, PG_READONLY)
    llm = use_llm(sql("SELECT phone FROM employees WHERE emp_code = 'E1003'"), "ok")
    body = client.post(
        "/v1/ask", headers=admin, json={"question": "q", "connection_id": conn["id"]}
    ).json()
    assert body["rows"][0]["phone"] in llm.prompt_text(1)


# --- key revocation (E8) ------------------------------------------------------------------


def test_revoked_key_stops_working_everywhere(client, admin):
    r = client.post("/v1/keys", headers=admin, json={"name": "temp", "scopes": ["ask", "docs"]})
    key = r.json()
    headers = {"Authorization": f"Bearer {key['key']}"}
    assert client.get("/v1/documents", headers=headers).status_code == 200
    assert client.delete(f"/v1/keys/{key['id']}", headers=admin).status_code == 204
    for method, path in [
        ("get", "/v1/documents"),
        ("post", "/v1/ask"),
        ("post", "/v1/ask/stream"),
        ("post", "/v1/feedback"),
    ]:
        kwargs = {"json": {"question": "q"}} if method == "post" else {}
        assert getattr(client, method)(path, headers=headers, **kwargs).status_code == 401
    assert client.get("/v1/health").status_code == 200


def test_feedback_export_for_the_evaluation_set(client, admin, use_llm):
    from datachat_agent.agent.service import export_feedback
    from datachat_agent.server.db import get_sessionmaker

    conn = scanned_connection(client, admin, PG_READONLY)
    ids = []
    for _ in range(2):
        use_llm(COUNT_BUYERS, "Six.")
        ids.append(
            client.post(
                "/v1/ask",
                headers=admin,
                json={"question": "How many buyers?", "connection_id": conn["id"]},
            ).json()["query_id"]
        )
    client.post(
        "/v1/feedback",
        headers=admin,
        json={"query_id": ids[0], "rating": "wrong", "comment": "Should be 7"},
    )
    client.post("/v1/feedback", headers=admin, json={"query_id": ids[1], "rating": "helpful"})
    tenant_id = tenant_of(client, admin)

    async def _export(rating):
        async with get_sessionmaker()() as s:
            return await export_feedback(s, tenant_id, rating)

    wrong = client.portal.call(_export, "wrong")
    assert len(wrong) == 1 and wrong[0]["comment"] == "Should be 7"
    assert wrong[0]["sql_used"].startswith("SELECT COUNT(*)") and wrong[0]["kind"] == "review"
    assert len(client.portal.call(_export, None)) == 2
    other = tenant_of(client, make_tenant(client))

    async def _other():
        async with get_sessionmaker()() as s:
            return await export_feedback(s, other, None)

    assert client.portal.call(_other) == []
