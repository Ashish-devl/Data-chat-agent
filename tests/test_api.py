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


def test_server_refuses_to_start_without_secret_key(monkeypatch):
    import pytest

    from datachat_agent.config import get_settings

    monkeypatch.setenv("SECRET_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="gen-secret"):
        with TestClient(create_app()):
            pass


def test_server_refuses_invalid_secret_key(monkeypatch):
    import pytest

    from datachat_agent.config import get_settings

    monkeypatch.setenv("SECRET_KEY", "not-a-fernet-key")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="not a valid key"):
        with TestClient(create_app()):
            pass


def test_widget_script_is_served_without_a_key():
    r = client.get("/widget.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert "DataChatWidget" in r.text and "dc_pub_" in r.text


def test_console_is_served_as_a_one_page_app():
    assert client.get("/console", follow_redirects=False).status_code in (307, 302)
    index = client.get("/console/")
    assert index.status_code == 200 and "DataChat Agent Console" in index.text
    assert client.get("/console/connections/abc").text == index.text  # app routes
    asset = next(
        p for p in index.text.split('"') if p.startswith("./assets/") and p.endswith(".js")
    )
    js = client.get("/console/" + asset[2:])
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    assert "immutable" in js.headers["cache-control"]


def test_console_files_cannot_escape_their_folder():
    for path in ("/console/../app.py", "/console/..%2F..%2Fconfig.py", "/console/%2e%2e/app.py"):
        r = client.get(path)
        assert "def create_app" not in r.text and "Settings" not in r.text
