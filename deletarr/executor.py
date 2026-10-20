"""Persisted, exact-scope deletion saga. A checkpoint marked started is never blindly replayed."""

import asyncio
import hashlib
import json
import os

from .db import encode, now, uid
from .security import SafetyError

STATES = {
    "Discovered": {"Eligible", "Protected"},
    "Eligible": {"PendingApproval", "GracePeriod", "Ready", "Protected"},
    "PendingApproval": {"Ready", "Rejected", "Postponed", "Protected"},
    "GracePeriod": {"Ready", "Rejected", "Postponed", "Protected"},
    "Scheduled": {"Ready", "Rejected", "Postponed", "Protected"},
    "Ready": {"Validating", "Rejected", "Postponed", "Protected"},
    "Validating": {"Executing", "Ready", "Protected", "Failed"},
    "Executing": {"Synchronizing", "ReconciliationRequired", "Failed"},
    "Synchronizing": {"Completed", "ReconciliationRequired"},
    "ReconciliationRequired": {"Synchronizing", "Completed", "Failed"},
    "Postponed": {"PendingApproval", "Ready", "Protected", "Rejected"},
    "Protected": {"PendingApproval", "GracePeriod", "Ready"},
    "Failed": {"PendingApproval", "Ready"},
    "Completed": set(),
    "Rejected": set(),
}


