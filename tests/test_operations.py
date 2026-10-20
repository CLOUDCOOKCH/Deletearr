import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from conftest import add_movie, add_rule, candidate

from deletarr.db import Database, encode, now, uid
from deletarr.security import SafetyError
from deletarr.storage import StoragePlanner


def test_scheduler_cron_timezone_and_interval(system):
    client = system.client
    response = client.post(
        "/api/schedules",
        json={"name": "Nightly", "kind": "full_sync", "cron": "0 3 * * *", "timezone": "Europe/Zurich"},
    )
    assert response.status_code == 200, response.text
    row = system.db.one("SELECT * FROM schedules")
    assert datetime.fromtimestamp(row["next_at"], ZoneInfo("Europe/Zurich")).hour == 3
    response = client.post(
        "/api/schedules", json={"name": "Every hour", "kind": "health", "cron": "* * * * *", "interval_seconds": 3600}
    )
    assert response.status_code == 200
    assert client.post("/api/schedules", json={"name": "bad", "kind": "health", "cron": "invalid"}).status_code == 409
    system.db.execute("UPDATE schedules SET next_at=0")
    system.app.state.worker.schedule()
    assert len(system.db.all("SELECT * FROM jobs")) == 2
    system.app.state.worker.schedule()
    assert len(system.db.all("SELECT * FROM jobs")) == 2


def test_queue_deduplication_cancellation_and_persistence(system):
    worker = system.app.state.worker
    first = worker.enqueue("maintenance")
    assert worker.enqueue("maintenance") == first
    assert system.client.post("/api/jobs/" + first + "/cancel", json={}).status_code == 200
    assert system.db.one("SELECT cancel_requested FROM jobs WHERE id=?", (first,))["cancel_requested"] == 1
    second = Database(system.db.directory)
    assert second.one("SELECT id FROM jobs WHERE id=?", (first,))
    second.connection.close()


def test_settings_backup_consistency_and_export(system):
    response = system.client.post("/api/backup", json={})
    assert response.status_code == 200
    path = system.db.directory / response.json()["filename"]
    with sqlite3.connect(path) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT username FROM administrator").fetchone()[0] == "admin"
    exported = system.client.get("/api/settings/export").json()
    assert exported["settings"]["dry_run"] and not exported["settings"]["live_enrolled"]
    assert "long-local-password" not in json.dumps(exported)
    assert system.client.get("/api/diagnostics").json()["database"] == "ok"


async def test_restart_never_replays_destructive_jobs(system):
    _, _, _ = add_movie(system)
    row = candidate(system)
    plan = system.app.state.executor.build(row["id"])
    db = system.db
    db.execute(
        "INSERT INTO operations(id,plan_id,idempotency_key,state,dry_run,created_at,updated_at) VALUES(?,?,?,?,0,?,?)",
        ("op", plan["id"], "interrupted", "Executing", now(), now()),
    )
    db.execute("UPDATE candidates SET state='Executing'")
    worker = system.app.state.worker
    job = worker.enqueue("cleanup")
    db.execute("UPDATE jobs SET state='running' WHERE id=?", (job,))
    worker.start()
    await worker.shutdown()
    assert db.one("SELECT state FROM operations")["state"] == "ReconciliationRequired"
    assert db.one("SELECT state FROM candidates")["state"] == "ReconciliationRequired"
    assert db.one("SELECT state FROM jobs WHERE id=?", (job,))["state"] == "failed"


def test_rolling_budget_cannot_be_bypassed_by_manual_jobs(system):
    add_movie(system, key="1")
    row = candidate(system)
    plan = system.app.state.executor.build(row["id"])
    system.db.execute(
        "INSERT INTO operations(id,plan_id,idempotency_key,state,dry_run,created_at,updated_at) VALUES(?,?,?,?,0,?,?)",
        (uid(), plan["id"], "first", "Completed", now(), now()),
    )
    settings = system.db.settings()
    settings["max_items"] = 1
    settings.pop("version")
    system.db.execute("UPDATE settings SET data=?", (encode(settings),))
    add_movie(system, key="2")
    system.app.state.engine.run(persist=True)
    other = system.db.one("SELECT c.* FROM candidates c JOIN media m ON m.id=c.media_id WHERE m.plex_key='2'")
    with pytest.raises(SafetyError, match="limit"):
        system.app.state.executor.build(other["id"])


@pytest.mark.parametrize("setting", ["max_logical_bytes", "max_reclaimable_bytes"])
def test_byte_limit_reports_exact_totals_and_both_configured_limits(system, setting):
    add_movie(system)
    row = candidate(system)
    executor = system.app.state.executor
    snapshot = executor.build(row["id"])
    settings = system.db.settings()
    settings[setting] = 1
    settings.pop("version")
    system.db.execute("UPDATE settings SET data=?", (encode(settings),))
    with pytest.raises(SafetyError, match="Global byte limit exceeded") as error:
        executor.limits([snapshot])
    message = str(error.value)
    assert "Logical total: 8,192 bytes" in message
    assert "Estimated reclaimable total:" in message
    assert "limit: 1 bytes" in message
    assert "last 24 hours" in message
    assert "all ready candidates together" in message
    assert not system.db.all("SELECT * FROM operations")


