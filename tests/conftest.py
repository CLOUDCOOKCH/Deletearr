from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from deletarr.app import create_app
from deletarr.db import encode, now, uid
from deletarr.filesystem import Filesystem


@pytest.fixture
def system(tmp_path):
    app = create_app(tmp_path / "config", run_worker=False)
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        setup_token = (tmp_path / "config" / "setup-token").read_text()
        response = client.post(
            "/api/auth/setup", json={"username": "admin", "password": "long-local-password", "setup_token": setup_token}
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        db = app.state.db
        settings = db.settings()
        settings["max_library_percent"] = 100
        settings.pop("version")
        db.execute("UPDATE settings SET data=? WHERE id=1", (encode(settings),))
        media_root = tmp_path / "media"
        media_root.mkdir()
        fs = Filesystem(db)
        root_id = fs.register(str(media_root))
        db.execute("INSERT INTO mappings VALUES(?,?,?,?)", (uid(), "plex", "/media", root_id))
        db.execute("INSERT INTO libraries VALUES('lib','1','Movies','movie',1,?)", (now(),))
        yield SimpleNamespace(app=app, client=client, db=db, fs=fs, root=media_root, root_id=root_id, temp=tmp_path)


def add_movie(system, key="1", path=None, watched=True, size=8192):
    path = path or f"movie-{key}.mkv"
    physical = system.root / path
    physical.parent.mkdir(exist_ok=True, parents=True)
    physical.write_bytes(b"1" * size)
    item = {
        "ratingKey": key,
        "type": "movie",
        "title": f"Movie {key}",
        "year": 2020,
        "addedAt": now() - 400 * 86400,
        "viewCount": 1 if watched else 0,
        "Media": [{"Part": [{"id": key, "file": "/media/" + path, "size": size}]}],
    }
    if watched:
        item["lastViewedAt"] = now() - 100 * 86400
    library = system.db.one("SELECT * FROM libraries WHERE id='lib'")
    ident = system.app.state.catalog.save(library, item)
    system.db.execute("UPDATE media SET history_visible=1 WHERE id=?", (ident,))
    return ident, physical, item


def add_rule(system, **overrides):
    data = {
        "name": "Watched 90 days",
        "kind": "movie",
        "conditions": {"field": "last_watched_days", "operator": "gte", "value": 90},
        **overrides,
    }
    response = system.client.post("/api/rules", json=data)
    assert response.status_code == 200, response.text
    return response.json()


def candidate(system, rule=None):
    rule = rule or add_rule(system)
    system.app.state.engine.run(persist=True)
    row = system.db.one("SELECT * FROM candidates WHERE rule_id=?", (rule["id"],))
    response = system.client.post(
        "/api/candidates/" + row["id"] + "/action", json={"version": row["version"], "action": "approve"}
    )
    assert response.status_code == 200, response.text
    return system.db.one("SELECT * FROM candidates WHERE id=?", (row["id"],))


class FakePlex:
    def __init__(self, items):
        self.items = items
        self.active = []
        self.fail = False

    async def metadata(self, key):
        return self.items[key]

    async def history_visibility(self):
        return True

    async def sessions(self):
        from deletarr.security import SafetyError

        if self.fail:
            raise SafetyError("Plex unreachable")
        return self.active

    async def pages(self, path, **params):
        if path == "/status/sessions/history/all":
            yield []
        else:
            yield list(self.items.values())


class FakeIntegrations:
    def __init__(self, plex, managers=None):
        self.plex = plex
        self.managers = managers or {}

    def get(self, kind):
        return self.plex if kind == "plex" else self.managers[kind]

    def configured(self, kind):
        return kind in self.managers


def fake_integrations(system, items, managers=None):
    integrations = FakeIntegrations(FakePlex(items), managers)
    system.app.state.executor.integrations = integrations
    system.app.state.catalog.integrations = integrations
    return integrations
