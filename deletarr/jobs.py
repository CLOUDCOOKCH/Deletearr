import asyncio
import fcntl
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from .db import encode, now, uid
from .integrations import HTTP
from .security import SafetyError

KINDS = {
    "full_sync",
    "incremental_sync",
    "watch_history",
    "health",
    "evaluate",
    "storage",
    "cleanup",
    "reconcile",
    "notifications",
    "maintenance",
    "pressure",
}


class Notifications:
    def __init__(self, db, vault):
        self.db, self.vault = db, vault

    def emit(self, event, message):
        if self.db.one(
            "SELECT id FROM notifications WHERE event=? AND message=? AND created_at>?", (event, message, now() - 3600)
        ):
            return
        ident = uid()
        self.db.execute("INSERT INTO notifications VALUES(?,?,?,?,0)", (ident, event, message, now()))
        for dest in self.db.all("SELECT * FROM destinations WHERE enabled=1"):
            if event in json.loads(dest["events"]):
                self.db.execute(
                    "INSERT INTO deliveries(id,notification_id,destination_id,state) VALUES(?,?,?,?)",
                    (uid(), ident, dest["id"], "pending"),
                )

    async def send(self, destination, message, event="test"):
        message = message[:1800]
        config = json.loads(self.vault.decrypt(destination["config"]))
        url = config["url"]
        body = {"event": event, "message": message, "application": "Deletarr"}
        path = ""
        if destination["kind"] == "discord":
            body = {"content": "Deletarr: " + message, "allowed_mentions": {"parse": []}}
        elif destination["kind"] == "telegram":
            url = "https://api.telegram.org"
            path = "/bot" + config["token"] + "/sendMessage"
            body = {"chat_id": config["chat_id"], "text": "Deletarr: " + message}
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        if not path:
            path = parsed.path or "/"
        http = HTTP(f"{parsed.scheme}://{parsed.netloc}", {}, config.get("allow_private", False))
        result = await http.request(
            "POST",
            path,
            json=body,
            params={"wait": "true"} if destination["kind"] == "discord" else None,
            allow_text=destination["kind"] == "webhook",
        )
        if result is None:
            raise SafetyError("Notification destination not found")
        if destination["kind"] == "telegram" and result.get("ok") is not True:
            raise SafetyError("Telegram did not confirm notification delivery")
        if destination["kind"] == "discord" and not result.get("id"):
            raise SafetyError("Discord did not confirm a saved message")

    async def deliver(self):
        for delivery in self.db.all(
            "SELECT * FROM deliveries WHERE state='pending' AND attempts<5 AND available_at<=?", (now(),)
        ):
            notification = self.db.one("SELECT * FROM notifications WHERE id=?", (delivery["notification_id"],))
            destination = self.db.one("SELECT * FROM destinations WHERE id=?", (delivery["destination_id"],))
            if not destination or not destination["enabled"]:
                continue
            try:
                await self.send(destination, notification["message"], notification["event"])
                self.db.execute(
                    "UPDATE deliveries SET state='delivered',attempts=attempts+1,error=NULL WHERE id=?",
                    (delivery["id"],),
                )
            except SafetyError:
                self.db.execute(
                    "UPDATE deliveries SET attempts=attempts+1,available_at=?,state=CASE WHEN attempts>=4 THEN 'failed' ELSE 'pending' END,error='Delivery failed; destination or connectivity error' WHERE id=?",
                    (now() + 60 * 2 ** delivery["attempts"], delivery["id"]),
                )


