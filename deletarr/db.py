"""Short, serialized SQLite transactions. No network calls while holding a transaction."""

import contextlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def uid():
    return str(uuid.uuid4())


def now():
    return int(time.time())


def encode(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


DEFAULTS = {
    "dry_run": True,
    "live_enrolled": False,
    "timezone": "UTC",
    "recent_playback_hours": 24,
    "minimum_age_days": 7,
    "request_protection_days": 30,
    "max_items": 20,
    "max_files": 50,
    "max_logical_bytes": 100_000_000_000,
    "max_reclaimable_bytes": 100_000_000_000,
    "max_library_percent": 10,
    "theme": "system",
    "accent": "#b0ed83",
    "onboarding_complete": False,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS administrator(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL, plex_identity TEXT);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY, count INTEGER NOT NULL, until_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS integrations(kind TEXT PRIMARY KEY CHECK(kind IN ('plex','radarr','sonarr','seerr')), url TEXT NOT NULL, secret TEXT NOT NULL, allow_private INTEGER NOT NULL, timeout INTEGER NOT NULL DEFAULT 15, server_id TEXT, health TEXT, checked_at INTEGER);
CREATE TABLE IF NOT EXISTS libraries(id TEXT PRIMARY KEY, external_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, kind TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, synced_at INTEGER);
CREATE TABLE IF NOT EXISTS roots(id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, device INTEGER NOT NULL, inode INTEGER NOT NULL, sentinel TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS mappings(id TEXT PRIMARY KEY, source TEXT NOT NULL, prefix TEXT NOT NULL, root_id TEXT NOT NULL REFERENCES roots(id), UNIQUE(source,prefix));
CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY, plex_key TEXT NOT NULL UNIQUE, library_id TEXT NOT NULL REFERENCES libraries(id), parent_id TEXT REFERENCES media(id), kind TEXT NOT NULL CHECK(kind IN ('movie','show','season','episode')), title TEXT NOT NULL, year INTEGER, added_at INTEGER, last_watched INTEGER, first_watched INTEGER, watch_count INTEGER NOT NULL DEFAULT 0, history_complete INTEGER NOT NULL DEFAULT 0, protected INTEGER NOT NULL DEFAULT 0, missing INTEGER NOT NULL DEFAULT 0, metadata TEXT NOT NULL DEFAULT '{}', updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS media_library ON media(library_id,kind);
CREATE INDEX IF NOT EXISTS media_parent ON media(parent_id);
CREATE TABLE IF NOT EXISTS external_ids(media_id TEXT NOT NULL REFERENCES media(id), provider TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(media_id,provider));
CREATE INDEX IF NOT EXISTS external_lookup ON external_ids(provider,value);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, plex_part TEXT NOT NULL UNIQUE, source_path TEXT NOT NULL, path TEXT, root_id TEXT REFERENCES roots(id), size INTEGER NOT NULL, identity TEXT, manager TEXT, manager_id INTEGER, manager_parent INTEGER, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS media_files(media_id TEXT NOT NULL REFERENCES media(id), file_id TEXT NOT NULL REFERENCES files(id), PRIMARY KEY(media_id,file_id));
CREATE INDEX IF NOT EXISTS file_relations ON media_files(file_id);
CREATE TABLE IF NOT EXISTS watch_events(id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media(id), external_id TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL, watched_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS protections(id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media(id), reason TEXT NOT NULL, expires INTEGER);
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, media_id TEXT REFERENCES media(id), seerr_media_id INTEGER NOT NULL, season INTEGER, requested_at INTEGER NOT NULL, status INTEGER, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rules(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, enabled INTEGER NOT NULL, priority INTEGER NOT NULL, library_id TEXT REFERENCES libraries(id), kind TEXT, action TEXT NOT NULL, grace_days INTEGER NOT NULL, min_age_days INTEGER NOT NULL, strategy TEXT NOT NULL, version INTEGER NOT NULL, conditions TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media(id), rule_id TEXT REFERENCES rules(id), rule_version INTEGER, eligible INTEGER NOT NULL, reasons TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media(id), rule_id TEXT NOT NULL REFERENCES rules(id), rule_version INTEGER NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL, reason TEXT NOT NULL, eligible_at INTEGER NOT NULL, due_at INTEGER, approved_at INTEGER, postponed_until INTEGER, UNIQUE(media_id,rule_id));
CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidates(id), snapshot TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL, expires INTEGER NOT NULL, executed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id), idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL, dry_run INTEGER NOT NULL, outcome TEXT NOT NULL DEFAULT '{}', error TEXT, retries INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(id), name TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL, created_at INTEGER NOT NULL, UNIQUE(operation_id,name));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, dedupe TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, available_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER, error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX IF NOT EXISTS active_job_dedupe ON jobs(dedupe) WHERE state IN ('queued','running');
CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, cron TEXT NOT NULL, timezone TEXT NOT NULL, enabled INTEGER NOT NULL, next_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reconciliation(id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(id), state TEXT NOT NULL, reason TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, event TEXT NOT NULL, message TEXT NOT NULL, created_at INTEGER NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS destinations(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, config TEXT NOT NULL, events TEXT NOT NULL, enabled INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, notification_id TEXT NOT NULL REFERENCES notifications(id), destination_id TEXT NOT NULL REFERENCES destinations(id), state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT, UNIQUE(notification_id,destination_id));
CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, at INTEGER NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, entity_id TEXT, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_time ON audit(at);
CREATE TABLE IF NOT EXISTS storage_snapshots(id TEXT PRIMARY KEY, root_id TEXT NOT NULL REFERENCES roots(id), at INTEGER NOT NULL, total INTEGER NOT NULL, free INTEGER NOT NULL, available INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS auth_pins(id TEXT PRIMARY KEY, pin_id INTEGER NOT NULL, secret TEXT NOT NULL, expires INTEGER NOT NULL, enrolling INTEGER NOT NULL);
"""


class Database:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "deletarr.db"
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA synchronous=FULL")
        # Refuse newer schemas rather than opening them with older runtime assumptions.
        exists = self.connection.execute("SELECT 1 FROM sqlite_master WHERE name='schema_version'").fetchone()
        if exists and (
            self.connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0] > 5
        ):
            raise ValueError("Database is newer than this Deletarr version; restore a compatible backup")
        if exists and self.connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0] < 5:
            self.backup()
        self.connection.executescript(SCHEMA)
        with self.transaction() as c:
            c.execute("INSERT OR IGNORE INTO schema_version VALUES(1,?)", (now(),))
            c.execute("INSERT OR IGNORE INTO settings VALUES(1,1,?)", (encode(DEFAULTS),))
            if not c.execute("SELECT 1 FROM schema_version WHERE version=2").fetchone():
                c.execute("ALTER TABLE media ADD COLUMN history_visible INTEGER NOT NULL DEFAULT 0")
                c.execute("INSERT INTO schema_version VALUES(2,?)", (now(),))
            if not c.execute("SELECT 1 FROM schema_version WHERE version=3").fetchone():
                c.execute("ALTER TABLE deliveries ADD COLUMN available_at INTEGER NOT NULL DEFAULT 0")
                c.execute("ALTER TABLE schedules ADD COLUMN interval_seconds INTEGER")
                c.execute("ALTER TABLE schedules ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
                c.execute(
                    "CREATE TABLE pressure_policies(id TEXT PRIMARY KEY,name TEXT NOT NULL,root_id TEXT NOT NULL REFERENCES roots(id),threshold REAL NOT NULL,target REAL NOT NULL,unit TEXT NOT NULL,never_order TEXT NOT NULL,enabled INTEGER NOT NULL)"
                )
                c.execute("CREATE INDEX candidates_state ON candidates(state,due_at)")
                c.execute("CREATE INDEX jobs_available ON jobs(state,available_at)")
                c.execute("CREATE INDEX operations_time ON operations(created_at)")
                c.execute("INSERT INTO schema_version VALUES(3,?)", (now(),))
            if not c.execute("SELECT 1 FROM schema_version WHERE version=4").fetchone():
                c.execute("ALTER TABLE media ADD COLUMN last_playback INTEGER")
                c.execute("ALTER TABLE watch_events ADD COLUMN completed INTEGER NOT NULL DEFAULT 0")
                c.execute("INSERT INTO schema_version VALUES(4,?)", (now(),))
            if not c.execute("SELECT 1 FROM schema_version WHERE version=5").fetchone():
                c.execute(
                    "CREATE TABLE manual_requests(candidate_id TEXT PRIMARY KEY REFERENCES candidates(id),file_ids TEXT NOT NULL,keep_file_ids TEXT NOT NULL)"
                )
                c.execute(
                    "CREATE TABLE file_protections(file_id TEXT PRIMARY KEY REFERENCES files(id),created_at INTEGER NOT NULL)"
                )
                c.execute("UPDATE plans SET expires=0")
                c.execute("INSERT INTO schema_version VALUES(5,?)", (now(),))
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise

    def all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.connection.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.transaction() as c:
            return c.execute(sql, args).rowcount

    def settings(self):
        row = self.one("SELECT * FROM settings WHERE id=1")
        return {**DEFAULTS, **json.loads(row["data"]), "version": row["version"]}

    def audit(self, event, entity=None, detail=None, actor="administrator"):
        self.execute(
            "INSERT INTO audit VALUES(?,?,?,?,?,?)", (uid(), now(), actor, event, entity, encode(detail or {}))
        )

    def backup(self):
        target = self.directory / ("backup-" + uid() + ".db")
        with self.lock, sqlite3.connect(target) as backup:
            self.connection.backup(backup)
        os.chmod(target, 0o600)
        return target
