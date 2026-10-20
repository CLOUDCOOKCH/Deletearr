import json

import pytest
from conftest import add_movie, add_rule, fake_integrations
from test_execution import live

from deletarr.db import encode, now
from deletarr.security import SafetyError


def preview(system, media_id, file_ids=None):
    item = {"media_id": media_id}
    if file_ids is not None:
        item["file_ids"] = file_ids
    response = system.client.post("/api/library/deletion-plans", json={"items": [item]})
    assert response.status_code == 200, response.text
    return response.json()


async def execute(system, review):
    response = system.client.post(
        "/api/library/deletion-plans/execute",
        json={
            "plans": [{"id": p["id"], "digest": p["digest"]} for p in review["plans"]],
            "confirmation": review["confirmation"],
            "expected_dry_run": review["dry_run"],
        },
    )
    assert response.status_code == 200, response.text
    job = system.db.one("SELECT * FROM jobs WHERE id=?", (response.json()["job_id"],))
    await system.app.state.worker.dispatch(job)
    return system.db.all("SELECT * FROM operations ORDER BY created_at,id")


def editions(system):
    media_id, original, item = add_movie(system, path="Movie/Movie-1080.mkv")
    second = system.root / "Movie/Movie-2160.mkv"
    second.write_bytes(b"new version" * 1024)
    item["Media"][0].update(videoResolution="1080", videoCodec="h264")
    item["Media"].append(
        {
            "videoResolution": "4k",
            "videoCodec": "hevc",
            "Part": [{"id": "second", "file": "/media/Movie/Movie-2160.mkv", "size": second.stat().st_size}],
        }
    )
    system.app.state.catalog.save(system.db.one("SELECT * FROM libraries WHERE id='lib'"), item)
    fake_integrations(system, {"1": item})
    return media_id, original, second, item


async def test_manual_deletion_needs_no_matching_retention_rule_and_stays_dry(system):
    media_id, physical, item = add_movie(system, watched=False, path="Movie/movie.mkv")
    fake_integrations(system, {"1": item})
    (physical.parent / "movie.en.srt").write_text("subtitle")
    (physical.parent / "poster.jpg").write_bytes(b"poster")
    review = preview(system, media_id)
    assert review["file_count"] == 3
    assert len(review["plans"][0]["directories"]) == 1
    assert system.client.get("/api/rules").json() == []
    assert system.client.get("/api/candidates").json()["items"] == []
    result = await execute(system, review)
    assert result[0]["dry_run"] and result[0]["state"] == "Completed"
    assert physical.exists() and (physical.parent / "poster.jpg").exists()
    assert not system.db.all("SELECT * FROM checkpoints")


async def test_manual_delete_removes_related_sidecars_and_empty_parents_only(system):
    media_id, physical, item = add_movie(system, path="Movie/Edition/movie.mkv")
    fake_integrations(system, {"1": item})
    (physical.parent / "movie.en.srt").write_text("subtitle")
    (physical.parent / "movie.nfo").write_text("metadata")
    (physical.parent.parent / "poster.jpg").write_bytes(b"poster")
    neighbor = system.root / "neighbor.txt"
    neighbor.write_text("keep")
    live(system)
    review = preview(system, media_id)
    assert review["file_count"] == 4
    operations = await execute(system, review)
    assert operations[0]["state"] == "Completed", operations
    assert not (system.root / "Movie").exists()
    assert system.root.is_dir() and neighbor.read_text() == "keep"
    assert all(row["result"] == "removed" for row in json.loads(operations[0]["outcome"])["directories"])


async def test_unknown_neighbor_keeps_its_folder(system):
    media_id, physical, item = add_movie(system, path="Movie/movie.mkv")
    fake_integrations(system, {"1": item})
    unknown = physical.parent / "personal-notes.txt"
    unknown.write_text("do not delete")
    live(system)
    operations = await execute(system, preview(system, media_id))
    assert operations[0]["state"] == "Completed"
    assert unknown.exists() and physical.parent.exists() and not physical.exists()
    assert json.loads(operations[0]["outcome"])["directories"][0]["result"] == "retained_not_empty"


