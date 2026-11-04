"""Exercise the actual tar/hash workflow in a temporary filesystem, never host accounts."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from compute_control import remote


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home/researcher"
    home.mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / ".ssh/authorized_keys").write_text("test-public-key")
    (home / "work.txt").write_text("valuable research\n")
    os.link(home / "work.txt", home / "hardlink")
    (home / "external").symlink_to("/outside-preserve-as-link")
    config = tmp_path / "etc"
    config.mkdir()
    (config / "shadow").write_text("researcher:!test-encrypted-hash:20000:0:99999:7:::\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "mounts").write_text("/dev/test / ext4 rw 0 0\n")
    receipt = tmp_path / "receipt.json"
    original = {"name": "researcher", "uid": os.getuid(), "home": str(home), "state": "active"}
    receipt.write_text(json.dumps(original))
    account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(home))
    present = {"account": account}
    commands = []
    real_run = remote.run

    def path(value=".", *parts):
        value = Path(value, *parts)
        text = str(value)
        if text.startswith(("/etc", "/proc", "/home", "/var")):
            return tmp_path / text.lstrip("/")
        return Path(value)

    def directory(value, create=False):
        result = Path(value)
        if create:
            result.mkdir(parents=True, exist_ok=True)
        return result

    def run(args, input=None, check=True):
        commands.append(args)
        if args[0] == "sshd":
            return "usepam yes\n"
        if args[0] == "userdel":
            present["account"] = None
            return ""
        if args[0] in ["usermod", "chpasswd"]:
            return ""
        if args[0] == "atq":
            return ""
        return real_run(args, input, check)

    monkeypatch.setattr(remote, "Path", path)
    monkeypatch.setattr(remote, "receipt_path", lambda _: receipt)
    monkeypatch.setattr(remote, "safe_directory", directory)
    monkeypatch.setattr(remote, "user_exists", lambda _: present["account"])
    monkeypatch.setattr(remote, "local_user", lambda _: ["researcher"])
    monkeypatch.setattr(remote.pwd, "getpwall", lambda: [])
    monkeypatch.setattr(remote, "active_processes", lambda *_: [])
    monkeypatch.setattr(remote, "run", run)
    payload = {
        "account_id": "a" * 32,
        "job_id": "b" * 32,
        "username": "researcher",
        "admin_username": "operator",
        "archive_root": str(tmp_path / "archives"),
    }
    return SimpleNamespace(
        home=home,
        payload=payload,
        receipt=receipt,
        present=present,
        commands=commands,
        root=tmp_path,
    )


def test_archive_actual_tar_hash_and_admin_cleanup(sandbox):
    result = remote.retire(sandbox.payload)
    assert result["state"] == "retired"
    assert sandbox.home.is_dir() and sandbox.present["account"] is None
    assert Path(result["archive"]).is_file()
    assert len(result["checksum"]) == 64
    assert (Path(result["archive"]).parent / "manifest.json").is_file()
    assert remote.cleanup(sandbox.payload)["state"] == "home_cleaned"
    assert not sandbox.home.exists()
    proof = json.loads(sandbox.receipt.read_text())
    assert proof["state"] == "cleaned"
    deleted = remote.delete_archive(
        sandbox.payload | {"archive_path": result["archive"], "checksum": result["checksum"]}
    )
    assert deleted["state"] == "archive_deleted"
    assert not Path(result["archive"]).exists()
    assert json.loads(sandbox.receipt.read_text())["archive_deleted"]


def test_busy_user_is_not_disabled_or_deleted(sandbox, monkeypatch):
    monkeypatch.setattr(remote, "active_processes", lambda *_: [123])
    with pytest.raises(remote.Refuse, match="仍被进程使用"):
        remote.retire(sandbox.payload)
    assert sandbox.commands == []
    assert sandbox.present["account"] is not None and sandbox.home.exists()


def test_insufficient_disk_keeps_user_and_home(sandbox, monkeypatch):
    monkeypatch.setattr(remote.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(remote.Refuse, match="空间不足"):
        remote.retire(sandbox.payload)
    assert sandbox.present["account"] is not None
    assert sandbox.home.exists()
    assert not any(command[0] == "userdel" for command in sandbox.commands)
    assert json.loads(sandbox.receipt.read_text())["state"] == "quiesced"


def test_tar_failure_never_deletes_user(sandbox, monkeypatch):
    previous = remote.run

    def fail(args, *rest, **kwargs):
        if args[0] == "tar":
            raise remote.Refuse("test archive failure")
        return previous(args, *rest, **kwargs)

    monkeypatch.setattr(remote, "run", fail)
    with pytest.raises(remote.Refuse):
        remote.retire(sandbox.payload)
    assert sandbox.present["account"] is not None and sandbox.home.exists()
    assert not any(command[0] == "userdel" for command in sandbox.commands)


def test_changed_home_or_corrupt_archive_blocks_cleanup(sandbox):
    result = remote.retire(sandbox.payload)
    (sandbox.home / "work.txt").write_text("new external work")
    with pytest.raises(remote.Refuse, match="内容变化"):
        remote.cleanup(sandbox.payload)
    assert sandbox.home.exists()
    Path(result["archive"]).write_bytes(b"corrupt")
    with pytest.raises(remote.Refuse, match="摘要不符"):
        remote.cleanup(sandbox.payload)
    assert sandbox.home.exists()


def test_archive_needed_for_home_cleanup_cannot_be_deleted(sandbox):
    result = remote.retire(sandbox.payload)
    with pytest.raises(remote.Refuse, match="清理依赖"):
        remote.delete_archive(
            sandbox.payload | {"archive_path": result["archive"], "checksum": result["checksum"]}
        )
    assert Path(result["archive"]).exists()


def test_restore_after_failure_uses_saved_state_without_logging_hash(sandbox, monkeypatch):
    monkeypatch.setattr(remote.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(remote.Refuse):
        remote.retire(sandbox.payload)
    result = remote.restore(sandbox.payload)
    assert result["state"] == "active"
    assert "test-encrypted-hash" not in json.dumps(result)
    assert all("test-encrypted-hash" not in " ".join(args) for args in sandbox.commands)


def test_xattrs_and_sparse_content_survive_real_tar(sandbox):
    import base64
    import tarfile

    sparse = sandbox.home / "sparse-data"
    with sparse.open("wb") as handle:
        handle.seek(8 * 1024 * 1024)
        handle.write(b"end")
    try:
        os.setxattr(sandbox.home / "work.txt", "user.compute-control-test", b"preserved")
    except OSError:
        pytest.skip("test filesystem does not support xattrs")
    result = remote.retire(sandbox.payload)
    manifest = json.loads((Path(result["archive"]).parent / "manifest.json").read_text())
    assert (
        manifest["work.txt"]["xattrs"]["user.compute-control-test"]
        == base64.b64encode(b"preserved").decode()
    )
    assert Path(result["archive"]).stat().st_size < sparse.stat().st_size
    with tarfile.open(result["archive"]) as archive:
        member = archive.getmember("./work.txt")
        assert member.pax_headers["SCHILY.xattr.user.compute-control-test"] == "preserved"
        content = archive.extractfile("./sparse-data")
        content.seek(8 * 1024 * 1024)
        assert content.read() == b"end"
