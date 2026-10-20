"""Linux descriptor-relative exact-file access. Never walks a symlink or removes a tree."""

import contextlib
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
