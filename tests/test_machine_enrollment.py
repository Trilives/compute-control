"""Automatic first trust and non-destructive machine enrollment failures."""

from compute_control.security import DomainError


def machine_input():
    return {
        "name": "测试节点",
        "host": "192.0.2.100",
        "port": 22,
        "username": "operator",
        "ssh_password": "example-ssh-password",
        "sudo_password": "example-sudo-password",
        "archive_root": "/var/lib/compute-control/archives",
        "available": False,
    }


def test_add_without_manual_fingerprint_uses_server_observation(client, app):
    data = machine_input()
    response = client.post("/api/admin/machines", json=data)
    assert response.status_code == 201
    machine = app.state.db.one("SELECT * FROM machines WHERE id=?", (response.json()["id"],))
    assert machine["fingerprint"] == "SHA256:" + "A" * 43
    assert machine["host_key"] == "ssh-ed25519 test-key"
    assert "example-ssh-password" not in response.text


def test_failure_after_first_observation_does_not_save_machine(client, app):
    app.state.service.remote.error = DomainError("SSH 主机指纹变化或不匹配", 502)
    response = client.post("/api/admin/machines", json=machine_input())
    assert response.status_code == 502
    assert app.state.db.all("SELECT id FROM machines") == []


def test_credential_update_preserves_existing_pin(client, app, monkeypatch):
    data = machine_input()
    machine_id = client.post("/api/admin/machines", json=data).json()["id"]
    previous_execute = app.state.service.remote.execute

    def check_pin(machine, action, payload):
        assert machine["fingerprint"] == "SHA256:" + "A" * 43
        return previous_execute(machine, action, payload)

    monkeypatch.setattr(app.state.service.remote, "execute", check_pin)
    response = client.put(
        "/api/admin/machines/" + machine_id, json=data | {"fingerprint": "SHA256:" + "B" * 43}
    )
    assert response.status_code == 200
    assert (
        app.state.db.one("SELECT fingerprint FROM machines WHERE id=?", (machine_id,))[
            "fingerprint"
        ]
        == "SHA256:" + "A" * 43
    )


def test_unavailable_machine_shows_specific_cause(client, app):
    import copy

    from conftest import REPORT

    report = copy.deepcopy(REPORT)
    report["identity"] = "test-nss-host"
    report["capabilities"].update(
        can_create=False, local_accounts_only=False, block_reasons=["账号来源需要核查：sss"]
    )
    app.state.service.remote.execute = lambda *args: copy.deepcopy(report)
    machine_id = client.post("/api/admin/machines", json=machine_input()).json()["id"]
    result = client.patch("/api/admin/machines/" + machine_id, json={"available": True})
    assert result.status_code == 400
    assert "账号来源需要核查：sss" in result.json()["detail"]
    assert client.get("/api/machines").json()[0]["creation_blockers"] == ["账号来源需要核查：sss"]


def test_machine_login_admin_is_registered_without_applicant_or_quota(app, client):
    from conftest import machine

    target = machine(app)
    report = app.state.service.remote.execute(target, "inspect")
    report["users"] = [
        {"username": target["username"], "uid": 1000, "home": "/home/" + target["username"]},
        {
            "username": "external_admin",
            "uid": 1002,
            "home": "/home/external_admin",
            "is_admin": True,
        },
    ]
    report["processes"] = [
        {"username": target["username"], "pid": "123", "name": "python", "memory_mib": "10"}
    ]
    app.state.service.save_snapshot(target["id"], report, False)
    result = client.get("/api/machines").json()[0]
    admin, other = result["snapshot"]["users"]
    assert admin["registered"] is True and admin["is_machine_admin"] is True
    assert admin["registration_source"] == "machine_admin" and admin["person_name"] == "机器管理员"
    assert other["registered"] is False and other["registration_source"] is None
    assert app.state.db.all("SELECT * FROM accounts") == []
    assert app.state.db.all("SELECT * FROM applicants") == []


def test_root_is_hidden_from_machine_and_ledger_but_preserved_for_protection(app, client):
    import json

    from conftest import machine, person

    from compute_control.security import DomainError
    from compute_control.services import new_id

    target, owner = machine(app), person(app)
    report = app.state.service.remote.execute(target, "inspect")
    report["users"] = [
        {"username": "root", "uid": 0, "home": "/root"},
        {"username": target["username"], "uid": 1000, "home": "/home/" + target["username"]},
    ]
    report["processes"] = [
        {"username": "root", "pid": "123"},
        {"username": target["username"], "pid": "456"},
    ]
    report["active_user_processes"] = [{"username": "root", "pid": 123}]
    report["existing_usernames"] = ["root", target["username"]]
    app.state.service.save_snapshot(target["id"], report, False)
    public = client.get("/api/machines").json()[0]["snapshot"]
    for field in ["users", "processes", "active_user_processes"]:
        assert all(item["username"] != "root" for item in public[field])
    assert "root" not in public["existing_usernames"]
    stored = json.loads(
        app.state.db.one("SELECT snapshot FROM machines WHERE id=?", (target["id"],))["snapshot"]
    )
    assert "root" in stored["existing_usernames"]
    import pytest

    with pytest.raises(DomainError):
        app.state.service.request_account(
            {"role": "applicant", "person_id": owner["id"]}, target["id"], "root", "123", new_id()
        )
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO accounts(id,person_id,machine_id,username,state,occupies,created) VALUES (?,?,?,?,?,?,?)",
            (new_id(), owner["id"], target["id"], "root", "retired", 0, 1),
        )
    assert client.get("/api/accounts?include_cleared=true").json() == []
