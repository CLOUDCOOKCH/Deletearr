import pytest

from deletarr.integrations import HTTP


@pytest.fixture
def plex_cloud(monkeypatch):
    state = {"server_id": "owned-server", "owned": True, "pending": False}

    async def request(self, method, path, **kwargs):
        assert self.headers.get("X-Plex-Client-Identifier")
        if path == "/api/v2/pins":
            return {"id": 12, "code": "test-code"}
        if path == "/api/v2/pins/12":
            return {} if state["pending"] else {"authToken": "cloud-secret"}
        if path == "/api/v2/user":
            return {"id": 123}
        if path == "/api/v2/resources":
            return [
                {
                    "name": "My NAS",
                    "clientIdentifier": "owned-server",
                    "owned": state["owned"],
                    "provides": "server",
                    "accessToken": "server-secret",
                    "connections": [{"uri": "http://192.168.1.235:32400"}],
                }
            ]
        if path == "/identity":
            assert self.headers["X-Plex-Token"] == "server-secret"
            return {"MediaContainer": {"machineIdentifier": state["server_id"]}}
        if path == "/library/sections":
            return {"MediaContainer": {"Directory": []}}
        raise AssertionError(path)

    monkeypatch.setattr(HTTP, "request", request)
    return state


def authorize(system):
    response = system.client.post("/api/auth/plex/start?connect=true", json={})
    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://app.plex.tv/auth#?")
    return system.client.post("/api/auth/plex/poll", json={})


def connect(system, **overrides):
    return system.client.post(
        "/api/integrations/plex/connect",
        json={
            "server_id": "owned-server",
            "uri": "http://192.168.1.235:32400",
            "allow_private": True,
            **overrides,
        },
    )


def test_connect_enrolls_only_verified_owner_and_never_returns_tokens(system, plex_cloud):
    response = authorize(system)
    assert response.status_code == 200, response.text
    assert response.json()["servers"][0]["name"] == "My NAS"
    assert "secret" not in response.text
    assert connect(system).status_code == 200
    row = system.db.one("SELECT * FROM integrations WHERE kind='plex'")
    assert row["health"] == "connected" and row["server_id"] == "owned-server"
    assert system.app.state.integrations.vault.decrypt(row["secret"]) == "server-secret"
    assert system.db.one("SELECT plex_identity FROM administrator")["plex_identity"] == "123"
    assert system.db.settings()["dry_run"] is True
    assert system.db.one("SELECT COUNT(*) n FROM auth_pins")["n"] == 0
    assert connect(system).status_code == 409  # Single-use connection grant.
    assert "secret" not in system.client.get("/api/integrations").text


def test_connection_requires_local_admin_and_csrf_before_plex_request(system, plex_cloud):
    csrf = system.client.headers.pop("X-CSRF-Token")
    assert system.client.post("/api/auth/plex/start?connect=true", json={}).status_code == 403
    system.client.headers["X-CSRF-Token"] = csrf
    assert authorize(system).status_code == 200
    system.client.cookies.delete("deletarr_session")
    assert system.client.post("/api/auth/plex/poll", json={}).status_code == 401
    assert connect(system).status_code == 401


def test_connect_rejects_unadvertised_address_and_wrong_server(system, plex_cloud):
    assert authorize(system).status_code == 200
    assert connect(system, uri="http://192.168.1.99:32400").status_code == 409
    assert connect(system, allow_private=False).status_code == 409
    plex_cloud["server_id"] = "different-server"
    assert connect(system).status_code == 409
    assert not system.db.one("SELECT * FROM integrations WHERE kind='plex'")


def test_shared_servers_are_not_offered_and_login_can_be_disabled(system, plex_cloud):
    plex_cloud["owned"] = False
    assert authorize(system).json()["servers"] == []
    assert connect(system).status_code == 409
    plex_cloud["owned"] = True
    assert authorize(system).status_code == 200
    assert connect(system, enable_login=False).status_code == 200
    assert system.db.one("SELECT plex_identity FROM administrator")["plex_identity"] is None


def test_pending_expired_and_environment_managed_connections(system, plex_cloud, monkeypatch):
    plex_cloud["pending"] = True
    assert authorize(system).json() == {"pending": True}
    plex_cloud["pending"] = False
    assert system.client.post("/api/auth/plex/poll", json={}).status_code == 200
    monkeypatch.setenv("DELETARR_PLEX_URL", "http://configured:32400")
    assert connect(system).status_code == 409
    monkeypatch.delenv("DELETARR_PLEX_URL")
    system.db.execute("UPDATE auth_pins SET expires=0")
    assert connect(system).status_code == 409
