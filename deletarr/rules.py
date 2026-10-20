"""Typed, bounded, three-valued retention predicates. Unknown never becomes permission."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .db import encode, now, uid
from .security import SafetyError


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    group: Literal["and", "or", "not"] | None = None
    children: list["Condition"] = Field(default_factory=list, max_length=20)
    field: (
        Literal[
            "kind",
            "watched",
            "size_bytes",
            "added_days",
            "last_watched_days",
            "first_watched_days",
            "release_year",
            "protected",
            "free_bytes",
            "free_percent",
        ]
        | None
    ) = None
    operator: Literal["eq", "gt", "gte", "lt", "lte"] = "eq"
    value: str | float | bool | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.group:
            if self.field or not self.children or (self.group == "not" and len(self.children) != 1):
                raise ValueError("Logical groups need children; NOT requires exactly one child")
        elif self.field is None or self.value is None or self.children:
            raise ValueError("A condition requires an allowlisted field and a value")
        elif self.field == "kind" and (
            self.value not in ["movie", "show", "season", "episode"] or self.operator != "eq"
        ):
            raise ValueError("Media type requires equality with a supported type")
        elif self.field in {"watched", "protected"} and (not isinstance(self.value, bool) or self.operator != "eq"):
            raise ValueError("Boolean fields require true/false equality")
        elif self.field not in {"kind", "watched", "protected"} and (
            type(self.value) not in {int, float} or self.value < 0
        ):
            raise ValueError("Numeric fields require a non-negative number")
        return self


def validate_depth(node, depth=0):
    if depth > 8:
        raise SafetyError("Rule nesting is limited to eight levels")
    for child in node.children:
        validate_depth(child, depth + 1)


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    library_id: str | None = None
    kind: Literal["movie", "show", "season", "episode"] | None = None
    action: Literal["automatic", "approval", "grace"] = "approval"
    grace_days: int = Field(default=7, ge=0, le=3650)
    min_age_days: int = Field(default=7, ge=0, le=3650)
    strategy: Literal["unmonitor", "remove"] = "unmonitor"
    conditions: Condition
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def bounded(self):
        validate_depth(self.conditions)
        return self


def predicate(condition, values):
    """Return True / False / None with a trace; None is insufficient evidence."""
    if condition.group:
        results = [predicate(child, values) for child in condition.children]
        states = [result[0] for result in results]
        if condition.group == "not":
            state = None if states[0] is None else not states[0]
        elif condition.group == "and":
            state = False if False in states else None if None in states else True
        else:
            state = True if True in states else None if None in states else False
        return state, {"group": condition.group, "result": state, "children": [r[1] for r in results]}
    value = values.get(condition.field)
    if value is None:
        return None, {"field": condition.field, "result": None, "reason": "Information is incomplete"}
    comparator = {
        "eq": lambda a, b: a == b,
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
    }[condition.operator]
    result = comparator(value, condition.value)
    return result, {
        "field": condition.field,
        "operator": condition.operator,
        "expected": condition.value,
        "actual": value,
        "result": result,
    }


class Engine:
    def __init__(self, db, catalog, filesystem):
        self.db, self.catalog, self.fs = db, catalog, filesystem

    def protection(self, item, rule, at):
        settings = self.db.settings()
        reasons = []
        library = self.db.one("SELECT * FROM libraries WHERE id=?", (item["library_id"],))
        if not library or not library["enabled"]:
            reasons.append("Library is excluded")
        if not library or not library["synced_at"] or at - library["synced_at"] > 86400:
            reasons.append("Library synchronization is missing or older than 24 hours")
        scope = self.catalog.descendants(item["id"])
        ancestors = self.catalog.ancestors(item["id"])
        if self.db.one("SELECT kind FROM integrations WHERE kind='seerr'"):
            identities = [n for n in ancestors if n["kind"] in {"movie", "show"}]
            if not identities or any(
                not self.db.one(
                    "SELECT media_id FROM external_ids WHERE media_id=? AND provider IN ('tmdb','tvdb')", (n["id"],)
                )
                for n in identities
            ):
                reasons.append("Missing external identity for Seerr request protection")
        for node in {n["id"]: n for n in scope + ancestors}.values():
            if node["protected"] or self.db.one(
                "SELECT id FROM protections WHERE media_id=? AND (expires IS NULL OR expires>?)", (node["id"], at)
            ):
                reasons.append("Keep Forever or explicit media protection")
        for node in scope:
            if node["missing"]:
                reasons.append("Source item is missing")
            if (
                node["added_at"] is None
                or at - node["added_at"] < max(settings["minimum_age_days"], rule["min_age_days"]) * 86400
            ):
                reasons.append("Minimum library age protection")
            if node["last_watched"] and at - node["last_watched"] < settings["recent_playback_hours"] * 3600:
                reasons.append("Recent playback protection")
            if node["last_playback"] and at - node["last_playback"] < settings["recent_playback_hours"] * 3600:
                reasons.append("Recent playback protection")
        for ancestor in ancestors:
            for request in self.db.all(
                "SELECT * FROM requests WHERE media_id=? AND requested_at>?",
                (ancestor["id"], at - settings["request_protection_days"] * 86400),
            ):
                season_numbers = [
                    json.loads(n["metadata"]).get("index") for n in scope + ancestors if n["kind"] == "season"
                ]
                if request["season"] is None or request["season"] in season_numbers or item["kind"] == "show":
                    reasons.append("Recent Seerr request protection")
        files = self.catalog.files(item["id"])
        if not files:
            reasons.append("No verified physical media files")
        for file in files:
            if not file["identity"] or not file["root_id"] or not file["path"]:
                reasons.append("Missing or invalid path mapping")
            else:
                try:
                    if self.fs.inspect(file["root_id"], file["path"]) != json.loads(file["identity"]):
                        reasons.append("Physical file identity changed")
                except (OSError, SafetyError):
                    reasons.append("Media mount or file is unavailable")
        return sorted(set(reasons))

    def values(self, item, at):
        nodes = [x for x in self.catalog.descendants(item["id"]) if x["kind"] in {"movie", "episode"}]
        # For a season/show last-watched uses latest event, and all leaves must have affirmative watch evidence.
        aggregate_complete = bool(nodes) and all(n["watch_count"] > 0 and n["history_visible"] for n in nodes)
        last = max((n["last_watched"] or 0 for n in nodes), default=0)
        first = min((n["first_watched"] for n in nodes if n["first_watched"]), default=None)
        files = self.catalog.files(item["id"])
        roots = {f["root_id"] for f in files}
        free = percent = None
        if len(roots) == 1 and None not in roots:
            try:
                import os

                root = self.fs.root(next(iter(roots)))
                stat = os.statvfs(root["path"])
                free = stat.f_bavail * stat.f_frsize
                percent = stat.f_bavail / stat.f_blocks * 100 if stat.f_blocks else None
            except (OSError, SafetyError):
                pass
        return {
            "kind": item["kind"],
            "watched": True
            if aggregate_complete
            else False
            if nodes and all(n["history_complete"] and n["watch_count"] == 0 for n in nodes)
            else None,
            "size_bytes": sum(f["size"] for f in files),
            "added_days": (at - item["added_at"]) / 86400 if item["added_at"] else None,
            "last_watched_days": (at - last) / 86400 if last and aggregate_complete else None,
            "first_watched_days": (at - first) / 86400 if first and aggregate_complete else None,
            "release_year": item["year"],
            "protected": bool(item["protected"]),
            "free_bytes": free,
            "free_percent": percent,
        }

    def evaluate(self, item, rule, at=None):
        at = at or now()
        applies = (
            bool(rule["enabled"])
            and (not rule["library_id"] or rule["library_id"] == item["library_id"])
            and (not rule["kind"] or rule["kind"] == item["kind"])
        )
        matched, trace = predicate(Condition.model_validate(json.loads(rule["conditions"])), self.values(item, at))
        reasons = self.protection(item, rule, at)
        if matched is None:
            reasons.append("Rule depends on uncertain metadata or watch history")
        return {
            "applicable": applies,
            "eligible": applies and matched is True and not reasons,
            "matched": applies and matched is True,
            "protections": reasons,
            "conditions": trace,
            "media_id": item["id"],
            "title": item["title"],
            "rule_id": rule["id"],
            "rule_version": rule["version"],
        }

    def run(self, rule_id=None, persist=False):
        results = []
        rules = self.db.all("SELECT * FROM rules WHERE enabled=1 ORDER BY priority, library_id IS NULL, id")
        for item in self.db.all("SELECT * FROM media WHERE missing=0"):
            for rule in rules:
                if rule_id and rule["id"] != rule_id:
                    continue
                if rule["kind"] and rule["kind"] != item["kind"]:
                    continue
                result = self.evaluate(item, rule)
                if (
                    result["matched"]
                    or result["eligible"]
                    or (result["applicable"] and result["conditions"]["result"] is None)
                ):
                    results.append(result)
                if persist:
                    self.db.execute(
                        "INSERT INTO evaluations VALUES(?,?,?,?,?,?,?)",
                        (
                            uid(),
                            item["id"],
                            rule["id"],
                            rule["version"],
                            int(result["eligible"]),
                            encode(result),
                            now(),
                        ),
                    )
                    previous = self.db.one(
                        "SELECT * FROM candidates WHERE media_id=? AND rule_id=?", (item["id"], rule["id"])
                    )
                    if result["eligible"]:
                        new_asset_cycle = False
                        if previous and previous["state"] == "Completed":
                            completed = self.db.one(
                                """SELECT p.snapshot FROM plans p JOIN operations o ON o.plan_id=p.id
                                WHERE p.candidate_id=? AND o.dry_run=0 AND o.state IN ('Completed','ReconciledManually')
                                ORDER BY o.updated_at DESC,o.id DESC LIMIT 1""",
                                (previous["id"],),
                            )
                            if completed:
                                prior_files = {f["id"] for f in json.loads(completed["snapshot"])["files"]}
                                new_asset_cycle = {f["id"] for f in self.catalog.files(item["id"])} != prior_files
                        if (
                            previous
                            and previous["rule_version"] == rule["version"]
                            and previous["state"] not in {"Protected", "Failed"}
                            and not new_asset_cycle
                        ):
                            break
                        state = {"automatic": "Ready", "approval": "PendingApproval", "grace": "GracePeriod"}[
                            rule["action"]
                        ]
                        self.db.execute(
                            """INSERT INTO candidates(id,media_id,rule_id,rule_version,state,version,reason,eligible_at,due_at)
                            VALUES(?,?,?,?,?,1,?,?,?) ON CONFLICT(media_id,rule_id) DO UPDATE SET rule_version=excluded.rule_version,
                            state=excluded.state,version=candidates.version+1,reason=excluded.reason,eligible_at=excluded.eligible_at,
                            due_at=excluded.due_at,approved_at=NULL""",
                            (
                                uid(),
                                item["id"],
                                rule["id"],
                                rule["version"],
                                state,
                                encode(result),
                                now(),
                                now() + rule["grace_days"] * 86400 if rule["action"] == "grace" else None,
                            ),
                        )
                        break  # highest-priority matching eligible rule owns the candidate
                    elif previous and previous["state"] not in {
                        "Completed",
                        "Executing",
                        "Synchronizing",
                        "ReconciliationRequired",
                    }:
                        self.db.execute(
                            "UPDATE candidates SET state='Protected',version=version+1,reason=? WHERE id=?",
                            (encode(result), previous["id"]),
                        )
        return results
