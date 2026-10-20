from datetime import datetime

from .db import encode, now, uid
from .security import SafetyError


def timestamp(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


class Catalog:
    def __init__(self, db, integrations, filesystem):
        self.db, self.integrations, self.fs = db, integrations, filesystem

    async def discover(self):
        plex = self.integrations.get("plex")
        identity = await plex.identity()
        previous = self.db.one("SELECT server_id FROM integrations WHERE kind='plex'")
        if previous["server_id"] and previous["server_id"] != identity["machineIdentifier"]:
            raise SafetyError("Plex server identity changed; refuse to merge catalogs")
        self.db.execute("UPDATE integrations SET server_id=? WHERE kind='plex'", (identity["machineIdentifier"],))
        for library in await plex.libraries():
            if library["type"] not in {"movie", "show"}:
                continue
            self.db.execute(
                """INSERT INTO libraries(id,external_id,title,kind) VALUES(?,?,?,?)
                ON CONFLICT(external_id) DO UPDATE SET title=excluded.title,kind=excluded.kind""",
                (uid(), str(library["key"]), library["title"], library["type"]),
            )

    def save(self, library, item):
        key = str(item["ratingKey"])
        existing = self.db.one("SELECT * FROM media WHERE plex_key=?", (key,))
        media_id = existing["id"] if existing else uid()
        parent_key = item.get("parentRatingKey")
        parent = self.db.one("SELECT id,kind FROM media WHERE plex_key=?", (str(parent_key),)) if parent_key else None
        if item["type"] in {"movie", "show"}:
            parent = None
        elif parent and parent["kind"] != {"season": "show", "episode": "season"}.get(item["type"]):
            raise SafetyError("Plex hierarchy contains an invalid parent type")
        watched = int(item.get("viewCount", 0))
        last = max(item.get("lastViewedAt", 0), existing["last_watched"] or 0 if existing else 0) or None
        self.db.execute(
            """INSERT INTO media(id,plex_key,library_id,parent_id,kind,title,year,added_at,last_watched,
            watch_count,metadata,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(plex_key) DO UPDATE SET
            library_id=excluded.library_id,parent_id=excluded.parent_id,title=excluded.title,year=excluded.year,
            last_watched=excluded.last_watched,watch_count=MAX(media.watch_count,excluded.watch_count),
            metadata=excluded.metadata,missing=0,updated_at=excluded.updated_at""",
            (
                media_id,
                key,
                library["id"],
                parent["id"] if parent else None,
                item["type"],
                item.get("title", ""),
                item.get("year"),
                item.get("addedAt"),
                last,
                watched,
                encode(item),
                now(),
            ),
        )
        for entry in item.get("Guid", []):
            guid = entry["id"]
            if "://" in guid:
                provider, value = guid.split("://", 1)
                if provider in {"tmdb", "tvdb", "imdb"}:
                    self.db.execute("INSERT OR REPLACE INTO external_ids VALUES(?,?,?)", (media_id, provider, value))
        if item["type"] not in {"movie", "episode"}:
            return media_id
        desired = []
        for media in item.get("Media", []):
            for part in media.get("Part", []):
                if not part.get("file"):
                    continue
                # A multi-episode physical asset is represented once, related to every Plex episode.
                old = self.db.one(
                    "SELECT * FROM files WHERE source_path=? ORDER BY deleted,id DESC LIMIT 1", (part["file"],)
                )
                file_id = old["id"] if old else uid()
                try:
                    path, root = self.fs.map("plex", part["file"])
                    identity = self.fs.inspect(root, path)
                except (SafetyError, OSError):
                    path, root, identity = None, None, None
                if old and old["deleted"] and identity:
                    # A new download at the same path is a new physical asset. Preserve the
                    # prior operation's file record instead of silently resurrecting it.
                    self.db.execute("UPDATE files SET plex_part=? WHERE id=?", ("retired:" + old["id"], old["id"]))
                    file_id = uid()
                self.db.execute(
                    """INSERT INTO files(id,plex_part,source_path,path,root_id,size,identity)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source_path=excluded.source_path,
                    path=excluded.path,root_id=excluded.root_id,size=excluded.size,identity=excluded.identity""",
                    (
                        file_id,
                        str(part["id"]),
                        part["file"],
                        path,
                        root,
                        identity["size"] if identity else int(part.get("size", 0)),
                        encode(identity) if identity else None,
                    ),
                )
                self.db.execute("INSERT OR IGNORE INTO media_files VALUES(?,?)", (media_id, file_id))
                desired.append(file_id)
        for relation in self.db.all("SELECT file_id FROM media_files WHERE media_id=?", (media_id,)):
            if relation["file_id"] not in desired:
                self.db.execute(
                    "DELETE FROM media_files WHERE media_id=? AND file_id=?", (media_id, relation["file_id"])
                )
        return media_id

    async def sync(self, incremental=False):
        # Full enumeration is intentional: Plex updatedAt filters do not reliably cover every watch-state change.
        await self.discover()
        plex = self.integrations.get("plex")
        for library in self.db.all("SELECT * FROM libraries WHERE enabled=1"):
            seen = set()
            for media_type in [1] if library["kind"] == "movie" else [2, 3, 4]:
                async for items in plex.pages(
                    f"/library/sections/{library['external_id']}/all", type=media_type, includeGuids=1
                ):
                    for item in items:
                        seen.add(self.save(library, item))
            # Disappearance only affects eligibility; never authorizes file deletion.
            for item in self.db.all("SELECT id FROM media WHERE library_id=?", (library["id"],)):
                if item["id"] not in seen:
                    self.db.execute("UPDATE media SET missing=1 WHERE id=?", (item["id"],))
            self.db.execute("UPDATE libraries SET synced_at=? WHERE id=?", (now(), library["id"]))
        await self.watch_history()
        await self.managers()
        if self.integrations.configured("seerr"):
            await self.requests()
        self.db.audit("catalog.synchronized", actor="scheduler")

    async def watch_history(self):
        plex = self.integrations.get("plex")
        # Failure revokes current coverage but never erases locally observed events.
        self.db.execute("UPDATE media SET history_visible=0")
        visible = await plex.history_visibility()
        uncertain = set()
        # Historical completeness is deliberately not inferred from a successful request.
        async for page in plex.pages("/status/sessions/history/all", sort="viewedAt:asc"):
            for entry in page:
                item = self.db.one("SELECT * FROM media WHERE plex_key=?", (str(entry.get("ratingKey")),))
                watched_at = entry.get("viewedAt")
                if not item or not watched_at:
                    continue
                external_id = str(
                    entry.get("historyKey")
                    or entry.get("key")
                    or (str(entry.get("accountID")) + ":" + item["plex_key"] + ":" + str(watched_at))
                )
                completed = int(entry.get("viewCount", 0)) > 0
                self.db.execute(
                    "INSERT OR IGNORE INTO watch_events(id,media_id,external_id,user_id,watched_at,completed) VALUES(?,?,?,?,?,?)",
                    (
                        uid(),
                        item["id"],
                        external_id,
                        str(entry.get("accountID", "unknown")),
                        watched_at,
                        int(completed),
                    ),
                )
                self.db.execute(
                    "UPDATE media SET last_playback=MAX(COALESCE(last_playback,0),?) WHERE id=?",
                    (watched_at, item["id"]),
                )
                if not completed:
                    uncertain.add(item["id"])
                    continue
                # Playback alone is not a completed watch. Require an affirmative
                # Plex watched marker before promoting history into retention evidence.
                self.db.execute(
                    """UPDATE media SET last_watched=MAX(COALESCE(last_watched,0),?),
                    first_watched=MIN(COALESCE(first_watched,?),?),watch_count=MAX(watch_count,1) WHERE id=?""",
                    (watched_at, watched_at, watched_at, item["id"]),
                )
        if visible:
            self.db.execute("UPDATE media SET history_visible=1 WHERE missing=0")
            uncertain.update(
                r["media_id"]
                for r in self.db.all("""SELECT DISTINCT w.media_id FROM watch_events w
                JOIN media m ON m.id=w.media_id WHERE w.completed=0 AND w.watched_at>COALESCE(m.last_watched,0)""")
            )
            for item_id in uncertain:
                self.db.execute("UPDATE media SET history_visible=0 WHERE id=?", (item_id,))
        self.db.execute("""UPDATE media SET watch_count=MAX(watch_count,
            (SELECT COUNT(*) FROM watch_events w WHERE w.media_id=media.id AND completed=1))""")

    async def managers(self):
        for kind in ["radarr", "sonarr"]:
            if not self.integrations.configured(kind):
                continue
            manager = self.integrations.get(kind)
            records = await (manager.movies() if kind == "radarr" else manager.series())
            if records is None:
                raise SafetyError("Manager catalog is unavailable")
            self.db.execute(
                "UPDATE files SET manager=NULL,manager_id=NULL,manager_parent=NULL WHERE manager=?", (kind,)
            )
            for record in records:
                files = await manager.files(record["id"])
                for remote in files or []:
                    try:
                        path, _ = self.fs.map(kind, remote["path"])
                    except (SafetyError, KeyError, OSError):
                        continue
                    self.db.execute(
                        "UPDATE files SET manager=?,manager_id=?,manager_parent=? WHERE path=? AND deleted=0",
                        (kind, remote["id"], record["id"], path),
                    )

    async def requests(self):
        adapter = self.integrations.get("seerr")
        requests = await adapter.requests()
        normalized = []

        def matches(remote):
            kind = "show" if remote.get("mediaType") == "tv" else "movie"
            found = {}
            for provider, key in [("tmdb", "tmdbId"), ("tvdb", "tvdbId")]:
                if remote.get(key):
                    for item in self.db.all(
                        """SELECT m.* FROM media m JOIN external_ids e ON m.id=e.media_id
                        WHERE e.provider=? AND e.value=? AND m.kind=?""",
                        (provider, str(remote[key]), kind),
                    ):
                        found[item["id"]] = item
            return list(found.values())

        for request in requests:
            remote = request.get("media", {})
            items = matches(remote)
            seasons = request.get("seasons") or [None]
            for item in items:
                for season in seasons:
                    number = season.get("seasonNumber") if season else None
                    normalized.append(
                        (
                            f"{request['id']}:{item['id']}:{number}",
                            item["id"],
                            remote["id"],
                            number,
                            timestamp(request["createdAt"]),
                            request.get("status"),
                            encode(
                                {
                                    "request_id": request["id"],
                                    "media_type": remote.get("mediaType"),
                                    "is4k": request.get("is4k", False),
                                }
                            ),
                        )
                    )
        for remote in await adapter.media():
            for item in matches(remote):
                normalized.append(
                    (
                        f"catalog:{remote['id']}:{item['id']}",
                        item["id"],
                        remote["id"],
                        None,
                        0,
                        remote.get("status"),
                        encode(
                            {
                                "source": "catalog",
                                "media_type": remote.get("mediaType"),
                                "is4k": remote.get("status4k") in {4, 5},
                            }
                        ),
                    )
                )
        with self.db.transaction() as c:
            c.execute("DELETE FROM requests")
            c.executemany("INSERT INTO requests VALUES(?,?,?,?,?,?,?)", normalized)

    def descendants(self, item_id):
        return self.db.all(
            """WITH RECURSIVE tree AS (SELECT * FROM media WHERE id=? UNION ALL
            SELECT m.* FROM media m JOIN tree ON m.parent_id=tree.id) SELECT * FROM tree""",
            (item_id,),
        )

    def ancestors(self, item_id):
        return self.db.all(
            """WITH RECURSIVE tree AS (SELECT * FROM media WHERE id=? UNION ALL
            SELECT m.* FROM media m JOIN tree ON tree.parent_id=m.id) SELECT * FROM tree""",
            (item_id,),
        )

    def files(self, item_id):
        return self.db.all(
            """WITH RECURSIVE tree AS (SELECT id FROM media WHERE id=? UNION ALL
            SELECT m.id FROM media m JOIN tree ON m.parent_id=tree.id)
            SELECT DISTINCT f.* FROM files f JOIN media_files mf ON mf.file_id=f.id
            JOIN tree ON tree.id=mf.media_id WHERE f.deleted=0 ORDER BY f.id""",
            (item_id,),
        )
