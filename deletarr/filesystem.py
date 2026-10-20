"""Linux descriptor-relative exact-file access. Never walks a symlink or removes a tree."""

import contextlib
import errno
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path, PurePosixPath

from .db import uid
from .security import SafetyError


class Filesystem:
    def __init__(self, db):
        self.db = db

    def ancestry(self, path):
        identities = []
        for parent in [Path(path), *Path(path).parents]:
            with self.directory(parent) as fd:
                info = os.fstat(fd)
                identities.append((info.st_dev, info.st_ino))
        return identities

    def validate_location(self, path, skip=None):
        identities = self.ancestry(path)
        config = self.ancestry(self.db.directory.resolve())
        if identities[0] in config or config[0] in identities:
            raise SafetyError("Configuration and media directories must be separate, including mount aliases")
        for root in self.db.all("SELECT * FROM roots"):
            if root["id"] == skip:
                continue
            other = self.ancestry(root["path"])
            if identities[0] in other or other[0] in identities:
                raise SafetyError("Media roots must not overlap, including mount aliases")

    def register(self, path):
        with self.db.lock:
            return self._register(path)

    def _register(self, path):
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts or str(path) == "/":
            raise SafetyError("Choose an absolute, non-root media directory")
        path = Path(os.path.abspath(path))
        config = self.db.directory.resolve()
        if path == config or path in config.parents or config in path.parents:
            raise SafetyError("Configuration and media directories must be separate")
        self.validate_location(path)
        for root in self.db.all("SELECT * FROM roots"):
            other = Path(root["path"])
            if path == other or path in other.parents or other in path.parents:
                raise SafetyError("Media roots must not overlap")
        with self.directory(path) as fd:
            info = os.fstat(fd)
            name = ".deletarr-volume-" + secrets.token_hex(16)
            marker = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            os.close(marker)
        ident = uid()
        self.db.execute("INSERT INTO roots VALUES(?,?,?,?,?,1)", (ident, str(path), info.st_dev, info.st_ino, name))
        return ident

    def reauthorize(self, root_id):
        root = self.db.one("SELECT * FROM roots WHERE id=?", (root_id,))
        if not root:
            raise SafetyError("Media root not found")
        self.validate_location(root["path"], skip=root_id)
        with self.directory(root["path"]) as fd:
            info = os.fstat(fd)
            marker = ".deletarr-volume-" + secrets.token_hex(16)
            handle = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            os.close(handle)
        with self.db.transaction() as c:
            c.execute(
                "UPDATE roots SET device=?,inode=?,sentinel=?,confirmed=1 WHERE id=?",
                (info.st_dev, info.st_ino, marker, root_id),
            )
            c.execute("UPDATE files SET identity=NULL WHERE root_id=?", (root_id,))
            c.execute("UPDATE plans SET expires=0")
        self.db.audit("storage.reauthorized", root_id)

    @contextlib.contextmanager
    def directory(self, path):
        parts = Path(path).parts
        if not parts or parts[0] != "/" or ".." in parts:
            raise SafetyError("Unsafe directory")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in parts[1:]:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    def root(self, root_id):
        root = self.db.one("SELECT * FROM roots WHERE id=? AND confirmed=1", (root_id,))
        if not root:
            raise SafetyError("Media root is not authorized")
        with self.directory(root["path"]) as fd:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (root["device"], root["inode"]):
                raise SafetyError("Media mount identity changed; reauthorize the mount")
            marker = os.stat(root["sentinel"], dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(marker.st_mode):
                raise SafetyError("Media mount sentinel is invalid")
        return root

    def map(self, source, path):
        source_path = PurePosixPath(path)
        if not source_path.is_absolute() or ".." in source_path.parts or "\\" in path:
            raise SafetyError("Unsafe source path; configure a POSIX source mapping")
        matches = []
        for mapping in self.db.all("SELECT * FROM mappings WHERE source=?", (source,)):
            prefix = PurePosixPath(mapping["prefix"])
            if source_path.is_relative_to(prefix):
                matches.append(mapping)
        if len(matches) != 1:
            raise SafetyError("Path requires exactly one confirmed mapping")
        mapping = matches[0]
        root = self.root(mapping["root_id"])
        relative = source_path.relative_to(mapping["prefix"])
        if str(relative) == ".":
            raise SafetyError("Media path cannot be a root directory")
        return str(Path(root["path"]) / str(relative)), root["id"]

    @contextlib.contextmanager
    def parent(self, root_id, path):
        root = self.root(root_id)
        target = Path(path)
        base = Path(root["path"])
        if not target.is_relative_to(base) or ".." in target.parts or target == base:
            raise SafetyError("File is outside its authorized media root")
        with self.directory(base) as root_fd:
            identity = os.fstat(root_fd)
            if (identity.st_dev, identity.st_ino) != (root["device"], root["inode"]):
                raise SafetyError("Media root changed while opening the file")
            os.stat(root["sentinel"], dir_fd=root_fd, follow_symlinks=False)
            fd = os.dup(root_fd)
            try:
                for component in target.relative_to(base).parts[:-1]:
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = child
                    if os.fstat(fd).st_dev != root["device"]:
                        raise SafetyError("Nested filesystem requires its own media root")
                yield fd, target.name
            finally:
                os.close(fd)

    def inspect(self, root_id, path):
        with self.parent(root_id, path) as (fd, name):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise SafetyError("Only regular media files may be removed")
            return {
                "device": info.st_dev,
                "inode": info.st_ino,
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "ctime_ns": info.st_ctime_ns,
                "links": info.st_nlink,
                "allocated": info.st_blocks * 512,
                "reclaimable": info.st_blocks * 512 if info.st_nlink == 1 else 0,
            }

    def exists(self, root_id, path):
        # A missing mount must never be mistaken for a successfully removed file.
        self.root(root_id)
        try:
            with self.parent(root_id, path) as (fd, name):
                os.stat(name, dir_fd=fd, follow_symlinks=False)
                return True
        except FileNotFoundError:
            self.root(root_id)
            return False

    def unlink(self, file):
        expected = json.loads(file["identity"]) if isinstance(file["identity"], str) else file["identity"]
        if self.inspect(file["root_id"], file["path"]) != expected:
            raise SafetyError("File identity changed since planning")
        with self.parent(file["root_id"], file["path"]) as (fd, name):
            # Last identity check against the held parent; the writable media tree must be trusted.
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                expected["device"],
                expected["inode"],
                expected["size"],
                expected["mtime_ns"],
                expected["ctime_ns"],
            ):
                raise SafetyError("File changed immediately before unlink")
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)

    def snapshot(self):
        results = []
        for row in self.db.all("SELECT * FROM roots"):
            try:
                self.root(row["id"])
                info = os.statvfs(row["path"])
                result = {
                    **row,
                    "total": info.f_blocks * info.f_frsize,
                    "free": info.f_bfree * info.f_frsize,
                    "available": info.f_bavail * info.f_frsize,
                    "healthy": True,
                }
                from .db import now

                previous = self.db.one("SELECT MAX(at) at FROM storage_snapshots WHERE root_id=?", (row["id"],))
                if not previous["at"] or now() - previous["at"] >= 300:
                    self.db.execute(
                        "INSERT INTO storage_snapshots VALUES(?,?,?,?,?,?)",
                        (uid(), row["id"], now(), result["total"], result["free"], result["available"]),
                    )
            except (OSError, SafetyError):
                result = {**row, "healthy": False, "error": "Mount identity or access validation failed"}
            results.append(result)
        return results

    def plan_directories(self, files):
        """Enumerate bounded parent directories, excluding every authorized root."""
        planned = {}
        for file in files:
            root = self.root(file["root_id"])
            base = Path(root["path"])
            target = Path(file["path"])
            if not target.is_relative_to(base) or ".." in target.parts:
                raise SafetyError("Directory cleanup is outside the authorized root")
            relative = target.relative_to(base)
            if len(relative.parts) > 64:
                raise SafetyError("Media path exceeds the directory cleanup depth limit")
            for parent in target.parents:
                if parent == base:
                    break
                with self.directory(parent) as fd:
                    info = os.fstat(fd)
                    if info.st_dev != root["device"]:
                        raise SafetyError("Directory cleanup cannot cross a filesystem boundary")
                    planned[(root["id"], str(parent))] = {
                        "root_id": root["id"],
                        "path": str(parent),
                        "device": info.st_dev,
                        "inode": info.st_ino,
                    }
                if len(planned) > 4096:
                    raise SafetyError("Directory cleanup exceeds its bounded scope")
        return sorted(planned.values(), key=lambda directory: (-len(Path(directory["path"]).parts), directory["path"]))

    def remove_empty_directory(self, directory):
        """Descriptor-relative rmdir only: neighboring files are never enumerated for deletion."""
        self.root(directory["root_id"])
        try:
            with self.parent(directory["root_id"], directory["path"]) as (parent_fd, name):
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (
                    directory["device"],
                    directory["inode"],
                ):
                    raise SafetyError("Planned directory identity changed; cleanup refused")
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    held = os.fstat(fd)
                    if (held.st_dev, held.st_ino) != (directory["device"], directory["inode"]):
                        raise SafetyError("Directory changed immediately before cleanup")
                    if os.listdir(fd):
                        return "retained_not_empty"
                    checked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if (checked.st_dev, checked.st_ino) != (held.st_dev, held.st_ino):
                        raise SafetyError("Directory changed immediately before cleanup")
                    try:
                        os.rmdir(name, dir_fd=parent_fd)
                    except OSError as exc:
                        if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                            return "retained_not_empty"
                        raise
                    os.fsync(parent_fd)
                    return "removed"
                finally:
                    os.close(fd)
        except FileNotFoundError:
            self.root(directory["root_id"])
            return "already_absent"

    def plan_sidecars(self, files, directories):
        """Recognize exact adjacent sidecars; never sweep arbitrary folder contents."""
        video = {
            ".mkv",
            ".mp4",
            ".avi",
            ".mov",
            ".m4v",
            ".wmv",
            ".ts",
            ".m2ts",
            ".mpg",
            ".mpeg",
            ".webm",
            ".strm",
            ".iso",
        }
        sidecar = {
            ".srt",
            ".ass",
            ".ssa",
            ".sub",
            ".idx",
            ".vtt",
            ".smi",
            ".nfo",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".bif",
        }
        generic = {
            "movie.nfo",
            "tvshow.nfo",
            "season.nfo",
            "poster.jpg",
            "poster.png",
            "fanart.jpg",
            "fanart.png",
            "folder.jpg",
            "folder.png",
            "banner.jpg",
            "landscape.jpg",
            "clearlogo.png",
            "clearart.png",
            "disc.png",
            "thumb.jpg",
        }
        targets = {f["path"] for f in files}
        entries = {}
        total = 0
        for directory in directories:
            with self.directory(directory["path"]) as fd:
                names = sorted(os.listdir(fd))
                total += len(names)
                if total > 100_000 or len(names) > 10_000:
                    raise SafetyError("Folder preview exceeds its bounded enumeration limit")
                entries[directory["path"]] = [(name, os.stat(name, dir_fd=fd, follow_symlinks=False)) for name in names]
        closed = {}
        for directory in directories:  # deepest first
            path = directory["path"]
            closed[path] = all(
                closed.get(str(Path(path) / name), False)
                if stat.S_ISDIR(info.st_mode)
                else str(Path(path) / name) in targets
                if Path(name).suffix.lower() in video
                else not stat.S_ISLNK(info.st_mode)
                for name, info in entries[path]
            )
        result = []
        for directory in directories:
            path = directory["path"]
            media_stems = [
                (Path(name).stem.casefold(), str(Path(path) / name) in targets)
                for name, info in entries[path]
                if stat.S_ISREG(info.st_mode) and Path(name).suffix.lower() in video
            ]
            for name, info in entries[path]:
                candidate = str(Path(path) / name)
                if candidate in targets or not stat.S_ISREG(info.st_mode) or Path(name).suffix.lower() not in sidecar:
                    continue
                stem = Path(name).stem.casefold()
                matches = [
                    (prefix, selected)
                    for prefix, selected in media_stems
                    if stem == prefix or stem.startswith(prefix + ".") or stem.startswith(prefix + "-")
                ]
                longest = max((len(prefix) for prefix, _ in matches), default=0)
                matching_target = bool(matches) and all(
                    selected for prefix, selected in matches if len(prefix) == longest
                )
                if not matching_target and not (closed[path] and name.casefold() in generic):
                    continue
                identity = self.inspect(directory["root_id"], candidate)
                ident = (
                    "sidecar:"
                    + hashlib.sha256(
                        (directory["root_id"] + candidate + json.dumps(identity, sort_keys=True)).encode()
                    ).hexdigest()
                )
                result.append(
                    {
                        "id": ident,
                        "root_id": directory["root_id"],
                        "path": candidate,
                        "size": identity["size"],
                        "identity": json.dumps(identity, sort_keys=True, separators=(",", ":")),
                    }
                )
        return sorted(result, key=lambda file: (file["root_id"], file["path"]))

    def validate_sidecar_ownership(self, sidecar, snapshot):
        """A newly downloaded version must not lose artwork/subtitles shared with it."""
        media_extensions = {
            ".mkv",
            ".mp4",
            ".avi",
            ".mov",
            ".m4v",
            ".wmv",
            ".ts",
            ".m2ts",
            ".mpg",
            ".mpeg",
            ".webm",
            ".strm",
            ".iso",
        }
        generic = Path(sidecar["path"]).name.casefold() in {
            "movie.nfo",
            "tvshow.nfo",
            "season.nfo",
            "poster.jpg",
            "poster.png",
            "fanart.jpg",
            "fanart.png",
            "folder.jpg",
            "folder.png",
            "banner.jpg",
            "landscape.jpg",
            "clearlogo.png",
            "clearart.png",
            "disc.png",
            "thumb.jpg",
        }
        parent = Path(sidecar["path"]).parent
        known = {file["path"] for file in snapshot["files"]}
        directories = {directory["path"] for directory in snapshot.get("directories", [])}
        checked = 0
        for directory in snapshot.get("directories", []):
            path = Path(directory["path"])
            if path != parent and not (generic and path.is_relative_to(parent)):
                continue
            with self.directory(path) as fd:
                names = os.listdir(fd)
                checked += len(names)
                if checked > 100_000:
                    raise SafetyError("Sidecar ownership validation exceeds its bounded scope")
                for name in names:
                    full = str(path / name)
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if generic and (
                        stat.S_ISLNK(info.st_mode) or stat.S_ISDIR(info.st_mode) and full not in directories
                    ):
                        raise SafetyError("Folder gained unplanned content; sidecar cleanup refused")
                    if (
                        stat.S_ISREG(info.st_mode)
                        and Path(name).suffix.lower() in media_extensions
                        and full not in known
                    ):
                        stem = Path(name).stem.casefold()
                        title = Path(sidecar["path"]).stem.casefold()
                        if generic or title == stem or title.startswith(stem + ".") or title.startswith(stem + "-"):
                            raise SafetyError("Sidecar is shared with a remaining or newly downloaded version")
