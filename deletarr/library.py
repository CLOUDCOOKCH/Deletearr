"""Explicit library intent and physical editions; no title-only duplicate matching."""

import hashlib
import json

from .db import encode, now, uid
from .security import SafetyError

MANUAL_RULE_ID = "00000000-0000-0000-0000-000000000001"


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class LibraryActions:
    def __init__(self, db, catalog, executor, worker):
        self.db, self.catalog, self.executor, self.worker = db, catalog, executor, worker

    def preview(self, items, keep_file_ids=None):
        keep_file_ids = sorted(set(keep_file_ids or []))
        if not items or len(items) > 100:
            raise SafetyError("Choose between one and 100 media items")
        grouped = {}
        for selection in items:
            media_id = selection["media_id"]
            if media_id not in grouped:
                grouped[media_id] = set(selection["file_ids"]) if selection.get("file_ids") else None
            elif grouped[media_id] is not None:
                grouped[media_id] = (
                    grouped[media_id] | set(selection["file_ids"]) if selection.get("file_ids") else None
                )
        items = [
            {"media_id": media_id, "file_ids": sorted(file_ids) if file_ids is not None else None}
            for media_id, file_ids in grouped.items()
        ]
        prepared, selected_ids = [], set()
        for selection in items:
            item = self.db.one("SELECT * FROM media WHERE id=? AND missing=0", (selection["media_id"],))
            if not item:
                raise SafetyError("Selected media is unavailable; synchronize the library")
            available = {file["id"]: file for file in self.catalog.files(item["id"])}
            ids = sorted(set(selection.get("file_ids") or available))
            if not ids or not set(ids) <= set(available):
                raise SafetyError("Selected version is no longer part of this item")
            if set(ids) & selected_ids:
                raise SafetyError("Selections share a physical file; select a single containing season or show")
            selected_ids.update(ids)
            prepared.append((item, ids))
        if selected_ids & set(keep_file_ids):
            raise SafetyError("A retained version cannot also be selected for deletion")
        for file_id in keep_file_ids:
            file = self.db.one("SELECT * FROM files WHERE id=? AND deleted=0", (file_id,))
            if (
                not file
                or not file["identity"]
                or self.executor.fs.inspect(file["root_id"], file["path"]) != json.loads(file["identity"])
            ):
                raise SafetyError("Retained version changed; refresh the Versions tab")
        candidates = []
        with self.db.transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO rules VALUES(?,?,?,0,0,NULL,NULL,'approval',0,0,'unmonitor',1,?,?,?)",
                (
                    MANUAL_RULE_ID,
                    "Manual library deletion",
                    "Explicit administrator selection; all safety protections still apply.",
                    encode({"field": "size_bytes", "operator": "gte", "value": 0}),
                    now(),
                    now(),
                ),
            )
            for item, file_ids in prepared:
                previous = c.execute(
                    "SELECT * FROM candidates WHERE media_id=? AND rule_id=?", (item["id"], MANUAL_RULE_ID)
                ).fetchone()
                if previous and previous["state"] in {
                    "Validating",
                    "Executing",
                    "Synchronizing",
                    "ReconciliationRequired",
                }:
                    raise SafetyError("This media has unfinished manual work; reconcile it first")
                candidate_id = previous["id"] if previous else uid()
                c.execute("UPDATE plans SET expires=0 WHERE candidate_id=?", (candidate_id,))
                c.execute(
                    """INSERT INTO candidates(id,media_id,rule_id,rule_version,state,version,reason,eligible_at)
                    VALUES(?,?,?,1,'PendingApproval',1,?,?) ON CONFLICT(media_id,rule_id) DO UPDATE SET
                    state='PendingApproval',version=candidates.version+1,reason=excluded.reason,eligible_at=excluded.eligible_at,
                    due_at=NULL,approved_at=NULL,postponed_until=NULL""",
                    (
                        candidate_id,
                        item["id"],
                        MANUAL_RULE_ID,
                        encode({"source": "administrator_library_selection"}),
                        now(),
                    ),
                )
                c.execute(
                    "INSERT OR REPLACE INTO manual_requests VALUES(?,?,?)",
                    (candidate_id, encode(file_ids), encode(keep_file_ids)),
                )
                candidates.append(candidate_id)
        plans = [self.executor.build(candidate_id) for candidate_id in candidates]
        self.executor.limits(plans)
        return self.summary(plans)

    def summary(self, plans):
        media = {node["id"] for plan in plans for node in plan["affected"] if node["kind"] in {"movie", "episode"}}
        files = {file["id"]: file for plan in plans for file in [*plan["files"], *plan.get("sidecars", [])]}
        logical = sum(file["size"] for file in files.values())
        return {
            "plans": plans,
            "item_count": len(media),
            "file_count": len(files),
            "logical_bytes": logical,
            "estimated_reclaimable": sum(json.loads(file["identity"])["reclaimable"] for file in files.values()),
            "confirmation": f"DELETE {len(media)} ITEMS {logical} BYTES",
            "dry_run": self.db.settings()["dry_run"],
            "kept_files": [
                self.db.one("SELECT id,path,size FROM files WHERE id=?", (file_id,))
                for file_id in sorted({file_id for plan in plans for file_id in plan.get("keep_file_ids", [])})
            ],
        }

    def submit(self, references, confirmation, expected_dry_run):
        plans = []
        for reference in references:
            plan = self.db.one("SELECT * FROM plans WHERE id=?", (reference["id"],))
            if not plan or plan["digest"] != reference["digest"] or plan["executed"] or plan["expires"] < now():
                raise SafetyError("A reviewed plan expired, changed, or was consumed; preview again")
            snapshot = json.loads(plan["snapshot"])
            if not snapshot.get("manual"):
                raise SafetyError("Only explicit library plans can be submitted here")
            plans.append({**snapshot, "id": plan["id"], "digest": plan["digest"]})
        if not plans or len({plan["candidate_id"] for plan in plans}) != len(plans):
            raise SafetyError("Choose distinct current manual plans")
        summary = self.summary(plans)
        if expected_dry_run != summary["dry_run"]:
            raise SafetyError("Deletion mode changed after preview; preview and confirm again")
        if not summary["dry_run"] and confirmation != summary["confirmation"]:
            raise SafetyError("Confirm exactly: " + summary["confirmation"])
        self.executor.limits(plans)
        with self.db.transaction() as c:
            for plan in plans:
                candidate = c.execute("SELECT * FROM candidates WHERE id=?", (plan["candidate_id"],)).fetchone()
                if candidate["state"] != "PendingApproval" or candidate["version"] != plan["candidate_version"]:
                    raise SafetyError("Selection changed; preview again")
                for file_id in plan.get("keep_file_ids", []):
                    if c.execute("SELECT id FROM files WHERE id=? AND deleted=0", (file_id,)).fetchone() is None:
                        raise SafetyError("Retained version is no longer available")
                    c.execute("INSERT OR IGNORE INTO file_protections VALUES(?,?)", (file_id, now()))
        # A stable dedupe key makes repeated clicks return the same queued work.
        key = "manual-library:" + fingerprint(sorted(reference["id"] for reference in references))
        job_id = self.worker.enqueue(
            "cleanup", {"manual_plans": references, "expected_dry_run": summary["dry_run"]}, key
        )
        self.db.audit(
            "library.deletion_submitted", job_id, {"plans": [p["id"] for p in plans], "dry_run": summary["dry_run"]}
        )
        return {"job_id": job_id}

    def versions(self, q="", kind="", page=1, limit=30):
        rows = self.db.all(
            "SELECT m.*,l.title library FROM media m JOIN libraries l ON l.id=m.library_id WHERE m.missing=0"
        )
        media = {row["id"]: row for row in rows}
        ids = {}
        for entry in self.db.all("SELECT * FROM external_ids"):
            ids.setdefault(entry["media_id"], {})[entry["provider"]] = entry["value"]
        relations = {}
        for file in self.db.all(
            "SELECT f.*,mf.media_id FROM files f JOIN media_files mf ON f.id=mf.file_id WHERE f.deleted=0"
        ):
            relations.setdefault(file.pop("media_id"), {})[file["source_path"]] = file
        protected = {row["file_id"] for row in self.db.all("SELECT file_id FROM file_protections")}
        groups = {}
        for item in rows:
            if item["kind"] not in {"movie", "episode"} or (kind and kind != item["kind"]):
                continue
            metadata = json.loads(item["metadata"])
            root = item
            if item["kind"] == "episode":
                season = media.get(item["parent_id"])
                root = media.get(season["parent_id"]) if season else None
                if not root or metadata.get("parentIndex") is None or metadata.get("index") is None:
                    continue
            identity = ids.get(root["id"], {})
            provider = next(
                (
                    provider
                    for provider in (["tmdb", "imdb"] if item["kind"] == "movie" else ["tvdb", "tmdb"])
                    if identity.get(provider)
                ),
                None,
            )
            # Titles and release years are display information, never identity keys.
            identity_key = (provider, identity[provider]) if provider else ("plex-item", root["id"])
            key = (
                item["kind"],
                identity_key,
                metadata.get("parentIndex") if item["kind"] == "episode" else None,
                metadata.get("index") if item["kind"] == "episode" else None,
            )
            title = (
                root["title"]
                if item["kind"] == "movie"
                else f"{root['title']} · S{metadata['parentIndex']:02d}E{metadata['index']:02d}"
            )
            group = groups.setdefault(
                key,
                {
                    "id": fingerprint(key),
                    "title": title,
                    "kind": item["kind"],
                    "year": root["year"],
                    "poster": f"/api/media/{root['id']}/poster" if json.loads(root["metadata"]).get("thumb") else None,
                    "identity": f"{provider}:{identity[provider]}" if provider else "Same Plex item",
                    "versions": {},
                },
            )
            for edition in metadata.get("Media", []):
                parts = [part for part in edition.get("Part", []) if part.get("file")]
                files = [relations.get(item["id"], {}).get(part["file"]) for part in parts]
                if not files or any(file is None for file in files):
                    continue
                file_ids = sorted({file["id"] for file in files})
                version_id = fingerprint(file_ids)
                version = group["versions"].setdefault(
                    version_id,
                    {
                        "id": version_id,
                        "items": [],
                        "files": files,
                        "logical_bytes": sum(file["size"] for file in files),
                        "resolution": edition.get("videoResolution"),
                        "video_codec": edition.get("videoCodec"),
                        "audio_codec": edition.get("audioCodec"),
                        "container": edition.get("container"),
                        "edition": metadata.get("editionTitle") or edition.get("videoProfile"),
                        "protected": any(file["id"] in protected for file in files),
                        "libraries": [],
                    },
                )
                version["items"].append({"media_id": item["id"], "file_ids": file_ids})
                if item["library"] not in version["libraries"]:
                    version["libraries"].append(item["library"])
        result = []
        for group in groups.values():
            versions = list(group["versions"].values())
            if len(versions) < 2 or (q and q.casefold() not in group["title"].casefold()):
                continue
            # Overlapping editions are not independent copies (e.g. shared multipart assets).
            overlaps = any(
                {f["id"] for f in a["files"]} & {f["id"] for f in b["files"]}
                for i, a in enumerate(versions)
                for b in versions[i + 1 :]
            )
            result.append({**group, "versions": versions, "ambiguous": overlaps})
        result.sort(key=lambda group: (group["title"].casefold(), group["id"]))
        return {"items": result[(page - 1) * limit : page * limit], "total": len(result), "page": page, "limit": limit}

    def keep_preview(self, group_id, version_id):
        group = next((group for group in self.versions(limit=1_000_000)["items"] if group["id"] == group_id), None)
        if not group or group["ambiguous"]:
            raise SafetyError("Versions changed or share physical parts; synchronize and inspect the title")
        kept = next((version for version in group["versions"] if version["id"] == version_id), None)
        if not kept:
            raise SafetyError("Kept version is no longer available")
        removed = [version for version in group["versions"] if version["id"] != version_id]
        if any(version["protected"] for version in removed):
            raise SafetyError(
                "Another version is protected. Release its version protection before choosing a replacement."
            )
        return self.preview(
            [selection for version in removed for selection in version["items"]], [file["id"] for file in kept["files"]]
        )