async def test_keep_version_deletes_other_edition_and_preserves_shared_artwork(system):
    media_id, original, kept, _ = editions(system)
    (original.parent / "Movie-1080.en.srt").write_text("old subtitles")
    (original.parent / "Movie-2160.en.srt").write_text("kept subtitles")
    (original.parent / "poster.jpg").write_bytes(b"shared poster")
    group = system.client.get("/api/versions").json()["items"][0]
    assert len(group["versions"]) == 2
    version = next(v for v in group["versions"] if v["resolution"] == "4k")
    live(system)
    response = system.client.post(f"/api/versions/{group['id']}/keep", json={"version_id": version["id"]})
    assert response.status_code == 200, response.text
    review = response.json()
    assert len(review["plans"][0]["files"]) == 1
    assert {f["path"] for f in review["plans"][0]["sidecars"]} == {str(original.parent / "Movie-1080.en.srt")}
    result = await execute(system, review)
    assert result[0]["state"] == "Completed", result
    assert not original.exists() and kept.exists()
    assert (kept.parent / "poster.jpg").exists() and (kept.parent / "Movie-2160.en.srt").exists()
    rule = add_rule(system)
    result = system.app.state.engine.evaluate(system.db.one("SELECT * FROM media WHERE id=?", (media_id,)), rule)
    assert "This physical version is protected" in result["protections"]
    # Protection remains manageable after the alternative disappeared from Versions.
    detail = system.client.get(f"/api/media/{media_id}").json()
    assert len(detail["files"]) == 1 and detail["files"][0]["version_protected"]
    response = system.client.put(
        "/api/library/version-protection", json={"file_ids": [detail["files"][0]["id"]], "protected": False}
    )
    assert response.status_code == 200
    assert not system.client.get(f"/api/media/{media_id}").json()["files"][0]["version_protected"]


def test_multipart_movie_is_one_version_not_a_duplicate(system):
    media_id, first, item = add_movie(system, path="Movie/part1.mkv")
    second = first.parent / "part2.mkv"
    second.write_bytes(b"part2")
    item["Media"][0]["Part"].append({"id": "part2", "file": "/media/Movie/part2.mkv", "size": 5})
    system.app.state.catalog.save(system.db.one("SELECT * FROM libraries WHERE id='lib'"), item)
    assert system.client.get("/api/versions").json()["items"] == []
    review = preview(system, media_id)
    assert len(review["plans"][0]["files"]) == 2


def test_same_title_without_shared_identity_does_not_merge(system):
    first, _, _ = add_movie(system, key="1")
    second, _, _ = add_movie(system, key="2")
    system.db.execute("UPDATE media SET title='Same title'")
    assert system.client.get("/api/versions").json()["items"] == []
    for media_id in (first, second):
        system.db.execute("INSERT INTO external_ids VALUES(?,?,?)", (media_id, "tmdb", "123"))
    assert len(system.client.get("/api/versions").json()["items"]) == 1


@pytest.mark.parametrize("block", ["protected", "excluded", "foreign_file"])
def test_manual_delete_keeps_global_protections(system, block):
    media_id, physical, _ = add_movie(system)
    ids = None
    if block == "protected":
        system.db.execute("UPDATE media SET protected=1 WHERE id=?", (media_id,))
    elif block == "excluded":
        system.db.execute("UPDATE libraries SET enabled=0")
    else:
        ids = ["not-an-owned-file"]
    response = system.client.post(
        "/api/library/deletion-plans", json={"items": [{"media_id": media_id, **({"file_ids": ids} if ids else {})}]}
    )
    assert response.status_code == 409
    assert physical.exists()


async def test_folder_scope_changes_invalidate_plan(system):
    media_id, physical, item = add_movie(system, path="Movie/movie.mkv")
    fake_integrations(system, {"1": item})
    review = preview(system, media_id)
    (physical.parent / "poster.jpg").write_bytes(b"new unapproved artwork")
    live(system)
    with pytest.raises(SafetyError, match="sidecar scope changed"):
        await execute(system, {**review, "dry_run": False})
    assert physical.exists()


async def test_live_library_delete_requires_exact_confirmation(system):
    media_id, physical, _ = add_movie(system)
    live(system)
    review = preview(system, media_id)
    response = system.client.post(
        "/api/library/deletion-plans/execute",
        json={
            "plans": [{"id": p["id"], "digest": p["digest"]} for p in review["plans"]],
            "confirmation": "",
            "expected_dry_run": review["dry_run"],
        },
    )
    assert response.status_code == 409 and physical.exists()
    assert not system.db.all("SELECT * FROM jobs WHERE kind='cleanup'")


def test_sidecars_count_towards_physical_file_limits(system):
    media_id, physical, _ = add_movie(system, path="Movie/movie.mkv")
    (physical.parent / "movie.en.srt").write_text("subtitle")
    settings = system.db.settings()
    settings["max_files"] = 1
    settings.pop("version")
    system.db.execute("UPDATE settings SET data=?", (encode(settings),))
    response = system.client.post("/api/library/deletion-plans", json={"items": [{"media_id": media_id}]})
    assert response.status_code == 409 and "file limit" in response.text


