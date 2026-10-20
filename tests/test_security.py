import os
import socket

import pytest

from deletarr.security import SafetyError, resolve_url, token_hash


def test_setup_is_single_admin_and_session_secure(system):
    assert (
        system.client.post(
            "/api/auth/setup", json={"username": "b", "password": "very-long-password", "setup_token": "x"}
        ).status_code
        == 409
    )
    cookie = system.client.cookies["deletarr_session"]
    assert system.db.one("SELECT * FROM sessions WHERE id=?", (token_hash(cookie),))
    assert not system.db.one("SELECT * FROM sessions WHERE id=?", (cookie,))
    assert system.db.one("SELECT password_hash FROM administrator")["password_hash"].startswith("$argon2id$")
    assert os.stat(system.db.directory / "credentials.key").st_mode & 0o077 == 0


def test_auth_csrf_and_origin(system):
    client = system.client
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/jobs", json={"kind": "maintenance"}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert (
        client.post("/api/jobs", headers={"Origin": "https://evil.example"}, json={"kind": "maintenance"}).status_code
        == 403
    )
    assert client.post("/api/jobs", json={"kind": "maintenance"}).status_code == 200
    client.cookies.clear()
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/internal/openapi.json").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_login_rate_limit_and_invalidation(system):
    client = system.client
    assert client.post("/api/auth/logout", json={}).status_code == 200
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 409
    response = client.post("/api/auth/login", json={"username": "admin", "password": "long-local-password"})
    assert "Too many" in response.text


def test_validation_does_not_echo_credentials(system):
    response = system.client.post(
        "/api/auth/login", json={"username": "admin", "password": "sensitive", "extra": "secret"}
    )
    assert response.status_code == 422
    assert "sensitive" not in response.text and "secret" not in response.text


@pytest.mark.parametrize(
    "address,trust,accepted",
    [
        ("127.0.0.1", False, False),
        ("192.168.1.2", True, True),
        ("169.254.169.254", True, False),
        ("::ffff:169.254.169.254", True, False),
        ("0.0.0.0", True, False),
        ("93.184.216.34", False, True),
    ],
)
def test_destination_trust(monkeypatch, address, trust, accepted):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80))]
    )
    if accepted:
        assert resolve_url("http://media.example", trust)[1]
    else:
        with pytest.raises(SafetyError):
            resolve_url("http://media.example", trust)


def test_local_username_supports_unicode(system):
    system.db.execute("UPDATE administrator SET username=? WHERE id=1", ("Jörg",))
    response = system.client.post("/api/auth/login", json={"username": "Jörg", "password": "long-local-password"})
    assert response.status_code == 200
    assert system.client.get("/api/auth/status").json()["authenticated"]


def test_configured_unavailable_plex_does_not_prevent_local_setup(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from deletarr.app import create_app

    def unavailable(*args, **kwargs):
        raise socket.gaierror("offline DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unavailable)
    monkeypatch.setenv("DELETARR_PLEX_URL", "http://plex-unavailable.local:32400")
    monkeypatch.setenv("DELETARR_PLEX_SECRET", "test-only-token")
    app = create_app(tmp_path / "offline-config", run_worker=False)
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        token = (tmp_path / "offline-config" / "setup-token").read_text().strip()
        response = client.post(
            "/api/auth/setup",
            json={"username": "local-admin", "password": "independent-local-password", "setup_token": token},
        )
        assert response.status_code == 200
        assert client.get("/api/auth/status").json()["authenticated"]