class Worker:
    def __init__(self, db, catalog, engine, executor, integrations, filesystem, notifications):
        self.db, self.catalog, self.engine, self.executor = db, catalog, engine, executor
        self.integrations, self.fs, self.notifications = integrations, filesystem, notifications
        self.stop = asyncio.Event()
        self.task = None
        self.instance_lock = None

    def enqueue(self, kind, payload=None, dedupe=None):
        if kind not in KINDS:
            raise SafetyError("Unsupported job kind")
        dedupe = dedupe or kind
        existing = self.db.one("SELECT id FROM jobs WHERE dedupe=? AND state IN ('queued','running')", (dedupe,))
        if existing:
            return existing["id"]
        ident = uid()
        self.db.execute(
            "INSERT INTO jobs(id,kind,payload,state,dedupe,available_at) VALUES(?,?,?,?,?,?)",
            (ident, kind, encode(payload or {}), "queued", dedupe, now()),
        )
        return ident

    def start(self):
        self.instance_lock = open(self.db.directory / "executor.lock", "a+")
        try:
            fcntl.flock(self.instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SafetyError("Only one Deletarr process may use this configuration directory") from exc
        self.db.execute(
            "UPDATE jobs SET state=CASE WHEN kind IN ('cleanup','pressure') THEN 'failed' ELSE 'queued' END,error='Interrupted by process restart' WHERE state='running'"
        )
        # Interrupted physical work always enters reconciliation; no destructive job is replayed.
        for operation in self.db.all(
            "SELECT * FROM operations WHERE state IN ('Validating','Executing','Synchronizing') AND dry_run=0"
        ):
            self.db.execute(
                "UPDATE operations SET state='ReconciliationRequired',error='Process interrupted' WHERE id=?",
                (operation["id"],),
            )
            plan = self.db.one("SELECT * FROM plans WHERE id=?", (operation["plan_id"],))
            self.db.execute(
                "UPDATE candidates SET state='ReconciliationRequired',version=version+1 WHERE id=?",
                (plan["candidate_id"],),
            )
            self.db.execute(
                "INSERT INTO reconciliation VALUES(?,?,?,?,?)",
                (uid(), operation["id"], "pending", "Process interrupted", now()),
            )
        self.db.execute(
            "UPDATE candidates SET state='Ready',version=version+1 WHERE state='Validating' AND id NOT IN (SELECT candidate_id FROM plans p JOIN operations o ON o.plan_id=p.id WHERE o.dry_run=0 AND o.state='ReconciliationRequired')"
        )
        self.task = asyncio.create_task(self.run())

    async def shutdown(self):
        self.executor.stopping = True
        self.stop.set()
        if self.task:
            await self.task
        if self.instance_lock:
            self.instance_lock.close()

    def schedule(self):
        with self.db.transaction() as c:
            for row in c.execute("SELECT * FROM schedules WHERE enabled=1 AND next_at<=?", (now(),)).fetchall():
                next_at = (
                    now() + row["interval_seconds"]
                    if row["interval_seconds"]
                    else int(
                        croniter(row["cron"], datetime.fromtimestamp(now(), ZoneInfo(row["timezone"])))
                        .get_next(datetime)
                        .timestamp()
                    )
                )
                existing = c.execute(
                    "SELECT id FROM jobs WHERE dedupe=? AND state IN ('queued','running')", (row["kind"],)
                ).fetchone()
                if not existing:
                    c.execute(
                        "INSERT INTO jobs(id,kind,payload,state,dedupe,available_at) VALUES(?,?,?,?,?,?)",
                        (uid(), row["kind"], row["payload"], "queued", row["kind"], now()),
                    )
                c.execute("UPDATE schedules SET next_at=? WHERE id=?", (next_at, row["id"]))

    async def dispatch(self, job):
        kind = job["kind"]
        if kind in {"full_sync", "incremental_sync"}:
            await self.catalog.sync(kind == "incremental_sync")
        elif kind == "watch_history":
            await self.catalog.watch_history()
        elif kind == "health":
            for integration in self.db.all("SELECT kind FROM integrations"):
                try:
                    await self.integrations.health(integration["kind"])
                except SafetyError:
                    self.notifications.emit(
                        "integration_disconnected", integration["kind"].capitalize() + " disconnected"
                    )
        elif kind == "evaluate":
            results = await asyncio.to_thread(self.engine.run, persist=True)
            count = sum(x["eligible"] for x in results)
            if count:
                self.notifications.emit(
                    "approval_required", f"{count} eligible media items evaluated. Review the cleanup queue."
                )
            for candidate in self.db.all(
                "SELECT c.*,m.title FROM candidates c JOIN media m ON m.id=c.media_id WHERE state='GracePeriod'"
            ):
                self.notifications.emit(
                    "upcoming_deletion",
                    f"{candidate['title']}: grace period expires at UTC timestamp {candidate['due_at']}",
                )
        elif kind == "storage":
            self.fs.snapshot()
        elif kind == "pressure":
            from .storage import StoragePlanner

            planner = StoragePlanner(self.db, self.catalog, self.engine, self.executor, self.fs)
            payload = json.loads(job["payload"])
            for policy in self.db.all("SELECT * FROM pressure_policies WHERE enabled=1"):
                if payload.get("policy_id") and payload["policy_id"] != policy["id"]:
                    continue
                result = await asyncio.to_thread(planner.select, policy)
                if result["under_pressure"]:
                    self.notifications.emit(
                        "storage_below_threshold",
                        f"{policy['name']}: storage below threshold; {len(result['plans'])} retention plans selected",
                    )
                for plan in result["plans"]:
                    candidate = self.db.one("SELECT * FROM candidates WHERE id=?", (plan["candidate_id"],))
                    try:
                        self.executor.authorized(candidate)
                    except SafetyError:
                        continue
                    if (
                        self.stop.is_set()
                        or self.db.one("SELECT cancel_requested FROM jobs WHERE id=?", (job["id"],))["cancel_requested"]
                    ):
                        break
                    import os

                    root = self.fs.root(policy["root_id"])
                    current = os.statvfs(root["path"])
                    if current.f_bavail * current.f_frsize >= result["target_bytes"]:
                        break
                    result = await self.executor.execute(
                        plan["id"], plan["digest"], job["id"] + ":" + plan["id"], job_id=job["id"]
                    )
                    if result["state"] != "Completed":
                        raise SafetyError("Pressure cleanup requires reconciliation; remaining work stopped")
        elif kind == "cleanup":
            payload = json.loads(job["payload"])
            if payload.get("plan_id"):
                if payload.get("expected_dry_run") != self.db.settings()["dry_run"]:
                    raise SafetyError("Deletion mode changed after submission; review and submit again")
                result = await self.executor.execute(payload["plan_id"], payload["digest"], job["id"], job_id=job["id"])
                if result["state"] != "Completed":
                    raise SafetyError("Cleanup operation requires reconciliation; inspect its checkpoints")
            else:
                plans = []
                for candidate in self.db.all(
                    "SELECT * FROM candidates WHERE state='Ready' OR (state='GracePeriod' AND due_at<=?)", (now(),)
                ):
                    if self.stop.is_set():
                        break
                    plans.append(self.executor.build(candidate["id"]))
                self.executor.limits(plans)
                for plan in plans:
                    cancelled = self.db.one("SELECT cancel_requested FROM jobs WHERE id=?", (job["id"],))
                    if self.stop.is_set() or cancelled["cancel_requested"]:
                        break
                    result = await self.executor.execute(
                        plan["id"], plan["digest"], job["id"] + ":" + plan["id"], job_id=job["id"]
                    )
                    if result["state"] != "Completed":
                        raise SafetyError("Cleanup operation requires reconciliation; remaining work stopped")
        elif kind == "reconcile":
            await self.executor.reconcile()
        elif kind == "notifications":
            await self.notifications.deliver()
        elif kind == "maintenance":
            self.db.execute("DELETE FROM sessions WHERE expires<?", (now(),))
            self.db.execute("DELETE FROM auth_pins WHERE expires<?", (now(),))
            with self.db.lock:
                self.db.connection.execute("PRAGMA wal_checkpoint(PASSIVE)")

    async def run(self):
        last_notifications = 0
        while not self.stop.is_set():
            self.schedule()
            if now() - last_notifications >= 60:
                self.enqueue("notifications")
                last_notifications = now()
            with self.db.transaction() as c:
                job = c.execute(
                    "SELECT * FROM jobs WHERE state='queued' AND available_at<=? ORDER BY available_at,id LIMIT 1",
                    (now(),),
                ).fetchone()
                if job:
                    if job["cancel_requested"]:
                        c.execute("UPDATE jobs SET state='cancelled',finished_at=? WHERE id=?", (now(), job["id"]))
                        job = None
                    else:
                        c.execute(
                            "UPDATE jobs SET state='running',attempts=attempts+1,started_at=? WHERE id=?",
                            (now(), job["id"]),
                        )
            if job:
                try:
                    await self.dispatch(dict(job))
                    self.db.execute(
                        "UPDATE jobs SET state='completed',finished_at=?,error=NULL WHERE id=?", (now(), job["id"])
                    )
                except Exception as exc:
                    message = (
                        str(exc)
                        if isinstance(exc, SafetyError)
                        else "Unexpected job failure; see diagnostics and operation checkpoints"
                    )
                    retry = job["kind"] not in {"cleanup", "pressure"} and job["attempts"] < 3
                    self.db.execute(
                        "UPDATE jobs SET state=?,available_at=?,finished_at=?,error=? WHERE id=?",
                        ("queued" if retry else "failed", now() + 30 * 2 ** job["attempts"], now(), message, job["id"]),
                    )
                    self.notifications.emit("cleanup_failed" if job["kind"] == "cleanup" else "job_failed", message)
                    if job["kind"] in {"cleanup", "pressure"} and isinstance(exc, SafetyError):
                        self.notifications.emit("deletion_blocked", message)
                    self.db.audit("job.failed", job["id"], {"kind": job["kind"], "error": message}, actor="scheduler")
            else:
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=1)
                except TimeoutError:
                    pass
