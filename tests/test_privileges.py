import json
from types import SimpleNamespace

import pytest
from conftest import apply, machine, person
from test_worker import MemoryMailbox

from compute_control import remote
from compute_control.security import DomainError
from compute_control.worker import Worker


def local_account(tmp_path, monkeypatch):
    path = tmp_path / "proof.json"
    proof = {"name": "researcher", "uid": 1001, "home": "/home/researcher", "state": "active"}
    path.write_text(json.dumps(proof))
    user = SimpleNamespace(pw_name=proof["name"], pw_uid=1001, pw_gid=1001, pw_dir=proof["home"])
    monkeypatch.setattr(remote, "receipt_path", lambda _: path)
    monkeypatch.setattr(remote, "user_exists", lambda _: user)
    monkeypatch.setattr(
        remote,
        "local_user",
        lambda _: ["researcher", "x", "1001", "1001", "", "/home/researcher", "/bin/bash"],
    )
    target = tmp_path / "grant"
    monkeypatch.setattr(remote, "privilege_path", lambda _: target)
    monkeypatch.setattr(remote.shutil, "which", lambda _: "/usr/sbin/visudo")
    payload = {
        "account_id": "a" * 32,
        "job_id": "b" * 32,
        "username": "researcher",
        "admin_username": "operator",
        "enabled": True,
    }
    return path, target, payload


def test_grant_uses_validated_password_required_sudoers_then_revoke_only_target(
    tmp_path, monkeypatch
):
    path, target, payload = local_account(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(remote, "run", lambda args: calls.append(args))
    monkeypatch.setattr(remote, "sudo_status", lambda name: {"is_admin": target.exists()})
    monkeypatch.setattr(
        remote.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=2000, gr_mem=[])
    )
    result = remote.set_privilege(payload)
    assert result["is_admin"] is True and target.read_text() == "researcher ALL=(ALL:ALL) ALL\n"
    assert calls[0][:3] == ["visudo", "-c", "-f"] and "NOPASSWD" not in target.read_text()
    payload["enabled"] = False
    assert remote.set_privilege(payload)["is_admin"] is False
    assert not target.exists()
    assert json.loads(path.read_text())["last_privilege_job"] == payload["job_id"]


def test_revoke_removes_only_sudo_wheel_membership_and_reports_other_policy(tmp_path, monkeypatch):
    _, _, payload = local_account(tmp_path, monkeypatch)
    payload["enabled"] = False
    calls = []
    monkeypatch.setattr(
        remote.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=2000, gr_mem=["researcher"])
    )
    monkeypatch.setattr(remote, "run", lambda args: calls.append(args))
    monkeypatch.setattr(remote, "sudo_status", lambda name: {"is_admin": True})
    with pytest.raises(remote.Refuse, match="未达目标"):
        remote.set_privilege(payload)
    assert calls == [
        ["gpasswd", "-d", "researcher", "sudo"],
        ["gpasswd", "-d", "researcher", "wheel"],
    ]


def test_privilege_refuses_protected_or_changed_identity_before_any_mutation(tmp_path, monkeypatch):
    path, _, payload = local_account(tmp_path, monkeypatch)
    before = path.read_bytes()
    payload["admin_username"] = "researcher"
    with pytest.raises(remote.Refuse):
        remote.set_privilege(payload)
    assert path.read_bytes() == before
    payload["admin_username"] = "operator"
    monkeypatch.setattr(
        remote,
        "user_exists",
        lambda _: SimpleNamespace(pw_uid=1002, pw_gid=1002, pw_dir="/home/researcher"),
    )
    with pytest.raises(remote.Refuse):
        remote.set_privilege(payload)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "returncode,stdout,stderr,expected",
    [
        (0, "User researcher may run the following commands:\n    (ALL : ALL) ALL\n", "", True),
        (1, "", "User researcher is not allowed to run sudo on host.\n", False),
        (1, "", "sudo: error initializing policy plugin", None),
    ],
)
def test_sudo_status_distinguishes_denial_from_failed_probe(
    monkeypatch, returncode, stdout, stderr, expected
):
    calls = []

    def probe(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(remote.subprocess, "run", probe)
    assert remote.sudo_status("researcher")["is_admin"] is expected
    assert calls == [["sudo", "-n", "-l", "-U", "researcher"]]


def test_interrupted_privilege_change_reconciles_actual_state_before_retry(
    app, client, monkeypatch
):
    owner, target = person(app), machine(app)
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    create = apply(app, owner, target)
    runner.process_job(create)
    account_id = create["account_id"]
    job = app.state.service.set_privilege(account_id, False)
    original = app.state.service.remote.execute

    def execute(host, action, data=None):
        if action == "set_privilege":
            raise DomainError("其他规则仍授权，需核对")
        if action == "reconcile":
            return {
                "state": "active",
                "uid": 1001,
                "home": "/home/" + data["username"],
                "is_admin": True,
                "last_privilege_job": job["id"],
            }
        return original(host, action, data)

    monkeypatch.setattr(app.state.service.remote, "execute", execute)
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE id=?", (job["id"],)))
    assert (
        app.state.db.one("SELECT state FROM accounts WHERE id=?", (account_id,))["state"]
        == "needs_reconcile"
    )
    check = app.state.service.admin_job("reconcile", account_id)
    runner.process_job(check)
    assert app.state.db.one("SELECT state FROM jobs WHERE id=?", (job["id"],))["state"] == "failed"
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    assert account["is_admin"] == 1 and account["state"] == "active"
    assert app.state.service.set_privilege(account_id, False)["id"]
