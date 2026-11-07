"""Recreation reserves quota and never overwrites a reused remote identity."""

import json
import os
from types import SimpleNamespace

import pytest
from conftest import apply, machine, person
from test_archive import sandbox as archive_sandbox
from test_worker import worker

from compute_control.security import DomainError


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    return archive_sandbox.__wrapped__(tmp_path, monkeypatch)


def recycled(app):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    runner = worker(app)
    runner.process_job(job)
    runner.process_job(app.state.service.retire_account({"role": "admin"}, job["account_id"]))
    return owner, job["account_id"], runner


def test_recreate_reserves_quota_and_restores_original_record(app, client):
    owner, account_id, runner = recycled(app)
    before = app.state.db.one("SELECT credential_cipher FROM accounts WHERE id=?", (account_id,))
    response = client.post(f"/api/accounts/{account_id}/restore", json={"confirmed": True})
    assert response.status_code == 202, response.text
    assert app.state.service.person(owner["id"])["used"] == 1
    runner.process_job(response.json())
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    assert account["state"] == "active" and account["occupies"] == 1
    assert account["credential_cipher"] == before["credential_cipher"]
    assert len(app.state.db.all("SELECT * FROM accounts")) == 1
    assert app.state.db.all("SELECT * FROM archives")


def test_recreate_rejects_quota_and_does_not_touch_remote(app):
    owner, account_id, _ = recycled(app)
    app.state.service.save_person({"name": owner["name"], "enabled": True, "quota": 0}, owner["id"])
    before = len(app.state.service.remote.calls)
    with pytest.raises(DomainError, match="额度"):
        app.state.service.admin_job("restore", account_id)
    assert len(app.state.service.remote.calls) == before
    assert (
        app.state.db.one("SELECT occupies FROM accounts WHERE id=?", (account_id,))["occupies"] == 0
    )


def test_recreation_uncertainty_keeps_quota_and_blocks_cleanup(app):
    owner, account_id, runner = recycled(app)
    job = app.state.service.admin_job("restore", account_id)
    app.state.service.remote.error = DomainError("连接中断", 502)
    runner.process_job(job)
    assert app.state.service.person(owner["id"])["used"] == 1
    with pytest.raises(DomainError):
        app.state.service.admin_job("cleanup", account_id)


def test_remote_recreates_deleted_account_with_saved_hash_and_uid(sandbox, monkeypatch):
    from compute_control import remote

    remote.retire(sandbox.payload)
    commands = []
    previous = remote.run
    old = sandbox.present["account"]
    assert old is None

    def run(args, input=None, check=True):
        commands.append((args, input))
        if args[0] == "useradd":
            sandbox.present["account"] = SimpleNamespace(
                pw_uid=os.getuid(), pw_dir=str(sandbox.home)
            )
            return ""
        return previous(args, input, check)

    monkeypatch.setattr(remote, "run", run)
    monkeypatch.setattr(remote, "sudo_status", lambda _: {"is_admin": False})
    result = remote.restore(sandbox.payload)
    assert result["state"] == "active"
    assert sandbox.home.joinpath("work.txt").read_text() == "valuable research\n"
    assert any(args[0] == "useradd" and "--no-create-home" in args for args, _ in commands)
    assert any(args[0] == "chpasswd" and "test-encrypted-hash" in stdin for args, stdin in commands)
    assert "test-encrypted-hash" not in json.dumps(result)
    assert all("test-encrypted-hash" not in " ".join(args) for args, _ in commands)


def test_remote_recreation_rejects_reused_name_and_cleaned_home(sandbox):
    from compute_control import remote

    old = sandbox.present["account"]
    remote.retire(sandbox.payload)
    sandbox.present["account"] = old
    before = list(sandbox.commands)
    with pytest.raises(remote.Refuse):
        remote.restore(sandbox.payload)
    assert sandbox.commands == before