def test_directory_cleanup_refuses_replacement_symlink_and_root(system):
    _, physical, _ = add_movie(system, path="Movie/movie.mkv")
    directories = system.fs.plan_directories(
        system.app.state.catalog.files(system.db.one("SELECT id FROM media")["id"])
    )
    physical.unlink()
    physical.parent.rmdir()
    outside = system.temp / "outside"
    outside.mkdir()
    physical.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises((SafetyError, OSError)):
        system.fs.remove_empty_directory(directories[0])
    root = system.fs.root(system.root_id)
    with pytest.raises(SafetyError):
        system.fs.remove_empty_directory(
            {"root_id": root["id"], "path": root["path"], "device": root["device"], "inode": root["inode"]}
        )
    assert outside.is_dir() and system.root.is_dir()


def test_schema_upgrade_backs_up_version_four_before_migration(tmp_path):
    import sqlite3

    from deletarr.db import Database

    original = Database(tmp_path / "config")
    with original.transaction() as c:
        c.execute("DROP TABLE manual_requests")
        c.execute("DROP TABLE file_protections")
        c.execute("DELETE FROM schema_version WHERE version=5")
    original.connection.close()
    upgraded = Database(tmp_path / "config")
    assert upgraded.one("SELECT MAX(version) version FROM schema_version")["version"] == 5
    backups = list(upgraded.directory.glob("backup-*.db"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o077 == 0
    with sqlite3.connect(backups[0]) as saved:
        assert saved.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 4
    upgraded.connection.close()


async def test_kept_version_identity_is_rechecked_before_removing_other_copy(system):
    _, original, kept, _ = editions(system)
    group = system.client.get("/api/versions").json()["items"][0]
    version = next(v for v in group["versions"] if v["resolution"] == "4k")
    live(system)
    review = system.client.post(f"/api/versions/{group['id']}/keep", json={"version_id": version["id"]}).json()
    kept.write_bytes(b"changed kept file")
    with pytest.raises(SafetyError, match="Kept version changed"):
        await execute(system, review)
    assert original.exists() and kept.exists()


async def test_new_copy_after_media_delete_preserves_now_shared_sidecars(system, monkeypatch):
    media_id, physical, item = add_movie(system, path="Movie/movie.mkv")
    fake_integrations(system, {"1": item})
    poster = physical.parent / "poster.jpg"
    poster.write_bytes(b"artwork")
    live(system)
    review = preview(system, media_id)
    unlink = system.fs.unlink
    arrived = physical.parent / "movie.mp4"

    def incoming_copy(file):
        unlink(file)
        if file["path"] == str(physical):
            arrived.write_bytes(b"new edition")

    monkeypatch.setattr(system.app.state.executor.fs, "unlink", incoming_copy)
    with pytest.raises(SafetyError, match="requires reconciliation"):
        await execute(system, review)
    assert system.db.one("SELECT state FROM operations")["state"] == "ReconciliationRequired"
    assert arrived.exists() and poster.exists()


async def test_kept_hardlink_is_verified_and_remains_protected(system):
    import os

    media_id, original, kept, item = editions(system)
    kept.unlink()
    os.link(original, kept)
    system.app.state.catalog.save(system.db.one("SELECT * FROM libraries WHERE id='lib'"), item)
    group = system.client.get("/api/versions").json()["items"][0]
    version = next(v for v in group["versions"] if v["resolution"] == "4k")
    live(system)
    review = system.client.post(f"/api/versions/{group['id']}/keep", json={"version_id": version["id"]}).json()
    assert review["estimated_reclaimable"] == 0
    results = await execute(system, review)
    assert results[0]["state"] == "Completed", results
    assert kept.exists() and not original.exists()
    kept_record = system.db.one("SELECT * FROM files WHERE id=?", (version["files"][0]["id"],))
    assert json.loads(kept_record["identity"]) == system.fs.inspect(system.root_id, str(kept))
    system.app.state.executor.refresh_kept_hardlinks(review["plans"][0])  # retry is idempotent


async def test_manual_deletion_blocks_active_playback(system):
    media_id, physical, item = add_movie(system)
    integration = fake_integrations(system, {"1": item})
    integration.plex.active = [{"ratingKey": "1"}]
    live(system)
    with pytest.raises(SafetyError, match="Active Plex playback"):
        await execute(system, preview(system, media_id))
    assert physical.exists()


def test_manual_preview_cannot_be_promoted_to_live_after_review(system):
    media_id, physical, _ = add_movie(system)
    review = preview(system, media_id)
    live(system)
    response = system.client.post(
        "/api/library/deletion-plans/execute",
        json={
            "plans": [{"id": p["id"], "digest": p["digest"]} for p in review["plans"]],
            "confirmation": review["confirmation"],
            "expected_dry_run": True,
        },
    )
    assert response.status_code == 409 and "mode changed" in response.text and physical.exists()


def test_television_versions_use_show_identity_and_episode_numbers(system):
    catalog = system.app.state.catalog
    library = system.db.one("SELECT * FROM libraries WHERE id='lib'")
    catalog.save(library, {"ratingKey": "10", "type": "show", "title": "Series", "addedAt": now() - 400 * 86400})
    catalog.save(
        library,
        {
            "ratingKey": "11",
            "type": "season",
            "title": "Season 1",
            "parentRatingKey": "10",
            "index": 1,
            "addedAt": now() - 400 * 86400,
        },
    )
    for filename in ("e1-1080.mkv", "e1-2160.mkv"):
        (system.root / filename).write_bytes(b"episode")
    catalog.save(
        library,
        {
            "ratingKey": "12",
            "type": "episode",
            "title": "Pilot",
            "parentRatingKey": "11",
            "parentIndex": 1,
            "index": 1,
            "addedAt": now() - 400 * 86400,
            "Media": [
                {"videoResolution": "1080", "Part": [{"id": "101", "file": "/media/e1-1080.mkv"}]},
                {"videoResolution": "4k", "Part": [{"id": "102", "file": "/media/e1-2160.mkv"}]},
            ],
        },
    )
    groups = system.client.get("/api/versions", params={"kind": "episode"}).json()["items"]
    assert len(groups) == 1 and groups[0]["title"] == "Series · S01E01" and len(groups[0]["versions"]) == 2


async def test_batch_manual_selection_obeys_combined_limits(system):
    first, _, _ = add_movie(system, key="1")
    second, _, _ = add_movie(system, key="2")
    settings = system.db.settings()
    settings["max_items"] = 1
    settings.pop("version")
    system.db.execute("UPDATE settings SET data=?", (encode(settings),))
    response = system.client.post(
        "/api/library/deletion-plans", json={"items": [{"media_id": first}, {"media_id": second}]}
    )
    assert response.status_code == 409 and "item or physical file limit" in response.text
    assert not system.db.all("SELECT * FROM operations")


def test_manual_episode_deletion_refuses_shared_file_outside_scope(system):
    first, physical, _ = add_movie(system, key="1")
    second, _, _ = add_movie(system, key="2")
    shared = system.db.one("SELECT file_id FROM media_files WHERE media_id=?", (first,))["file_id"]
    system.db.execute("UPDATE media SET kind='episode'")
    system.db.execute("DELETE FROM media_files WHERE media_id=?", (second,))
    system.db.execute("INSERT INTO media_files VALUES(?,?)", (second, shared))
    response = system.client.post("/api/library/deletion-plans", json={"items": [{"media_id": first}]})
    assert response.status_code == 409 and "Shared file" in response.text and physical.exists()


@pytest.mark.parametrize("partial", [False, True])
async def test_remaining_edition_seerr_availability_is_verified(system, partial):
    import httpx

    from deletarr.integrations import HTTP, Seerr

    media_id, removed, kept, item = editions(system)
    if partial:
        # Two parts of one movie edition are not a complete remaining copy.
        item["Media"] = [{"Part": [edition["Part"][0] for edition in item["Media"]]}]
        system.app.state.catalog.save(system.db.one("SELECT * FROM libraries WHERE id='lib'"), item)
    writes = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "results": []
                    if request.url.path.endswith("request")
                    else [{"id": 44, "mediaType": "movie", "tmdbId": 55, "status": 5}]
                },
            )
        writes.append(request.url.path)
        assert request.method == "POST" and request.url.path == "/api/v1/media/44/available"
        return httpx.Response(200, json={"id": 44, "status": 5})

    adapter = Seerr(HTTP("https://seerr.test", {}, transport=httpx.MockTransport(handle)))
    fake_integrations(system, {"1": item}, {"seerr": adapter})
    system.db.execute("INSERT INTO external_ids VALUES(?,?,?)", (media_id, "tmdb", "55"))
    live(system)
    removed_id = system.db.one("SELECT id FROM files WHERE path=?", (str(removed),))["id"]
    review = preview(system, media_id, [removed_id])
    if partial:
        with pytest.raises(SafetyError, match="requires reconciliation"):
            await execute(system, review)
        assert writes == []
        assert "multipart" in system.db.one("SELECT error FROM operations")["error"]
    else:
        result = await execute(system, review)
        assert result[0]["state"] == "Completed" and writes == ["/api/v1/media/44/available"]
    assert not removed.exists() and kept.exists()
