"""Hosted mode refuses connection strings that reach the server's own network (SSRF)."""

import socket

import pytest
from fastapi.testclient import TestClient

from datachat_agent.config import get_settings
from datachat_agent.sql.connector import (
    ConnectionConfigError,
    check_dsn_allowed,
    engine_options,
    public_address,
)

PUBLIC = "93.184.216.34"


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("BLOCK_PRIVATE_NETWORKS", "true")
    get_settings.cache_clear()


@pytest.fixture
def fake_dns(monkeypatch):
    """Names resolve to whatever the test says, without touching the network."""
    table = {
        "db.example.com": [PUBLIC],
        "rebind.example.com": ["10.0.0.7"],
        "mixed.example.com": [PUBLIC, "192.168.1.5"],
    }
    real = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.20",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fd00::1",
        "fe80::1",
        "rebind.example.com",
        "mixed.example.com",
    ],
)
def test_internal_addresses_are_refused(fake_dns, host):
    with pytest.raises(ConnectionConfigError, match="internal network"):
        public_address(host)


def test_public_address_is_allowed(fake_dns):
    assert public_address("db.example.com") == PUBLIC


def test_unknown_host_and_missing_host(fake_dns):
    with pytest.raises(ConnectionConfigError, match="Cannot find"):
        public_address("no-such-host.invalid")
    with pytest.raises(ConnectionConfigError, match="must name a host"):
        public_address(None)


def test_guard_is_off_unless_hosted(fake_dns):
    check_dsn_allowed("postgresql://u:p@localhost/db")  # self-hosting: fine


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@db.example.com/db?host=10.0.0.1",
        "postgresql://u:p@db.example.com/db?hostaddr=127.0.0.1",
        "postgresql://u:p@db.example.com/db?service=internal",
        "mysql://u:p@db.example.com/db?unix_socket=/var/run/mysqld.sock",
    ],
)
def test_options_that_change_the_host_are_refused(hosted, fake_dns, dsn):
    with pytest.raises(ConnectionConfigError, match="change the host"):
        check_dsn_allowed(dsn)


def test_engine_is_pinned_to_the_checked_address(hosted, fake_dns):
    url, args = engine_options("postgresql", "postgresql+psycopg://u:p@db.example.com/db")
    assert args["hostaddr"] == PUBLIC and "db.example.com" in url  # name kept for TLS
    url, args = engine_options("mysql", "mysql+pymysql://u:p@db.example.com/db")
    assert f"@{PUBLIC}/" in url and "hostaddr" not in args


def test_api_refuses_internal_connections_when_hosted(hosted, fake_dns, monkeypatch):
    from datachat_agent.server.app import create_app
    from datachat_agent.server.auth import Principal, authenticate

    app = create_app()
    app.dependency_overrides[authenticate] = lambda: Principal(
        tenant_id=__import__("uuid").uuid4(), key_id=None, scopes=frozenset({"admin"})
    )
    with TestClient(app) as c:
        r = c.post(
            "/v1/connections", json={"name": "x", "dsn": "postgresql://u:p@169.254.169.254/db"}
        )
        assert r.status_code == 422 and "internal network" in r.json()["detail"]