def test_remote_restores_cleaned_home_from_verified_archive(sandbox, monkeypatch):
    from compute_control import remote

    result = remote.retire(sandbox.payload)
    remote.cleanup(sandbox.payload)
    assert not sandbox.home.exists()
    commands = []
    previous = remote.run

    def run(args, input=None, check=True):
        commands.append(args)
        if args[0] == "useradd":
            sandbox.present["account"] = SimpleNamespace(
                pw_uid=os.getuid(), pw_dir=str(sandbox.home)
            )
            return ""
        return previous(args, input, check)

    monkeypatch.setattr(remote, "run", run)
    monkeypatch.setattr(remote, "sudo_status", lambda _: {"is_admin": False})
    assert remote.restore(sandbox.payload)["state"] == "active"
    assert sandbox.home.joinpath("work.txt").read_text() == "valuable research\n"
    assert (
        sandbox.home.joinpath("work.txt").stat().st_ino
        == sandbox.home.joinpath("hardlink").stat().st_ino
    )
    assert sandbox.home.joinpath("external").is_symlink()
    assert any(args[0] == "tar" and "--extract" in args for args in commands)
    assert __import__("pathlib").Path(result["archive"]).is_file()


def test_corrupt_archive_cannot_recreate_account_or_directory(sandbox):
    from pathlib import Path

    from compute_control import remote

    result = remote.retire(sandbox.payload)
    remote.cleanup(sandbox.payload)
    Path(result["archive"]).write_bytes(b"corrupt")
    before = list(sandbox.commands)
    with pytest.raises(remote.Refuse, match="摘要"):
        remote.restore(sandbox.payload)
    assert not sandbox.home.exists() and sandbox.present["account"] is None
    assert sandbox.commands == before


def test_cleaned_account_recovery_from_api(app, client):
    owner, account_id, runner = recycled(app)
    response = client.post(f"/api/accounts/{account_id}/restore", json={"confirmed": True})
    assert response.status_code == 202
    runner.process_job(response.json())
    assert app.state.service.person(owner["id"])["used"] == 1
    assert (
        app.state.db.one("SELECT state FROM accounts WHERE id=?", (account_id,))["state"]
        == "active"
    )


def test_concurrent_recovery_requests_reserve_only_one_quota_slot(app):
    from concurrent.futures import ThreadPoolExecutor

    owner, account_id, _ = recycled(app)

    def submit(_):
        try:
            return app.state.service.admin_job("restore", account_id)["id"]
        except DomainError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(bool(item) for item in pool.map(submit, range(4))) == 1
    assert app.state.service.person(owner["id"])["used"] == 1


def test_remote_recovery_refuses_reused_uid_before_extracting(sandbox, monkeypatch):
    from compute_control import remote

    remote.retire(sandbox.payload)
    remote.cleanup(sandbox.payload)
    monkeypatch.setattr(
        remote.pwd,
        "getpwall",
        lambda: [SimpleNamespace(pw_name="other", pw_uid=os.getuid(), pw_dir="/home/other")],
    )
    with pytest.raises(remote.Refuse, match="UID"):
        remote.restore(sandbox.payload)
    assert not sandbox.home.exists()


