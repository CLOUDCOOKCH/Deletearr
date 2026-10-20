import httpx
import pytest
from conftest import add_movie, add_rule, candidate, fake_integrations

from deletarr.db import encode, now
from deletarr.integrations import HTTP, Seerr
from deletarr.security import SafetyError


async def test_seerr_415_recovers_sync_without_repeating_physical_deletion(system, monkeypatch):
    media_id, physical, item = add_movie(system)
    row = candidate(system)
    requests = []
    failing = True

    def handle(request):
        if request.method == "GET":
            assert request.url.path in {"/api/v1/request", "/api/v1/media"}
            return httpx.Response(
                200,
                json={
                    "results": []
                    if request.url.path.endswith("request")
                    else [
                        {"id": 44, "mediaType": "movie", "tmdbId": 55, "status": 5},
                    ]
                },
            )
        requests.append(request)
        assert request.method == "POST" and request.url.path == "/api/v1/media/44/unknown"
        assert request.headers["Content-Type"] == "application/json"
        return httpx.Response(415) if failing else httpx.Response(200, json={"id": 44, "status": 1})

    adapter = Seerr(HTTP("https://seerr.test", {}, transport=httpx.MockTransport(handle)))
    fake_integrations(system, {"1": item}, {"seerr": adapter})
    system.db.execute("INSERT INTO external_ids VALUES(?,?,?)", (media_id, "tmdb", "55"))
    system.db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?)", ("seerr-ref", media_id, 44, None, 0, 5, "{}"))
    live(system)
    executor = system.app.state.executor
    plan = executor.build(row["id"])
    result = await executor.execute(plan["id"], plan["digest"], "seerr-415")
    assert result["state"] == "ReconciliationRequired" and not physical.exists()
    assert "HTTP 415" in result["error"]

    def no_unlink(*args):
        raise AssertionError("Physical deletion was repeated")

    monkeypatch.setattr(executor.fs, "unlink", no_unlink)
    failing = False
    await executor.reconcile()
    assert system.db.one("SELECT state FROM operations")["state"] == "Completed"
    assert system.db.one("SELECT state FROM reconciliation")["state"] == "resolved"
    assert len(requests) == 2


def live(system):
    response = system.client.post(
        "/api/settings/live",
        json={
            "password": "long-local-password",
            "acknowledge_permanent_deletion": True,
        },
    )
    assert response.status_code == 200, response.text


async def test_dry_run_never_mutates(system):
    ident, physical, item = add_movie(system)
    row = candidate(system)
    fake_integrations(system, {"1": item})
    executor = system.app.state.executor
    plan = executor.build(row["id"])
    result = await executor.execute(plan["id"], plan["digest"], "dry-key")
    assert result["dry_run"] == 1 and result["state"] == "Completed"
    assert physical.exists()
    assert not system.db.all("SELECT * FROM checkpoints")


async def test_exact_live_unlink_idempotency_and_audit(system):
    _, physical, item = add_movie(system)
    neighbor = system.root / "subtitles.srt"
    neighbor.write_text("keep")
    row = candidate(system)
    fake_integrations(system, {"1": item})
    live(system)
    executor = system.app.state.executor
    plan = executor.build(row["id"])
    result = await executor.execute(plan["id"], plan["digest"], "live-key")
    assert result["state"] == "Completed", result
    assert not physical.exists() and neighbor.exists()
    assert system.db.one("SELECT state FROM candidates")["state"] == "Completed"
    assert system.db.one("SELECT * FROM audit WHERE event='deletion.completed'")
    repeated = await executor.execute(plan["id"], plan["digest"], "live-key")
    assert repeated["id"] == result["id"]
    assert len(system.db.all("SELECT * FROM operations")) == 1


@pytest.mark.parametrize("block", ["active", "unreachable", "protected", "changed", "expired", "source_changed"])
async def test_final_validation_blocks_deletion(system, block):
    ident, physical, item = add_movie(system)
    row = candidate(system)
    integrations = fake_integrations(system, {"1": item})
    live(system)
    executor = system.app.state.executor
    plan = executor.build(row["id"])
    if block == "active":
        integrations.plex.active = [{"ratingKey": "1"}]
    elif block == "unreachable":
        integrations.plex.fail = True
    elif block == "protected":
        system.db.execute("UPDATE media SET protected=1 WHERE id=?", (ident,))
    elif block == "changed":
        physical.write_bytes(b"changed")
    elif block == "expired":
        system.db.execute("UPDATE plans SET expires=0")
    elif block == "source_changed":
        item["addedAt"] = now()
    with pytest.raises(SafetyError):
        await executor.execute(plan["id"], plan["digest"], "blocked")
    assert physical.exists()
    assert not system.db.all("SELECT * FROM checkpoints")


async def test_partial_failure_does_not_repeat_unlink(system, monkeypatch):
    _, physical, item = add_movie(system)
    row = candidate(system)
    fake_integrations(system, {"1": item})
    live(system)
    executor = system.app.state.executor
    original = executor.synchronize

    async def fail(*args):
        raise SafetyError("Seerr unavailable")

    monkeypatch.setattr(executor, "synchronize", fail)
    plan = executor.build(row["id"])
    result = await executor.execute(plan["id"], plan["digest"], "partial")
    assert result["state"] == "ReconciliationRequired" and not physical.exists()
    monkeypatch.setattr(executor, "synchronize", original)

    def refuse_unlink(*args):
        raise AssertionError("Deletion was replayed")

    monkeypatch.setattr(executor.fs, "unlink", refuse_unlink)
    await executor.reconcile()
    assert system.db.one("SELECT state FROM operations")["state"] == "Completed"
    assert system.db.one("SELECT state FROM reconciliation")["state"] == "resolved"


