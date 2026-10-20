"""Storage-pressure selection is a plan over retention-eligible media, never a safety override."""

import os

from .security import SafetyError


class StoragePlanner:
    def __init__(self, db, catalog, engine, executor, filesystem):
        self.db, self.catalog, self.engine, self.executor, self.fs = db, catalog, engine, executor, filesystem

    def select(self, policy):
        root = self.fs.root(policy["root_id"])
        stat = os.statvfs(root["path"])
        total, free = stat.f_blocks * stat.f_frsize, stat.f_bavail * stat.f_frsize
        threshold = total * policy["threshold"] / 100 if policy["unit"] == "percent" else policy["threshold"]
        target = total * policy["target"] / 100 if policy["unit"] == "percent" else policy["target"]
        response = {
            "root_id": root["id"],
            "available_bytes": free,
            "threshold_bytes": threshold,
            "target_bytes": target,
            "plans": [],
            "estimated_reclaimable": 0,
            "under_pressure": free < threshold,
            "target_achievable": free >= target,
            "blocked": [],
        }
        if free >= threshold:
            return response
        self.engine.run(persist=True)
        choices = []
        for candidate in self.db.all(
            "SELECT * FROM candidates WHERE state IN ('PendingApproval','Ready','GracePeriod')"
        ):
            item = self.db.one("SELECT * FROM media WHERE id=?", (candidate["media_id"],))
            files = self.catalog.files(item["id"])
            if not files or any(f["root_id"] != root["id"] for f in files):
                continue
            leaves = [n for n in self.catalog.descendants(item["id"]) if n["kind"] in {"movie", "episode"}]
            if not leaves or any(
                not ((n["last_watched"] and n["history_visible"]) or (n["history_complete"] and n["watch_count"] == 0))
                for n in leaves
            ):
                response["blocked"].append(
                    {"candidate_id": candidate["id"], "reason": "Watch-order evidence is uncertain"}
                )
                continue
            last = max((n["last_watched"] or 0 for n in leaves), default=0)
            if not last and policy["never_order"] == "exclude":
                continue
            # Never-watched items form a separate bucket. No invented last-watched date.
            order = (0, last, item["id"]) if last else (1, item["added_at"] or 0, item["id"])
            if not last and policy["never_order"] == "newest_added":
                order = (1, -(item["added_at"] or 0), item["id"])
            choices.append((order, candidate))
        selected_files = set()
        for _, candidate in sorted(choices, key=lambda value: value[0]):
            if free + response["estimated_reclaimable"] >= target:
                break
            try:
                plan = self.executor.build(candidate["id"])
                ids = {f["id"] for f in plan["files"]}
                if ids & selected_files:
                    continue
                self.executor.limits([*response["plans"], plan])
                # Hardlinked files returning no blocks do not advance a pressure recovery plan.
                if plan["estimated_reclaimable"] == 0:
                    continue
                response["plans"].append(plan)
                selected_files.update(ids)
                response["estimated_reclaimable"] += plan["estimated_reclaimable"]
            except (SafetyError, OSError) as exc:
                response["blocked"].append({"candidate_id": candidate["id"], "reason": str(exc)})
        response["target_achievable"] = free + response["estimated_reclaimable"] >= target
        return response


async def mapping_suggestions(db, integrations, filesystem):
    """Read-only bounded evidence. Suggestions never create mappings or expand root authority."""
    from pathlib import PurePosixPath

    result = {"suggestions": [], "unavailable": [], "requires_manual_confirmation": True}
    sources = {}
    for kind in ["plex", "radarr", "sonarr"]:
        if not integrations.configured(kind):
            continue
        try:
            adapter = integrations.get(kind)
            if kind == "plex":
                libraries = await adapter.libraries()
                prefixes = [
                    location["path"]
                    for library in libraries
                    for location in library.get("Location", [])
                    if location.get("path")
                ]
                samples = [
                    (f["source_path"], f["size"])
                    for f in db.all("SELECT source_path,size FROM files WHERE deleted=0 LIMIT 100")
                ]
            else:
                prefixes = [root["path"] for root in await adapter.roots() if root.get("path")]
                records = await (adapter.movies() if kind == "radarr" else adapter.series())
                samples = []
                for record in records[:3]:
                    samples.extend(
                        (f["path"], f.get("size", 0))
                        for f in (await adapter.files(record["id"]) or [])[:30]
                        if f.get("path")
                    )
            sources[kind] = (prefixes[:10], samples[:100])
        except (SafetyError, KeyError, TypeError):
            result["unavailable"].append(kind)
    for kind, (prefixes, samples) in sources.items():
        for raw in prefixes:
            prefix = PurePosixPath(raw)
            if not prefix.is_absolute() or ".." in prefix.parts or "\\" in raw:
                continue
            configured = [
                PurePosixPath(m["prefix"]) for m in db.all("SELECT prefix FROM mappings WHERE source=?", (kind,))
            ]
            if any(prefix.is_relative_to(existing) or existing.is_relative_to(prefix) for existing in configured):
                continue
            applicable = [
                (PurePosixPath(path).relative_to(prefix), size)
                for path, size in samples
                if PurePosixPath(path).is_relative_to(prefix) and ".." not in PurePosixPath(path).parts and size > 0
            ][:3]
            if not applicable:
                continue
            for root in db.all("SELECT * FROM roots WHERE confirmed=1 LIMIT 20"):
                verified = []
                try:
                    for relative, size in applicable:
                        target = str(PurePosixPath(root["path"]) / relative)
                        info = filesystem.inspect(root["id"], target)
                        if info["size"] != size:
                            break
                        verified.append(target)
                    if len(verified) == len(applicable):
                        result["suggestions"].append(
                            {
                                "source": kind,
                                "prefix": str(prefix),
                                "root_id": root["id"],
                                "root_path": root["path"],
                                "matched_samples": verified,
                                "evidence": "Exact relative path and logical size match; administrator must verify underlying storage",
                            }
                        )
                except (SafetyError, OSError):
                    continue
    return result
