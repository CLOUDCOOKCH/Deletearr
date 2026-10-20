import errno
import json
import os

import pytest

from deletarr.security import SafetyError


@pytest.mark.parametrize(
    "code,expected",
    [
        (errno.EACCES, "Permission denied for container UID"),
        (errno.EROFS, "mount is read-only"),
        (errno.ENOENT, "Directory not found inside the container"),
        (errno.ENOTDIR, "no symlink components"),
    ],
)
def test_root_authorization_explains_filesystem_failures(system, monkeypatch, code, expected):
    def fail(*args):
        raise OSError(code, "private exception details")

    fs = system.app.state.catalog.fs
    monkeypatch.setattr(fs, "register", fail)
    response = system.client.post(
        "/api/storage/roots", json={"path": "/media/movies", "confirmation": "AUTHORIZE THIS MEDIA ROOT"}
    )
    assert response.status_code == 409, response.text
    assert expected in response.json()["error"]
    assert "private exception details" not in response.text
    monkeypatch.setattr(fs, "reauthorize", fail)
    response = system.client.post(
        f"/api/storage/roots/{system.root_id}/reauthorize",
        json={"path": str(system.root), "confirmation": "AUTHORIZE THIS MEDIA ROOT"},
    )
    assert response.status_code == 409, response.text
    assert expected in response.json()["error"]


def test_rejects_root_config_overlap_and_traversal(system):
    for path in ["/", str(system.db.directory), str(system.root), str(system.root / "nested"), "/tmp/../etc"]:
        with pytest.raises((OSError, SafetyError)):
            system.fs.register(path)
    for path in ["/media/../../etc/passwd", "relative/file", "/media-other/file"]:
        with pytest.raises(SafetyError):
            system.fs.map("plex", path)


def test_mount_disappearance_fails_closed(system):
    root = system.db.one("SELECT * FROM roots")
    (system.root / root["sentinel"]).unlink()
    with pytest.raises(FileNotFoundError):
        system.fs.root(root["id"])
    assert not system.fs.snapshot()[0]["healthy"]


def test_symlink_leaf_and_parent_refused(system):
    external = system.temp / "unrelated"
    external.write_text("keep")
    (system.root / "escape.mkv").symlink_to(external)
    with pytest.raises(SafetyError):
        system.fs.inspect(system.root_id, str(system.root / "escape.mkv"))
    (system.root / "directory").symlink_to(system.temp, target_is_directory=True)
    with pytest.raises(OSError):
        system.fs.inspect(system.root_id, str(system.root / "directory" / "unrelated"))
    assert external.read_text() == "keep"


def test_hardlinks_and_exact_unlink(system):
    target = system.root / "movie.mkv"
    target.write_bytes(b"a" * 8192)
    linked = system.root / "keep.mkv"
    os.link(target, linked)
    expected = system.fs.inspect(system.root_id, str(target))
    assert expected["links"] == 2 and expected["reclaimable"] == 0
    system.fs.unlink({"root_id": system.root_id, "path": str(target), "identity": json.dumps(expected)})
    assert not target.exists() and linked.exists()


def test_changed_identity_blocks_unlink(system):
    target = system.root / "movie.mkv"
    target.write_text("old")
    identity = system.fs.inspect(system.root_id, str(target))
    target.unlink()
    target.write_text("new content")
    with pytest.raises(SafetyError):
        system.fs.unlink({"root_id": system.root_id, "path": str(target), "identity": identity})
    assert target.read_text() == "new content"