def test_shared_multi_episode_asset_refuses_narrow_plan(system):
    first, _, _ = add_movie(system)
    db = system.db
    file_id = db.one("SELECT id FROM files")["id"]
    db.execute("UPDATE media SET kind='episode' WHERE id=?", (first,))
    second, _, _ = add_movie(system, key="2")
    db.execute("UPDATE media SET kind='episode' WHERE id=?", (second,))
    db.execute("DELETE FROM media_files WHERE media_id=?", (second,))
    db.execute("INSERT INTO media_files VALUES(?,?)", (second, file_id))
    add_rule(system, kind="episode")
    system.app.state.engine.run(persist=True)
    row = db.one("SELECT * FROM candidates WHERE media_id=?", (first,))
    with pytest.raises(SafetyError, match="Shared file"):
        system.app.state.executor.build(row["id"])


def test_approval_and_grace_cannot_be_bypassed(system):
    add_movie(system)
    add_rule(system, action="grace", grace_days=7)
    system.app.state.engine.run(persist=True)
    row = system.db.one("SELECT * FROM candidates")
    with pytest.raises(SafetyError, match="Grace period"):
        system.app.state.executor.authorized(row)
    assert (
        system.client.post(
            "/api/candidates/" + row["id"] + "/action", json={"version": row["version"], "action": "approve"}
        ).status_code
        == 200
    )
    row = system.db.one("SELECT * FROM candidates")
    with pytest.raises(SafetyError, match="Grace period"):
        system.app.state.executor.authorized(row)


def test_file_limit_applies_to_whole_scope(system):
    add_movie(system)
    row = candidate(system)
    settings = system.db.settings()
    settings["max_logical_bytes"] = 1
    settings.pop("version")
    system.db.execute("UPDATE settings SET data=?", (encode(settings),))
    with pytest.raises(SafetyError, match="byte limit"):
        system.app.state.executor.build(row["id"])


async def test_season_scope_deletes_shared_asset_once_and_checks_every_episode(system):
    from conftest import add_rule, candidate, fake_integrations

    from deletarr.db import now

    catalog = system.app.state.catalog
    library = system.db.one("SELECT * FROM libraries WHERE id='lib'")
    added = now() - 400 * 86400
    catalog.save(library, {"ratingKey": "10", "type": "show", "title": "Show", "addedAt": added})
    season_id = catalog.save(
        library,
        {
            "ratingKey": "11",
            "parentRatingKey": "10",
            "type": "season",
            "title": "Season 1",
            "index": 1,
            "addedAt": added,
        },
    )
    physical = system.root / "shared.mkv"
    physical.write_bytes(b"x" * 8192)
    items = {}
    for key, index in [("12", 1), ("13", 2)]:
        item = {
            "ratingKey": key,
            "parentRatingKey": "11",
            "parentIndex": 1,
            "index": index,
            "type": "episode",
            "title": "Episode " + str(index),
            "addedAt": added,
            "viewCount": 1,
            "lastViewedAt": now() - 100 * 86400,
            "Media": [{"Part": [{"id": "100", "file": "/media/shared.mkv", "size": 8192}]}],
        }
        ident = catalog.save(library, item)
        system.db.execute("UPDATE media SET history_visible=1 WHERE id=?", (ident,))
        items[key] = item
    rule = add_rule(system, kind="season")
    row = candidate(system, rule)
    assert row["media_id"] == season_id
    fake_integrations(system, items)
    live(system)
    plan = system.app.state.executor.build(row["id"])
    assert len(plan["files"]) == 1 and len(plan["affected"]) == 3
    child = system.db.one("SELECT id FROM media WHERE plex_key='13'")["id"]
    system.db.execute("UPDATE media SET protected=1 WHERE id=?", (child,))
    with pytest.raises(SafetyError, match="eligible"):
        system.app.state.executor.build(row["id"])
    system.db.execute("UPDATE media SET protected=0 WHERE id=?", (child,))
    result = await system.app.state.executor.execute(plan["id"], plan["digest"], "season-shared")
    assert result["state"] == "Completed", result
    assert not physical.exists()
    assert len(system.db.all("SELECT * FROM checkpoints WHERE name LIKE 'file:%'")) == 1


async def test_reimported_asset_requires_a_new_approval_cycle(system):
    _, physical, item = add_movie(system)
    row = candidate(system)
    fake_integrations(system, {"1": item})
    live(system)
    plan = system.app.state.executor.build(row["id"])
    result = await system.app.state.executor.execute(plan["id"], plan["digest"], "initial-asset")
    assert result["state"] == "Completed"
    physical.write_bytes(b"new physical asset")
    system.app.state.catalog.save(system.db.one("SELECT * FROM libraries WHERE id='lib'"), item)
    system.app.state.engine.run(persist=True)
    current = system.db.one("SELECT * FROM candidates WHERE id=?", (row["id"],))
    assert current["state"] == "PendingApproval" and current["approved_at"] is None
    assert physical.exists()
