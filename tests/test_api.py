from fastapi.testclient import TestClient

from datachat_agent.server.app import create_app

client = TestClient(create_app())


def test_health_needs_no_key():
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_admin_routes_reject_missing_key():
    for path in ("/v1/keys", "/v1/usage"):
        r = client.get(path)
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
