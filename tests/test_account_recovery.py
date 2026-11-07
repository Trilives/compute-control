"""Recreation reserves quota and never overwrites a reused remote identity."""

import json

import pytest
from conftest import apply, machine, person
from test_worker import worker
from test_archive import sandbox  # noqa: F401

from compute_control.security import DomainError


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
    assert app.state.db.one("SELECT occupies FROM accounts WHERE id=?", (account_id,))["occupies"] == 0


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
    from types import SimpleNamespace
    def run(args, input=None, check=True):
        commands.append((args, input))
        if args[0] == "useradd":
            sandbox.present["account"] = SimpleNamespace(pw_uid=__import__('os').getuid(), pw_dir=str(sandbox.home))
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
    sandbox.present["account"] = None
    remote.cleanup(sandbox.payload)
    with pytest.raises(remote.Refuse):
        remote.restore(sandbox.payload)
