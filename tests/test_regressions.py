"""Repository-review regressions use disposable files and synthetic integrations only."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from conftest import add_movie, add_rule, candidate, fake_integrations
from pydantic import ValidationError

from deletarr.app import PressureInput
from deletarr.db import encode, now, uid
from deletarr.rules import Condition
from deletarr.security import SafetyError


async def test_pressure_job_executes_multiple_plans_without_losing_target(system, monkeypatch):
    items = {}
    for key in ("1", "2"):
        _, _, item = add_movie(system, key=key)
        items[key] = item
    fake_integrations(system, items)
    add_rule(system, action="automatic")
    monkeypatch.setattr(
        "deletarr.storage.os.statvfs",
        lambda path: SimpleNamespace(f_blocks=100_000, f_bavail=1, f_frsize=4096),
    )
    system.db.execute(
        "INSERT INTO pressure_policies VALUES(?,?,?,?,?,?,?,?)",
        (uid(), "Test recovery", system.root_id, 10_000, 20_000, "bytes", "exclude", 1),
    )
    worker = system.app.state.worker
    job = worker.enqueue("pressure")
    await worker.dispatch(system.db.one("SELECT * FROM jobs WHERE id=?", (job,)))
    operations = system.db.all("SELECT * FROM operations")
    assert len(operations) == 2
    assert all(op["state"] == "Completed" and op["dry_run"] for op in operations)
    assert all((system.root / f"movie-{key}.mkv").exists() for key in items)


def test_concurrent_enqueue_returns_one_durable_job(system, monkeypatch):
    start = Barrier(8)
    lookup = Barrier(8)
    original_one = system.db.one

    def simultaneous_lookup(sql, args=()):
        result = original_one(sql, args)
        if sql.startswith("SELECT id FROM jobs WHERE dedupe="):
            lookup.wait(timeout=5)
        return result

    # Expose the old read/insert race deterministically. Transactional lookup
    # correctly uses its held SQLite connection instead of this split read.
    monkeypatch.setattr(system.db, "one", simultaneous_lookup)

    def submit(_):
        start.wait(timeout=5)
        return system.app.state.worker.enqueue("maintenance")

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(submit, range(8)))
    assert len(set(jobs)) == 1
    assert len(system.db.all("SELECT * FROM jobs")) == 1


@pytest.mark.parametrize("kind", ["cleanup", "pressure"])
async def test_manually_queued_simulation_cannot_become_live(system, kind):
    add_movie(system)
    candidate(system)
    response = system.client.post("/api/jobs", json={"kind": kind})
    assert response.status_code == 200
    job = system.db.one("SELECT * FROM jobs WHERE id=?", (response.json()["id"],))
    settings = system.db.settings()
    settings.update(dry_run=False, live_enrolled=True)
    settings.pop("version")
    system.db.execute("UPDATE settings SET data=?", (encode(settings),))
    with pytest.raises(SafetyError, match="mode changed"):
        await system.app.state.worker.dispatch(job)
    assert not system.db.all("SELECT * FROM operations")
    assert (system.root / "movie-1.mkv").exists()


def test_keep_forever_also_protects_an_already_protected_candidate(system):
    media_id, _, _ = add_movie(system)
    row = candidate(system)
    system.db.execute("UPDATE candidates SET state='Protected' WHERE id=?", (row["id"],))
    response = system.client.post(
        f"/api/candidates/{row['id']}/action", json={"version": row["version"], "action": "keep"}
    )
    assert response.status_code == 200
    assert system.db.one("SELECT protected FROM media WHERE id=?", (media_id,))["protected"] == 1
    system.app.state.engine.run(persist=True)
    assert system.db.one("SELECT state FROM candidates WHERE id=?", (row["id"],))["state"] == "Protected"


def interrupted_absent_file(system, nested=False):
    _, physical, item = add_movie(system, path="movie/file.mkv" if nested else None)
    row = candidate(system)
    fake_integrations(system, {"1": item})
    executor = system.app.state.executor
    plan = executor.build(row["id"])
    operation = uid()
    system.db.execute(
        "INSERT INTO operations(id,plan_id,idempotency_key,state,dry_run,error,created_at,updated_at) VALUES(?,?,?,'ReconciliationRequired',0,?,?,?)",
        (operation, plan["id"], uid(), "Temporary synchronization failure", now(), now()),
    )
    system.db.execute("UPDATE candidates SET state='ReconciliationRequired' WHERE id=?", (row["id"],))
    system.db.execute(
        "INSERT INTO reconciliation VALUES(?,?,'pending',?,?)", (uid(), operation, "Test recovery", now())
    )
    executor.checkpoint(operation, "file:" + plan["files"][0]["id"], "started")
    physical.unlink()
    if nested:
        physical.parent.rmdir()
    return operation, row


async def test_reconcile_handles_removed_parent_and_clears_recovered_error(system):
    operation, row = interrupted_absent_file(system, nested=True)
    await system.app.state.executor.reconcile()
    recovered = system.db.one("SELECT * FROM operations WHERE id=?", (operation,))
    assert recovered["state"] == "Completed"
    assert recovered["error"] is None
    assert system.db.one("SELECT state FROM candidates WHERE id=?", (row["id"],))["state"] == "Completed"
    assert system.db.one("SELECT state FROM reconciliation")["state"] == "resolved"


async def test_unexpected_sync_failure_can_be_reconciled_again(system, monkeypatch):
    operation, row = interrupted_absent_file(system)
    executor = system.app.state.executor
    synchronize = executor.synchronize

    async def broken_sync(*args):
        raise RuntimeError("test-only-private-upstream-payload")

    monkeypatch.setattr(executor, "synchronize", broken_sync)
    await executor.reconcile()
    assert system.db.one("SELECT state FROM candidates WHERE id=?", (row["id"],))["state"] == "ReconciliationRequired"
    stored = system.db.one("SELECT * FROM operations WHERE id=?", (operation,))
    assert stored["state"] == "ReconciliationRequired"
    assert "test-only-private" not in stored["error"]
    monkeypatch.setattr(executor, "synchronize", synchronize)
    await executor.reconcile()
    assert system.db.one("SELECT state FROM operations WHERE id=?", (operation,))["state"] == "Completed"


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_rule_values_are_rejected(value):
    with pytest.raises(ValidationError):
        Condition(field="added_days", operator="gte", value=value)


def test_nonfinite_storage_target_is_rejected():
    with pytest.raises(ValidationError):
        PressureInput(name="Invalid target", root_id="test-root", threshold=1, target=float("inf"))


def test_show_and_season_sizes_count_shared_physical_file_once(system):
    catalog = system.app.state.catalog
    library = system.db.one("SELECT * FROM libraries WHERE id='lib'")
    show = catalog.save(library, {"ratingKey": "10", "type": "show", "title": "Shared show"})
    season = catalog.save(library, {"ratingKey": "11", "type": "season", "title": "Season", "parentRatingKey": "10"})
    (system.root / "shared.mkv").write_bytes(b"x" * 8192)
    for key in ("12", "13"):
        catalog.save(
            library,
            {
                "ratingKey": key,
                "type": "episode",
                "title": "Episode",
                "parentRatingKey": "11",
                "Media": [{"Part": [{"id": "shared-part", "file": "/media/shared.mkv", "size": 8192}]}],
            },
        )
    for kind, media_id in [("show", show), ("season", season)]:
        rows = system.client.get("/api/media", params={"kind": kind}).json()["items"]
        assert next(row for row in rows if row["id"] == media_id)["size"] == 8192


async def test_mode_change_during_validation_does_not_promote_simulation(system, monkeypatch):
    _, physical, item = add_movie(system)
    row = candidate(system)
    fake_integrations(system, {"1": item})
    executor = system.app.state.executor
    plan = executor.build(row["id"])
    validate = executor.validate

    async def changed_during_network_validation(saved_plan):
        snapshot = await validate(saved_plan)
        settings = system.db.settings()
        settings.update(dry_run=False, live_enrolled=True)
        settings.pop("version")
        system.db.execute("UPDATE settings SET data=?", (encode(settings),))
        return snapshot

    monkeypatch.setattr(executor, "validate", changed_during_network_validation)
    with pytest.raises(SafetyError, match="mode changed"):
        await executor.execute(plan["id"], plan["digest"], "test-mode-change", expected_dry_run=True)
    assert physical.exists()
    assert not system.db.all("SELECT * FROM operations")


async def test_running_job_cancellation_is_not_reported_as_completed(system, monkeypatch):
    worker = system.app.state.worker
    job_id = worker.enqueue("maintenance")
    system.db.execute("UPDATE jobs SET available_at=? WHERE id=?", (now() - 1, job_id))

    async def stop_requested(job):
        system.db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job["id"],))
        worker.stop.set()

    monkeypatch.setattr(worker, "dispatch", stop_requested)
    await worker.run()
    job = system.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert job["state"] == "cancelled" and job["finished_at"]


def test_dashboard_storage_excludes_detached_file_records(system):
    _, original, item = add_movie(system)
    replacement = system.root / "replacement.mkv"
    replacement.write_bytes(b"r" * 16384)
    item["Media"] = [{"Part": [{"id": "replacement", "file": "/media/replacement.mkv", "size": 16384}]}]
    system.app.state.catalog.save(system.db.one("SELECT * FROM libraries WHERE id='lib'"), item)
    dashboard = system.client.get("/api/dashboard").json()
    assert dashboard["files"] == 1
    assert dashboard["logical_bytes"] == 16384
    assert original.exists()  # Catalog changes never authorize removal of the old file.
    assert len(system.db.all("SELECT * FROM files")) == 2  # Historical identity remains intact.


async def test_reconciliation_holds_executor_lock_against_manual_resolution(system, monkeypatch):
    operation, _ = interrupted_absent_file(system)
    executor = system.app.state.executor
    synchronize = executor.synchronize
    task = system.db.one("SELECT id FROM reconciliation WHERE operation_id=?", (operation,))

    async def verify_lock(operation_id, snapshot):
        assert executor.lock.locked()
        response = system.client.post(
            f"/api/reconciliation/{task['id']}/resolve",
            json={
                "confirmation": "I VERIFIED THE EXTERNAL STATE AND ACCEPT THIS RESOLUTION",
                "explanation": "Synthetic concurrent resolution while recovery is in progress.",
            },
        )
        assert response.status_code == 409
        await synchronize(operation_id, snapshot)

    monkeypatch.setattr(executor, "synchronize", verify_lock)
    await executor.reconcile()
    assert system.db.one("SELECT state FROM operations WHERE id=?", (operation,))["state"] == "Completed"