def test_combined_ready_candidates_can_exceed_limit_even_when_each_fits(system):
    add_movie(system, key="1")
    add_movie(system, key="2")
    rule = add_rule(system)
    system.app.state.engine.run(persist=True)
    executor = system.app.state.executor
    plans = [
        executor.build(row["id"]) for row in system.db.all("SELECT * FROM candidates WHERE rule_id=?", (rule["id"],))
    ]
    settings = system.db.settings()
    settings["max_logical_bytes"] = 10_000
    settings.pop("version")
    system.db.execute("UPDATE settings SET data=?", (encode(settings),))
    for plan in plans:
        executor.limits([plan])
    with pytest.raises(SafetyError, match="Logical total: 16,384 bytes"):
        executor.limits(plans)


def test_pressure_target_orders_oldest_and_respects_hardlinks(system, monkeypatch):
    a, _, _ = add_movie(system, key="1")
    b, _, _ = add_movie(system, key="2")
    system.db.execute("UPDATE media SET last_watched=? WHERE id=?", (now() - 200 * 86400, b))
    add_rule(system, action="automatic")
    monkeypatch.setattr(
        "deletarr.storage.os.statvfs", lambda path: SimpleNamespace(f_blocks=100000, f_bavail=1, f_frsize=4096)
    )
    planner = StoragePlanner(
        system.db, system.app.state.catalog, system.app.state.engine, system.app.state.executor, system.fs
    )
    result = planner.select(
        {"root_id": system.root_id, "threshold": 10000, "target": 12000, "unit": "bytes", "never_order": "exclude"}
    )
    assert result["under_pressure"] and result["target_achievable"]
    assert result["plans"][0]["media_id"] == b
    assert len(result["plans"]) == 1


def test_notification_persistence_and_dedup(system):
    notifications = system.app.state.executor.notifications
    notifications.emit("cleanup_completed", "Done")
    notifications.emit("cleanup_completed", "Done")
    assert len(system.client.get("/api/notifications").json()) == 1
    ident = system.db.one("SELECT id FROM notifications")["id"]
    assert system.client.post("/api/notifications/" + ident + "/dismiss", json={}).status_code == 200
    assert system.client.get("/api/notifications").json() == []


def test_manual_reconciliation_refuses_existing_physical_media(system):
    from conftest import add_movie, candidate

    from deletarr.db import now, uid

    add_movie(system)
    row = candidate(system)
    plan = system.app.state.executor.build(row["id"])
    op, task = uid(), uid()
    system.db.execute(
        "INSERT INTO operations(id,plan_id,idempotency_key,state,dry_run,created_at,updated_at) VALUES(?,?,?,?,0,?,?)",
        (op, plan["id"], uid(), "ReconciliationRequired", now(), now()),
    )
    system.db.execute("INSERT INTO reconciliation VALUES(?,?,?,?,?)", (task, op, "pending", "Test interruption", now()))
    response = system.client.post(
        "/api/reconciliation/" + task + "/resolve",
        json={
            "explanation": "Externally checked integration state for this test operation.",
            "confirmation": "I VERIFIED THE EXTERNAL STATE AND ACCEPT THIS RESOLUTION",
        },
    )
    assert response.status_code == 409
    assert system.db.one("SELECT state FROM reconciliation")["state"] == "pending"


def test_pressure_never_orders_unknown_history_as_never_watched(system, monkeypatch):
    from types import SimpleNamespace

    from conftest import add_movie, add_rule

    from deletarr.storage import StoragePlanner

    add_movie(system, watched=False)
    add_rule(system, action="automatic", conditions={"field": "added_days", "operator": "gte", "value": 30})
    monkeypatch.setattr(
        "deletarr.storage.os.statvfs", lambda path: SimpleNamespace(f_blocks=100000, f_bavail=1, f_frsize=4096)
    )
    planner = StoragePlanner(
        system.db, system.app.state.catalog, system.app.state.engine, system.app.state.executor, system.fs
    )
    result = planner.select(
        {"root_id": system.root_id, "threshold": 10000, "target": 20000, "unit": "bytes", "never_order": "oldest_added"}
    )
    assert result["plans"] == []
    assert any("uncertain" in b["reason"] for b in result["blocked"])


async def test_mapping_suggestions_are_read_only_and_require_evidence(system):
    from conftest import FakeIntegrations, FakePlex, add_movie

    from deletarr.storage import mapping_suggestions

    add_movie(system)
    system.db.execute("DELETE FROM mappings")
    plex = FakePlex({})

    async def libraries():
        return [{"Location": [{"path": "/media"}]}]

    plex.libraries = libraries
    integrations = FakeIntegrations(plex, {"plex": plex})
    before = system.db.all("SELECT * FROM mappings")
    result = await mapping_suggestions(system.db, integrations, system.fs)
    assert result["requires_manual_confirmation"]
    assert len(result["suggestions"]) == 1
    assert result["suggestions"][0]["source"] == "plex"
    assert result["suggestions"][0]["matched_samples"] == [str(system.root / "movie-1.mkv")]
    assert before == system.db.all("SELECT * FROM mappings")