def test_worker_full_retirement_and_archive_recovery_preserves_real_files(
    app, sandbox, monkeypatch
):
    from compute_control import remote

    owner, target = person(app), machine(app)
    runner = worker(app)
    created = apply(app, owner, target)
    runner.process_job(created)
    previous = app.state.service.remote.execute
    real_run = remote.run

    def execute(machine, action, data=None):
        if action in ["retire", "cleanup", "restore"]:
            return getattr(remote, action)(
                dict(data, admin_username="operator", archive_root=str(sandbox.root / "archives"))
            )
        return previous(machine, action, data)

    def run(args, input=None, check=True):
        if args[0] == "useradd":
            sandbox.present["account"] = SimpleNamespace(
                pw_uid=os.getuid(), pw_dir=str(sandbox.home)
            )
            return ""
        return real_run(args, input, check)

    monkeypatch.setattr(remote, "run", run)
    monkeypatch.setattr(remote, "sudo_status", lambda _: {"is_admin": False})
    app.state.service.remote.execute = execute
    runner.process_job(app.state.service.retire_account({"role": "admin"}, created["account_id"]))
    assert not sandbox.home.exists() and sandbox.present["account"] is None
    assert app.state.db.one("SELECT state FROM accounts")["state"] == "retired"
    runner.process_job(app.state.service.admin_job("restore", created["account_id"]))
    assert app.state.db.one("SELECT state FROM accounts")["state"] == "active"
    assert sandbox.home.joinpath("work.txt").read_text() == "valuable research\n"
    assert app.state.service.person(owner["id"])["used"] == 1


def test_restore_never_overwrites_existing_directory_or_ignores_short_storage(sandbox, monkeypatch):
    from compute_control import remote

    remote.retire(sandbox.payload)
    remote.cleanup(sandbox.payload)
    sandbox.home.mkdir()
    sandbox.home.joinpath("new-owner-work").write_text("keep this")
    with pytest.raises(remote.Refuse, match="禁止覆盖"):
        remote.restore(sandbox.payload)
    assert sandbox.home.joinpath("new-owner-work").read_text() == "keep this"
    sandbox.home.rename(sandbox.home.with_name("occupied"))
    monkeypatch.setattr(remote.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(remote.Refuse, match="空间不足"):
        remote.restore(sandbox.payload)
    assert sandbox.present["account"] is None and not sandbox.home.exists()


@pytest.mark.parametrize("variant", ["traversal", "duplicate", "special", "hardlink", "incomplete"])
def test_unsafe_archive_is_rejected_before_extraction(sandbox, variant):
    import hashlib
    import tarfile
    from pathlib import Path

    from compute_control import remote

    result = remote.retire(sandbox.payload)
    remote.cleanup(sandbox.payload)
    archive = Path(result["archive"])
    with tarfile.open(archive, "w") as contents:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        contents.addfile(root)
        if variant == "duplicate":
            contents.addfile(root)
        elif variant != "incomplete":
            entry = tarfile.TarInfo("../escape" if variant == "traversal" else "work.txt")
            if variant == "special":
                entry.type = tarfile.FIFOTYPE
            elif variant == "hardlink":
                entry.type, entry.linkname = tarfile.LNKTYPE, "../escape"
            contents.addfile(entry)
    proof = json.loads(sandbox.receipt.read_text())
    proof["checksum"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    sandbox.receipt.write_text(json.dumps(proof))
    before = list(sandbox.commands)
    with pytest.raises(remote.Refuse):
        remote.restore(sandbox.payload)
    assert sandbox.commands == before and not sandbox.home.exists()


def test_recreation_rebuilds_removed_primary_group_without_touching_other_users(
    sandbox, monkeypatch
):
    from compute_control import remote

    remote.retire(sandbox.payload)

    def missing(_):
        raise KeyError

    monkeypatch.setattr(remote.grp, "getgrgid", missing)
    monkeypatch.setattr(remote.grp, "getgrnam", missing)
    original = remote.run
    commands = []

    def run(args, input=None, check=True):
        commands.append(args)
        if args[0] == "groupadd":
            return ""
        if args[0] == "useradd":
            sandbox.present["account"] = SimpleNamespace(
                pw_uid=os.getuid(), pw_dir=str(sandbox.home)
            )
            return ""
        return original(args, input, check)

    monkeypatch.setattr(remote, "run", run)
    monkeypatch.setattr(remote, "sudo_status", lambda _: {"is_admin": False})
    assert remote.restore(sandbox.payload)["state"] == "active"
    assert commands[0] == ["groupadd", "--gid", str(os.getgid()), "--", "researcher"]