class Executor:
    def __init__(self, db, catalog, engine, integrations, filesystem, notifications):
        self.db, self.catalog, self.engine, self.integrations, self.fs = db, catalog, engine, integrations, filesystem
        self.notifications = notifications
        self.lock = asyncio.Lock()
        self.stopping = False

    def integration_fingerprint(self):
        return hashlib.sha256(
            encode(
                self.db.all("SELECT kind,url,secret,allow_private,timeout,server_id FROM integrations ORDER BY kind")
            ).encode()
        ).hexdigest()

    def transition(self, candidate_id, target, detail=None, expected_version=None):
        with self.db.transaction() as c:
            item = c.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
            if item and expected_version is not None and item["version"] != expected_version:
                raise SafetyError("Candidate changed; reload before acting")
            if not item or target not in STATES.get(item["state"], set()):
                raise SafetyError(f"Invalid candidate transition to {target}")
            c.execute("UPDATE candidates SET state=?,version=version+1 WHERE id=?", (target, candidate_id))
            c.execute(
                "INSERT INTO audit VALUES(?,?,?,?,?,?)",
                (
                    uid(),
                    now(),
                    "executor",
                    "candidate." + target,
                    candidate_id,
                    encode({"previous": item["state"], **(detail or {})}),
                ),
            )

    def candidate(self, candidate_id):
        candidate = self.db.one("SELECT * FROM candidates WHERE id=?", (candidate_id,))
        if not candidate:
            raise SafetyError("Candidate not found")
        item = self.db.one("SELECT * FROM media WHERE id=?", (candidate["media_id"],))
        rule = self.db.one("SELECT * FROM rules WHERE id=?", (candidate["rule_id"],))
        if not rule or rule["version"] != candidate["rule_version"]:
            raise SafetyError("Rule changed; evaluate and approve again")
        return candidate, item, rule

    def authorized(self, candidate):
        if candidate["postponed_until"] and candidate["postponed_until"] > now():
            raise SafetyError("Candidate is postponed")
        if candidate["state"] not in {"Ready", "GracePeriod", "Validating"}:
            raise SafetyError("Candidate has not been authorized")
        if candidate["due_at"] and candidate["due_at"] > now():
            raise SafetyError("Grace period has not expired")

    def limits(self, snapshots):
        settings = self.db.settings()
        # A rolling safety budget also covers separately submitted manual jobs.
        # Count attempted live scopes conservatively, including uncertain outcomes.
        snapshots = list(snapshots) + [
            json.loads(row["snapshot"])
            for row in self.db.all(
                "SELECT p.snapshot FROM operations o JOIN plans p ON p.id=o.plan_id "
                "WHERE o.dry_run=0 AND o.created_at>?",
                (now() - 86400,),
            )
        ]
        unique_items = {n["id"] for p in snapshots for n in p["affected"] if n["kind"] in {"movie", "episode"}}
        files = {f["id"]: f for p in snapshots for f in [*p["files"], *p.get("sidecars", [])]}
        if len(unique_items) > settings["max_items"] or len(files) > settings["max_files"]:
            raise SafetyError("Global item or physical file limit exceeded")
        logical = sum(f["size"] for f in files.values())
        reclaimable = sum(json.loads(f["identity"])["reclaimable"] for f in files.values())
        if logical > settings["max_logical_bytes"] or reclaimable > settings["max_reclaimable_bytes"]:

            def amount(value):
                return f"{value:,} bytes ({value / 1_000_000_000:.2f} GB)"

            raise SafetyError(
                "Global byte limit exceeded. "
                f"Logical total: {amount(logical)}; limit: {amount(settings['max_logical_bytes'])}. "
                f"Estimated reclaimable total: {amount(reclaimable)}; limit: {amount(settings['max_reclaimable_bytes'])}. "
                f"Scope: {len(unique_items)} media items, {len(files)} physical files. "
                "Totals include this request plus live operation scopes from the last 24 hours. "
                "Bulk cleanup checks all ready candidates together; it does not select a smaller batch."
            )
        for library_id in {p["library_id"] for p in snapshots}:
            total = self.db.one(
                "SELECT COUNT(*) AS n FROM media WHERE library_id=? AND kind IN ('movie','episode') AND missing=0",
                (library_id,),
            )["n"]
            targeted = len(
                {
                    n["id"]
                    for p in snapshots
                    if p["library_id"] == library_id
                    for n in p["affected"]
                    if n["kind"] in {"movie", "episode"}
                }
            )
            if total and targeted / total * 100 > settings["max_library_percent"]:
                raise SafetyError("Maximum library percentage exceeded; review deletion safety settings")

    def selection(self, candidate, item, rule):
        request = self.db.one("SELECT * FROM manual_requests WHERE candidate_id=?", (candidate["id"],))
        files = self.catalog.files(item["id"])
        if request:
            selected = set(json.loads(request["file_ids"]))
            files = [file for file in files if file["id"] in selected]
            if {file["id"] for file in files} != selected:
                raise SafetyError("Manually selected file scope changed; preview again")
            reasons = self.engine.protection(item, rule, now(), files=files)
            result = {
                "applicable": True,
                "matched": True,
                "eligible": not reasons,
                "protections": reasons,
                "conditions": {"source": "explicit_administrator_selection"},
                "media_id": item["id"],
                "title": item["title"],
                "rule_id": rule["id"],
                "rule_version": rule["version"],
            }
        else:
            result = self.engine.evaluate(item, rule)
        return result, files, request

    def build(self, candidate_id):
        candidate, item, rule = self.candidate(candidate_id)
        result, files, manual = self.selection(candidate, item, rule)
        if not result["eligible"]:
            raise SafetyError("Candidate no longer eligible: " + "; ".join(result["protections"]))
        affected = self.catalog.descendants(item["id"])
        directories = self.fs.plan_directories(files)
        sidecars = self.fs.plan_sidecars(files, directories)
        target_ids = {n["id"] for n in affected}
        for file in files:
            owners = self.db.all("SELECT media_id FROM media_files WHERE file_id=?", (file["id"],))
            # Shared multi-episode files outside a single candidate scope cannot be removed.
            if any(owner["media_id"] not in target_ids for owner in owners):
                raise SafetyError(
                    "Shared file includes media outside this approved scope; use a matching season/show rule"
                )
        if rule["strategy"] == "remove" and item["kind"] not in {"movie", "show"}:
            raise SafetyError("Record removal is only supported for entire movies or shows; choose Unmonitor")
        snapshot = {
            "candidate_id": candidate_id,
            "media_id": item["id"],
            "title": item["title"],
            "library_id": item["library_id"],
            "kind": item["kind"],
            "rule_id": rule["id"],
            "rule_version": rule["version"],
            "strategy": rule["strategy"],
            "manual": bool(manual),
            "candidate_version": candidate["version"] if manual else None,
            "keep_file_ids": json.loads(manual["keep_file_ids"]) if manual else [],
            "kept_files": [
                self.db.one("SELECT * FROM files WHERE id=? AND deleted=0", (file_id,))
                for file_id in json.loads(manual["keep_file_ids"])
            ]
            if manual
            else [],
            "integration_fingerprint": self.integration_fingerprint(),
            "affected": [{"id": n["id"], "plex_key": n["plex_key"], "kind": n["kind"]} for n in affected],
            "files": files,
            "sidecars": sidecars,
            "directories": directories,
            "logical_bytes": sum(f["size"] for f in [*files, *sidecars]),
            "estimated_reclaimable": sum(json.loads(f["identity"])["reclaimable"] for f in [*files, *sidecars]),
            "checks": result,
            "postconditions": [
                "Exact media files absent",
                "Previewed title-related sidecars absent; empty planned parent directories removed",
                "Manager unmonitored or record removed",
                "Seerr availability reconciled when configured",
            ],
        }
        self.limits([snapshot])
        digest = hashlib.sha256(encode(snapshot).encode()).hexdigest()
        plan_id = uid()
        self.db.execute(
            "INSERT INTO plans VALUES(?,?,?,?,?,?,0)",
            (plan_id, candidate_id, encode(snapshot), digest, now(), now() + 900),
        )
        self.db.audit("plan.created", plan_id, {"digest": digest, "files": len(files)})
        return {"id": plan_id, "digest": digest, "expires": now() + 900, **snapshot}

    async def validate(self, plan):
        snapshot = json.loads(plan["snapshot"])
        if snapshot.get("integration_fingerprint") != self.integration_fingerprint():
            raise SafetyError("Integration configuration changed since planning")
        candidate, item, rule = self.candidate(plan["candidate_id"])
        self.authorized(candidate)
        if plan["expires"] < now() or plan["executed"]:
            raise SafetyError("Plan expired or already consumed; create a new plan")
        plex = self.integrations.get("plex")
        # Re-fetch the exact source leaves; changed scope, editions or source paths blocks execution.
        for node in snapshot["affected"]:
            if node["kind"] not in {"movie", "episode"}:
                continue
            fresh = await plex.metadata(node["plex_key"])
            expected = {f["source_path"] for f in self.catalog.files(node["id"])}
            actual = {p["file"] for m in fresh.get("Media", []) for p in m.get("Part", []) if p.get("file")}
            if expected != actual or fresh["type"] != node["kind"]:
                raise SafetyError("Plex physical file scope changed")
            old = self.db.one("SELECT * FROM media WHERE id=?", (node["id"],))
            prior = json.loads(old["metadata"])
            if any(
                fresh.get(field) != prior.get(field)
                for field in ("addedAt", "year", "parentRatingKey", "parentIndex", "index")
            ):
                raise SafetyError("Plex retention metadata or hierarchy changed; synchronize and reevaluate")

            def source_parts(metadata):
                return sorted(
                    (str(p.get("id")), p.get("file", "")) for m in metadata.get("Media", []) for p in m.get("Part", [])
                )

            if source_parts(fresh) != source_parts(prior):
                raise SafetyError("Plex part identities changed since planning")
            if fresh.get("lastViewedAt", 0) > (old["last_watched"] or 0):
                raise SafetyError("Playback metadata changed; synchronize and reevaluate")
        # Verify show/season scope has not grown beyond the approved descendant list.
        if item["kind"] in {"show", "season"}:
            path = f"/library/metadata/{item['plex_key']}/allLeaves"
            fresh_keys = set()
            async for page in plex.pages(path):
                fresh_keys.update(str(n["ratingKey"]) for n in page)
            if fresh_keys != {n["plex_key"] for n in snapshot["affected"] if n["kind"] == "episode"}:
                raise SafetyError("Television scope changed; create a new plan")
        await self.catalog.watch_history()
        if self.integrations.configured("seerr"):
            await self.catalog.requests()
        active = await plex.sessions()
        keys = {n["plex_key"] for n in snapshot["affected"]}
        paths = {f["source_path"] for f in snapshot["files"]}
        for session in active:
            session_paths = {p.get("file") for m in session.get("Media", []) for p in m.get("Part", [])}
            if str(session.get("ratingKey")) in keys or session_paths & paths:
                raise SafetyError("Active Plex playback protects this file")
            if not session.get("ratingKey") and not session_paths:
                raise SafetyError("An active stream could not be identified safely")
        result, current_files, manual = self.selection(candidate, item, rule)
        if not result["eligible"]:
            raise SafetyError("Final protection/retention validation failed: " + "; ".join(result["protections"]))
        for higher in (
            [] if manual else self.db.all("SELECT * FROM rules WHERE enabled=1 ORDER BY priority,library_id IS NULL,id")
        ):
            if self.engine.evaluate(item, higher)["eligible"]:
                if higher["id"] != rule["id"]:
                    raise SafetyError("A different rule now owns this candidate; reevaluate and approve again")
                break
        if encode(current_files) != encode(snapshot["files"]):
            raise SafetyError("File or integration identity changed since planning")
        for file in current_files:
            if self.fs.inspect(file["root_id"], file["path"]) != json.loads(file["identity"]):
                raise SafetyError("File identity changed since planning")
        if encode(self.fs.plan_directories(current_files)) != encode(snapshot.get("directories", [])):
            raise SafetyError("Parent directory identities changed since planning")
        if encode(self.fs.plan_sidecars(current_files, snapshot.get("directories", []))) != encode(
            snapshot.get("sidecars", [])
        ):
            raise SafetyError("Associated sidecar scope changed since planning; preview again")
        self.refresh_kept_hardlinks(snapshot)
        self.limits([snapshot])
        return snapshot

    def authorize_manual(self, plan_id, digest):
        plan = self.db.one("SELECT * FROM plans WHERE id=?", (plan_id,))
        if not plan or plan["digest"] != digest or plan["expires"] < now() or plan["executed"]:
            raise SafetyError("Manual plan expired or was consumed; preview again")
        snapshot = json.loads(plan["snapshot"])
        if not snapshot.get("manual"):
            return
        candidate, _, _ = self.candidate(plan["candidate_id"])
        if candidate["state"] != "PendingApproval" or candidate["version"] != snapshot["candidate_version"]:
            raise SafetyError("Manual selection changed; preview again")
        self.transition(candidate["id"], "Ready", {"manual_plan": plan_id}, expected_version=candidate["version"])

    def cleanup_directories(self, operation, snapshot):
        results = []
        for directory in snapshot.get("directories", []):
            key = "directory:" + hashlib.sha256((directory["root_id"] + directory["path"]).encode()).hexdigest()
            self.checkpoint(operation, key, "started", {"path": directory["path"]})
            outcome = self.fs.remove_empty_directory(directory)
            results.append({"path": directory["path"], "result": outcome})
            self.checkpoint(operation, key, "confirmed", results[-1])
        return results

    def refresh_kept_hardlinks(self, snapshot):
        removed = {}
        for file in snapshot["files"]:
            identity = json.loads(file["identity"])
            current = self.db.one("SELECT deleted FROM files WHERE id=?", (file["id"],))
            if identity["links"] > 1 and current and current["deleted"]:
                key = identity["device"], identity["inode"]
                removed[key] = removed.get(key, 0) + 1
        for original in snapshot.get("kept_files", []):
            if not original:
                raise SafetyError("Kept version is missing from the reviewed plan")
            file_id = original["id"]
            file = self.db.one("SELECT * FROM files WHERE id=? AND deleted=0", (file_id,))
            if (
                not file
                or not file["identity"]
                or (file["path"], file["root_id"]) != (original["path"], original["root_id"])
            ):
                raise SafetyError("Kept version is unavailable after deletion")
            expected = json.loads(original["identity"])
            count = removed.get((expected["device"], expected["inode"]), 0)
            actual = self.fs.inspect(file["root_id"], file["path"])
            if not count:
                if actual != expected:
                    raise SafetyError("Kept version changed since preview; deletion refused")
                continue
            if (
                any(actual[field] != expected[field] for field in ("device", "inode", "size", "mtime_ns"))
                or actual["links"] != expected["links"] - count
            ):
                raise SafetyError("Kept hardlink changed beyond the verified removed links")
            self.db.execute("UPDATE files SET identity=? WHERE id=?", (encode(actual), file_id))

    def checkpoint(self, operation_id, name, state, detail=None):
        self.db.execute(
            """INSERT INTO checkpoints VALUES(?,?,?,?,?,?) ON CONFLICT(operation_id,name)
            DO UPDATE SET state=excluded.state,detail=excluded.detail""",
            (uid(), operation_id, name, state, encode(detail or {}), now()),
        )

    async def preconditions(self, snapshot):
        grouped = {}
        for file in snapshot["files"]:
            if file["manager"]:
                grouped.setdefault((file["manager"], file["manager_parent"]), []).append(file)
            elif self.integrations.configured("radarr" if snapshot["kind"] == "movie" else "sonarr"):
                raise SafetyError("Configured manager has no confirmed association; synchronize mappings first")
        for (kind, parent), files in grouped.items():
            manager = self.integrations.get(kind)
            remote_files = await manager.files(parent)
            if remote_files is None:
                raise SafetyError("Manager physical file inventory unavailable")
            targeted = {f["manager_id"] for f in files}
            remote_by_id = {f["id"]: f for f in remote_files}
            if not targeted <= set(remote_by_id):
                raise SafetyError("Manager file identities changed")
            for file in files:
                mapped, _ = self.fs.map(kind, remote_by_id[file["manager_id"]]["path"])
                if mapped != file["path"]:
                    raise SafetyError("Manager file path differs from approved file")
            # API deletion must not move files to a configured recycling directory.
            config = await manager.http.request("GET", "/api/v3/config/mediamanagement")
            if not config or "recycleBin" not in config or config["recycleBin"]:
                raise SafetyError("Disable the manager recycling bin to use permanent exact-file deletion")
            if config.get("deleteEmptyFolders", True):
                raise SafetyError("Disable manager empty-folder deletion to preserve neighboring files and folders")
            if snapshot["strategy"] == "remove" and targeted != set(remote_by_id):
                raise SafetyError("Record removal would affect unplanned manager files")
            if kind == "radarr":
                await manager.unmonitor(parent)
            else:
                episodes = await manager.episodes(parent)
                ids = [e["id"] for e in episodes if e.get("episodeFileId") in targeted]
                if not ids:
                    raise SafetyError("Sonarr episode/file associations unavailable")
                # Every represented episode must be in the approved Plex scope, by season/episode number.
                expected = set()
                for node in snapshot["affected"]:
                    if node["kind"] == "episode":
                        metadata = json.loads(
                            self.db.one("SELECT metadata FROM media WHERE id=?", (node["id"],))["metadata"]
                        )
                        expected.add((metadata.get("parentIndex"), metadata.get("index")))
                for episode in episodes:
                    if (
                        episode.get("episodeFileId") in targeted
                        and (episode["seasonNumber"], episode["episodeNumber"]) not in expected
                    ):
                        raise SafetyError("Sonarr shared file contains an episode outside the approved scope")
                await manager.unmonitor(ids)
                if snapshot["kind"] == "show":
                    await manager.unmonitor_scope(parent)
                elif snapshot["kind"] == "season":
                    metadata = json.loads(
                        self.db.one("SELECT metadata FROM media WHERE id=?", (snapshot["media_id"],))["metadata"]
                    )
                    await manager.unmonitor_scope(parent, metadata["index"])
        return grouped

    async def synchronize(self, operation, snapshot):
        if self.integrations.configured("seerr"):
            references = {}
            for ancestor in self.catalog.ancestors(snapshot["media_id"]):
                for request in self.db.all("SELECT * FROM requests WHERE media_id=?", (ancestor["id"],)):
                    references[request["seerr_media_id"]] = request
            for media_id, request in references.items():
                related = self.db.all("SELECT * FROM requests WHERE seerr_media_id=?", (media_id,))
                # Mixed 4K/standard availability needs separate server-level availability tracking.
                if any(json.loads(r["metadata"]).get("is4k") for r in related):
                    raise SafetyError(
                        "4K Seerr availability requires administrator reconciliation; no whole-title override sent"
                    )
                remaining, complete = False, False
                for item_id in {r["media_id"] for r in related}:
                    files = self.catalog.files(item_id)
                    present = []
                    present_paths = set()
                    for file in files:
                        if not file["root_id"] or not file["identity"]:
                            raise SafetyError(
                                "Remaining Seerr availability cannot be verified through an authorized mapping"
                            )
                        exists = self.fs.exists(file["root_id"], file["path"])
                        if exists and self.fs.inspect(file["root_id"], file["path"]) != json.loads(file["identity"]):
                            raise SafetyError("Remaining media changed; resynchronize before availability repair")
                        present.append(exists)
                        if exists:
                            present_paths.add(file["source_path"])
                    remaining = remaining or any(present)
                    root = self.db.one("SELECT kind,metadata FROM media WHERE id=?", (item_id,))
                    if root["kind"] == "movie" and present and all(present):
                        editions = json.loads(root["metadata"]).get("Media", [])
                        complete = complete or any(
                            edition.get("Part") and all(part.get("file") in present_paths for part in edition["Part"])
                            for edition in editions
                        )
                if snapshot["kind"] == "movie" and remaining and not complete:
                    raise SafetyError("A multipart movie remains partly available; verify Seerr status manually")
                await self.integrations.get("seerr").availability(media_id, remaining, complete=complete)
                self.checkpoint(
                    operation,
                    f"seerr:{media_id}",
                    "confirmed",
                    {
                        "remaining": remaining,
                        "availability": "available" if complete else "partial" if remaining else "unknown",
                    },
                )
        self.db.execute(
            "UPDATE operations SET state='Completed',error=NULL,updated_at=? WHERE id=?", (now(), operation)
        )
        self.transition(snapshot["candidate_id"], "Completed")
        self.notifications.emit("cleanup_completed", f"Cleanup completed: {snapshot['title']}")

    async def execute(self, plan_id, digest, idempotency_key, job_id=None, expected_dry_run=None):
        async with self.lock:
            previous = self.db.one("SELECT * FROM operations WHERE idempotency_key=?", (idempotency_key,))
            if previous:
                if previous["plan_id"] != plan_id:
                    raise SafetyError("Idempotency key belongs to a different plan")
                return previous
            if self.stopping:
                raise SafetyError("Application is shutting down")
            plan = self.db.one("SELECT * FROM plans WHERE id=?", (plan_id,))
            if not plan or plan["digest"] != digest:
                raise SafetyError("Plan digest is invalid")
            candidate, _, _ = self.candidate(plan["candidate_id"])
            self.authorized(candidate)
            if candidate["state"] == "GracePeriod":
                self.transition(candidate["id"], "Ready")
            self.transition(candidate["id"], "Validating")
            try:
                snapshot = await self.validate(plan)
                dry_run = self.db.settings()["dry_run"]
                if expected_dry_run is not None and dry_run != expected_dry_run:
                    raise SafetyError("Deletion mode changed after submission; review and submit again")
            except Exception:
                self.transition(candidate["id"], "Protected", {"reason": "Final validation refused execution"})
                raise
            operation = uid()
            self.db.execute(
                "INSERT INTO operations(id,plan_id,idempotency_key,state,dry_run,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (operation, plan_id, idempotency_key, "Validating", int(dry_run), now(), now()),
            )
            self.db.execute("UPDATE plans SET executed=1 WHERE id=?", (plan_id,))
            if dry_run:
                self.db.execute(
                    "UPDATE operations SET state='Completed',outcome=? WHERE id=?",
                    (
                        encode(
                            {
                                "simulation": True,
                                "files": len(snapshot["files"]) + len(snapshot.get("sidecars", [])),
                                "logical_bytes": snapshot["logical_bytes"],
                            }
                        ),
                        operation,
                    ),
                )
                self.transition(candidate["id"], "Ready", {"dry_run": True})
                self.db.audit("deletion.simulated", operation, snapshot)
                return self.db.one("SELECT * FROM operations WHERE id=?", (operation,))
            if not self.db.settings()["live_enrolled"]:
                raise SafetyError("Live deletion requires explicit enrollment")
            try:
                self.checkpoint(operation, "manager_preconditions", "started")
                grouped = await self.preconditions(snapshot)
                self.checkpoint(operation, "manager_preconditions", "confirmed")
                if self.stopping or self.db.settings()["dry_run"]:
                    raise SafetyError("Deletion stopped before physical execution")
                # Sessions checked again after potentially slow monitoring operations.
                await self.validate({**plan, "executed": 0})
                self.transition(candidate["id"], "Executing")
                self.db.execute("UPDATE operations SET state='Executing',updated_at=? WHERE id=?", (now(), operation))
                before = {f["root_id"]: os.statvfs(self.fs.root(f["root_id"])["path"]) for f in snapshot["files"]}
                for file in snapshot["files"]:
                    self.refresh_kept_hardlinks(snapshot)
                    if self.stopping or self.db.settings()["dry_run"]:
                        raise SafetyError("Deletion interrupted; remaining files require reconciliation")
                    if snapshot["integration_fingerprint"] != self.integration_fingerprint():
                        raise SafetyError("Integration configuration changed during execution")
                    self.limits([snapshot])
                    if (
                        job_id
                        and self.db.one("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,))["cancel_requested"]
                    ):
                        raise SafetyError("Job cancelled; remaining files require reconciliation")
                    _, current_item, current_rule = self.candidate(candidate["id"])
                    if self.engine.protection(
                        current_item, current_rule, now(), files=[file] if snapshot.get("manual") else None
                    ):
                        raise SafetyError("Protection changed during execution; remaining files are blocked")
                    # Refresh playback before every physical deletion.
                    sessions = await self.integrations.get("plex").sessions()
                    if sessions:
                        # Conservative: block the run while any playback is active between files.
                        raise SafetyError("Playback began during execution; remaining files are protected")
                    if self.fs.inspect(file["root_id"], file["path"]) != json.loads(file["identity"]):
                        raise SafetyError("File identity changed during execution")
                    self.checkpoint(operation, "file:" + file["id"], "started", {"path": file["path"]})
                    if file["manager"]:
                        manager = self.integrations.get(file["manager"])
                        await manager.delete_file(file["manager_id"])
                        endpoint = "moviefile" if file["manager"] == "radarr" else "episodefile"
                        if (
                            await manager.http.request("GET", f"/api/v3/{endpoint}/{int(file['manager_id'])}")
                            is not None
                        ):
                            raise SafetyError("Manager file removal postcondition failed")
                    else:
                        self.fs.unlink(file)
                    if self.fs.exists(file["root_id"], file["path"]):
                        raise SafetyError("Physical deletion postcondition failed")
                    self.db.execute("UPDATE files SET deleted=1 WHERE id=?", (file["id"],))
                    self.checkpoint(
                        operation, "file:" + file["id"], "confirmed", {"path": file["path"], "bytes": file["size"]}
                    )
                for sidecar in snapshot.get("sidecars", []):
                    if self.stopping or self.db.settings()["dry_run"]:
                        raise SafetyError("Sidecar cleanup stopped; remaining work requires reconciliation")
                    if (
                        job_id
                        and self.db.one("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,))["cancel_requested"]
                    ):
                        raise SafetyError("Job cancelled before sidecar cleanup")
                    _, current_item, current_rule = self.candidate(candidate["id"])
                    if self.fs.exists(sidecar["root_id"], sidecar["path"]) and self.engine.protection(
                        current_item, current_rule, now(), files=[sidecar]
                    ):
                        raise SafetyError("Protection changed before sidecar cleanup")
                    if await self.integrations.get("plex").sessions():
                        raise SafetyError("Playback began before sidecar cleanup")
                    self.checkpoint(operation, "file:" + sidecar["id"], "started", {"path": sidecar["path"]})
                    if self.fs.exists(sidecar["root_id"], sidecar["path"]):
                        self.fs.validate_sidecar_ownership(sidecar, snapshot)
                        self.fs.unlink(sidecar)
                    self.checkpoint(
                        operation,
                        "file:" + sidecar["id"],
                        "confirmed",
                        {"path": sidecar["path"], "bytes": sidecar["size"]},
                    )
                if snapshot["strategy"] == "remove":
                    for kind, parent in grouped:
                        inventory = await self.integrations.get(kind).files(parent)
                        if inventory is None or inventory:
                            raise SafetyError(
                                "Manager gained unplanned files before record removal; reconciliation required"
                            )
                        self.checkpoint(operation, f"remove:{kind}:{parent}", "started")
                        await self.integrations.get(kind).remove(parent)
                        self.checkpoint(operation, f"remove:{kind}:{parent}", "confirmed")
                self.refresh_kept_hardlinks(snapshot)
                directory_results = self.cleanup_directories(operation, snapshot)
                after = {root: os.statvfs(self.fs.root(root)["path"]) for root in before}
                observed, seen_devices = 0, set()
                for root_id, info in before.items():
                    device = self.fs.root(root_id)["device"]
                    if device not in seen_devices:
                        observed += (after[root_id].f_bfree - info.f_bfree) * info.f_frsize
                        seen_devices.add(device)
                outcome = {
                    "logical_bytes": snapshot["logical_bytes"],
                    "estimated_reclaimable": snapshot["estimated_reclaimable"],
                    "observed_free_space_delta": observed,
                    "observed_delta_is_attributable": False,
                    "directories": directory_results,
                }
                self.db.execute(
                    "UPDATE operations SET state='Synchronizing',outcome=?,updated_at=? WHERE id=?",
                    (encode(outcome), now(), operation),
                )
                self.transition(candidate["id"], "Synchronizing")
                await self.synchronize(operation, snapshot)
                self.db.audit("deletion.completed", operation, {**snapshot, "outcome": outcome, "dry_run": False})
            except Exception as exc:
                message = (
                    str(exc) if isinstance(exc, SafetyError) else "Unexpected execution failure; inspect checkpoints"
                )
                self.db.execute(
                    "UPDATE operations SET state='ReconciliationRequired',error=?,updated_at=? WHERE id=?",
                    (message, now(), operation),
                )
                current = self.db.one("SELECT state FROM candidates WHERE id=?", (candidate["id"],))["state"]
                target = "ReconciliationRequired" if current in {"Executing", "Synchronizing"} else "Failed"
                self.transition(candidate["id"], target)
                self.db.execute(
                    "INSERT INTO reconciliation VALUES(?,?,?,?,?)", (uid(), operation, "pending", message, now())
                )
                self.notifications.emit("reconciliation_required", f"{snapshot['title']}: {message}")
                self.db.audit("deletion.interrupted", operation, {"error": message})
            return self.db.one("SELECT * FROM operations WHERE id=?", (operation,))

    async def reconcile(self):
        async with self.lock:
            if self.stopping:
                raise SafetyError("Application is shutting down")
            await self._reconcile()

    async def _reconcile(self):
        for task in self.db.all("SELECT * FROM reconciliation WHERE state='pending'"):
            operation = self.db.one("SELECT * FROM operations WHERE id=?", (task["operation_id"],))
            plan = self.db.one("SELECT * FROM plans WHERE id=?", (operation["plan_id"],))
            snapshot = json.loads(plan["snapshot"])
            checkpoints = self.db.all("SELECT * FROM checkpoints WHERE operation_id=?", (operation["id"],))
            confirmed = {c["name"] for c in checkpoints if c["state"] == "confirmed"}
            # Reconciliation is read-only for ambiguous physical requests. Never reissue DELETE.
            all_absent = True
            for file in [*snapshot["files"], *snapshot.get("sidecars", [])]:
                if self.fs.exists(file["root_id"], file["path"]):
                    all_absent = False
                elif "file:" + file["id"] not in {c["name"] for c in checkpoints} and not file["id"].startswith(
                    "sidecar:"
                ):
                    all_absent = False
                else:
                    if not file["id"].startswith("sidecar:"):
                        self.db.execute("UPDATE files SET deleted=1 WHERE id=?", (file["id"],))
                    self.checkpoint(operation["id"], "file:" + file["id"], "confirmed", {"reconciled_absent": True})
            remove_pending = any(c["name"].startswith("remove:") and c["state"] != "confirmed" for c in checkpoints)
            if snapshot["strategy"] == "remove":
                expected_removes = {
                    f"remove:{f['manager']}:{f['manager_parent']}" for f in snapshot["files"] if f["manager"]
                }
                remove_pending = remove_pending or not expected_removes <= confirmed
            if not all_absent or remove_pending:
                continue  # Operator must inspect and explicitly resolve incomplete/ambiguous manager actions.
            candidate = self.db.one("SELECT * FROM candidates WHERE id=?", (snapshot["candidate_id"],))
            if candidate["state"] != "ReconciliationRequired":
                continue
            self.transition(candidate["id"], "Synchronizing")
            try:
                self.refresh_kept_hardlinks(snapshot)
                self.cleanup_directories(operation["id"], snapshot)
                await self.synchronize(operation["id"], snapshot)
                self.db.execute("UPDATE reconciliation SET state='resolved' WHERE id=?", (task["id"],))
            except Exception as exc:
                self.transition(candidate["id"], "ReconciliationRequired")
                message = (
                    str(exc)
                    if isinstance(exc, SafetyError)
                    else "Synchronization failed; inspect integration health and retry reconciliation"
                )
                self.db.execute(
                    "UPDATE operations SET state='ReconciliationRequired',error=?,updated_at=? WHERE id=?",
                    (message, now(), operation["id"]),
                )
