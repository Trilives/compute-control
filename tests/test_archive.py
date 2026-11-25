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
    account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(home), pw_gid=os.getgid())
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
        if args[0] in ["useradd", "groupadd"]:
            raise AssertionError("Account creation must be simulated by the test")
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
    monkeypatch.setattr(remote, "receipt_path", lambda _, create=True: receipt)
    monkeypatch.setattr(remote, "safe_directory", directory)
    monkeypatch.setattr(remote, "user_exists", lambda _: present["account"])
    monkeypatch.setattr(
        remote,
        "local_user",
        lambda _: [
            "researcher",
            "x",
            str(os.getuid()),
            str(os.getgid()),
            "",
            str(home),
            "/bin/bash",
        ],
    )
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


def record_process(sandbox, pid=123, start_time=456, uid=None):
    uid = os.getuid() if uid is None else uid
    process = sandbox.root / "proc" / str(pid)
    process.mkdir(exist_ok=True)
    (process / "comm").write_text("training\n")
    (process / "status").write_text(f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    (process / "stat").write_text(f"{pid} (training) S " + "0 " * 18 + str(start_time))


def test_retirement_activity_names_blocking_processes_and_schedules(sandbox, monkeypatch):
    monkeypatch.setattr(remote, "active_processes", lambda *_: [123])
    record_process(sandbox)
    cron = sandbox.root / "var/spool/cron/crontabs/researcher"
    cron.parent.mkdir(parents=True)
    cron.touch()
    before = sandbox.receipt.read_bytes()

    report = remote.retirement_activity(sandbox.payload)
    assert report["processes"] == [{"pid": 123, "name": "training", "uid": os.getuid()}]
    assert "cron" in report["schedule_blocker"]
    assert "process_review" not in report
    assert sandbox.commands == []
    assert sandbox.receipt.read_bytes() == before

    with pytest.raises(remote.Refuse, match="身份不匹配"):
        remote.retirement_activity(sandbox.payload | {"username": "other"})


@pytest.mark.parametrize(
    "change",
    [
        None,
        "pid_reused",
        "foreign_process",
        "new_process",
        "reboot",
        "still_running",
        "shared_home",
        "external_only",
        "external_released",
    ],
)
def test_forced_recycle_checks_all_processes_before_signaling(sandbox, monkeypatch, change):
    boot_file = sandbox.root / "proc/sys/kernel/random/boot_id"
    boot_file.parent.mkdir(parents=True)
    boot_file.write_text("cb0589b3-a152-45c5-b31f-d5723b9b3a80")
    record_process(sandbox)
    own_uid, external_uid = os.getuid(), os.getuid() + 1
    own_gid, external_gid = os.getgid(), os.getgid() + 1
    busy, signaled = ([] if change == "external_only" else [123]), []
    handles_by_fd = {}
    monkeypatch.setattr(remote, "active_processes", lambda *_: busy.copy())
    monkeypatch.setattr(remote.shutil, "which", lambda _: None)
    if change in ("shared_home", "external_only", "external_released"):
        record_process(sandbox, pid=124, uid=external_uid)
        process = sandbox.root / "proc/124"
        (process / "fd").mkdir()
        (process / "cwd").symlink_to(sandbox.home)
        busy.append(124)

    def open_process(pid):
        handle = os.open(boot_file, os.O_RDONLY)
        handles_by_fd[handle] = pid
        return handle

    monkeypatch.setattr(remote.os, "pidfd_open", open_process, raising=False)
    monkeypatch.setattr(remote.signal, "pidfd_send_signal", lambda *_: None, raising=False)
    monkeypatch.setattr(remote.pwd, "getpwuid", lambda _: SimpleNamespace(pw_gid=external_gid))

    def stop(handles, uid, gid):
        pids = [handles_by_fd[fd] for fd in handles]
        assert (uid, gid) == ((own_uid, own_gid) if pids == [123] else (external_uid, external_gid))
        signaled.extend(pids)
        if change != "still_running":
            busy[:] = [pid for pid in busy if pid not in signaled]
            if change == "external_released":
                busy.remove(124)

    monkeypatch.setattr(remote, "signal_reviewed_processes", stop)
    review = remote.retirement_activity(sandbox.payload)["process_review"]
    assert [process["pid"] for process in review["processes"]] == busy
    if change == "pid_reused":
        record_process(sandbox, start_time=457)
    elif change == "foreign_process":
        record_process(sandbox, uid=os.getuid() + 1)
    elif change == "new_process":
        record_process(sandbox, pid=124)
        busy.append(124)
    elif change == "reboot":
        boot_file.write_text("b09a2e46-4417-4cc1-bc6a-3eb78e118eb4")
    elif change == "still_running":
        monkeypatch.setattr(remote, "time", SimpleNamespace(monotonic=iter((0, 11)).__next__))
    elif change == "external_released":
        (process / "cwd").unlink()

    payload = sandbox.payload | {"process_review": review}
    if change in ("pid_reused", "foreign_process", "new_process", "reboot", "still_running"):
        with pytest.raises(remote.Refuse, match="请重新检查活动"):
            remote.retire(payload)
        assert signaled == ([123] if change == "still_running" else [])
        assert not any(command[0] in ("usermod", "userdel") for command in sandbox.commands)
    else:
        assert remote.retire(payload)["state"] == "retired"
        expected = {"shared_home": [123, 124], "external_only": [124]}
        assert signaled == expected.get(change, [123])


def test_insufficient_disk_keeps_user_and_home(sandbox, monkeypatch):
    monkeypatch.setattr(remote.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(remote.Refuse, match="空间不足"):
        remote.retire(sandbox.payload)
    assert sandbox.present["account"] is not None
    assert sandbox.home.exists()
    assert not any(command[0] == "userdel" for command in sandbox.commands)
    assert json.loads(sandbox.receipt.read_text())["state"] == "quiesced"
    with pytest.raises(remote.Refuse, match="登录已停用，回收尚未完成"):
        remote.reconcile(sandbox.payload)


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
