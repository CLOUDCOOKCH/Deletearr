import json

import httpx
import pytest
from conftest import add_movie, candidate, fake_integrations
from test_execution import live

from deletarr.db import uid
from deletarr.integrations import HTTP, Radarr, Sonarr
from deletarr.security import SafetyError


async def test_manager_ordering_and_exact_deletion(system):
    _, physical, item = add_movie(system)
    db = system.db
    db.execute("UPDATE files SET manager='radarr',manager_id=11,manager_parent=22")
    db.execute("INSERT INTO mappings VALUES(?,?,?,?)", (uid(), "radarr", "/movies", system.root_id))
    calls = []
    monitored = True
    deleted = False

    def handler(request):
        nonlocal monitored, deleted
        path = request.url.path
        calls.append((request.method, path))
        if path == "/api/v3/config/mediamanagement":
            return httpx.Response(200, json={"recycleBin": "", "deleteEmptyFolders": False})
        if path == "/api/v3/moviefile":
            return httpx.Response(200, json=[] if deleted else [{"id": 11, "path": "/movies/movie-1.mkv"}])
        if path == "/api/v3/movie/22":
            if request.method == "PUT":
                monitored = json.loads(request.content)["monitored"]
            return httpx.Response(200, json={"id": 22, "monitored": monitored})
        if path == "/api/v3/moviefile/11":
            if request.method == "DELETE":
                assert monitored is False
                physical.unlink()
                deleted = True
                return httpx.Response(200)
            return httpx.Response(404) if deleted else httpx.Response(200, json={"id": 11})
        raise AssertionError((request.method, path))

    manager = Radarr(HTTP("https://radarr.test", {}, transport=httpx.MockTransport(handler)))
    row = candidate(system)
    fake_integrations(system, {"1": item}, {"radarr": manager})
    live(system)
    plan = system.app.state.executor.build(row["id"])
    result = await system.app.state.executor.execute(plan["id"], plan["digest"], "managed")
    assert result["state"] == "Completed", result
    assert not physical.exists()
    assert calls.index(("PUT", "/api/v3/movie/22")) < calls.index(("DELETE", "/api/v3/moviefile/11"))


@pytest.mark.parametrize(
    "config",
    [{}, {"recycleBin": "/recycle", "deleteEmptyFolders": False}, {"recycleBin": "", "deleteEmptyFolders": True}],
)
async def test_unsafe_manager_configuration_never_deletes(system, config):
    _, physical, item = add_movie(system)
    system.db.execute("UPDATE files SET manager='radarr',manager_id=11,manager_parent=22")
    system.db.execute("INSERT INTO mappings VALUES(?,?,?,?)", (uid(), "radarr", "/movies", system.root_id))
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.url.path == "/api/v3/config/mediamanagement":
            return httpx.Response(200, json=config)
        return httpx.Response(200, json=[{"id": 11, "path": "/movies/movie-1.mkv"}])

    manager = Radarr(HTTP("https://radarr.test", {}, transport=httpx.MockTransport(handler)))
    row = candidate(system)
    fake_integrations(system, {"1": item}, {"radarr": manager})
    live(system)
    plan = system.app.state.executor.build(row["id"])
    result = await system.app.state.executor.execute(plan["id"], plan["digest"], "refused")
    assert result["state"] == "ReconciliationRequired"
    assert physical.exists() and "DELETE" not in calls and "PUT" not in calls


async def test_record_removal_requires_verified_import_exclusion():
    calls = []
    deleted = False

    def handler(request):
        nonlocal deleted
        calls.append(request)
        if request.url.path == "/api/v3/exclusions":
            return httpx.Response(200, json=[{"tmdbId": 123}])
        if request.method == "DELETE":
            deleted = True
            return httpx.Response(200)
        return httpx.Response(404) if deleted else httpx.Response(200, json={"id": 2, "tmdbId": 123})

    await Radarr(HTTP("https://radarr.test", {}, transport=httpx.MockTransport(handler))).remove(2)
    mutation = next(r for r in calls if r.method == "DELETE")
    assert mutation.url.params["deleteFiles"] == "false"
    assert mutation.url.params["addImportExclusion"] == "true"
    assert calls[-1].url.path == "/api/v3/exclusions"


async def test_sonarr_missing_season_cannot_confirm_unmonitor():
    def handler(request):
        return httpx.Response(200, json={"id": 1, "monitored": True, "seasons": []})

    manager = Sonarr(HTTP("https://sonarr.test", {}, transport=httpx.MockTransport(handler)))
    with pytest.raises(SafetyError):
        await manager.unmonitor_scope(1, 99)


def test_mount_loss_cannot_look_like_successful_deletion(system):
    root = system.db.one("SELECT * FROM roots")
    (system.root / root["sentinel"]).unlink()
    with pytest.raises(FileNotFoundError):
        system.fs.exists(root["id"], str(system.root / "absent.mkv"))


def test_new_download_at_deleted_path_gets_new_asset(system):
    ident, physical, item = add_movie(system)
    old = system.db.one("SELECT id FROM files")["id"]
    system.db.execute("UPDATE files SET deleted=1")
    physical.unlink()
    physical.write_bytes(b"new download")
    system.app.state.catalog.save(system.db.one("SELECT * FROM libraries WHERE id='lib'"), item)
    current = system.app.state.catalog.files(ident)
    assert len(current) == 1 and current[0]["id"] != old
    assert system.db.one("SELECT deleted FROM files WHERE id=?", (old,))["deleted"] == 1
