"""Management password rotation, authorization and interrupted-connection recovery."""

import json

import pytest
from conftest import ADMIN_PASSWORD, FakeRemote, apply, login, machine, person
from fastapi.testclient import TestClient

from compute_control.security import DomainError

NEW_PASSWORD = "new-management-password-123"


def change(password=NEW_PASSWORD, current=ADMIN_PASSWORD):
    return {"current_password": current, "new_password": password, "confirm_password": password}


class PasswordRemote(FakeRemote):
    def __init__(self, vault):
        super().__init__()
        self.vault, self.actual, self.mode, self.receipt = (
            vault,
            "example-ssh-credential",
            "ok",
            None,
        )
        self.changes = 0

    def execute(self, target, action, data=None):
        credentials = self.vault.decrypt(target["credentials"])
        if credentials["ssh_password"] != self.actual:
            raise DomainError("test authentication refused", 502)
        if action == "management_password":
            self.changes += 1
            if self.mode == "refuse":
                self.receipt = "failed"
                raise DomainError("private-remote-diagnostic", 502)
            self.actual, self.receipt = data["password"], "changed"
            if self.mode == "disconnect":
                raise OSError("private-remote-diagnostic")
            return {"state": "changed"}
        if action == "management_password_status":
            return {"state": self.receipt or "unknown"}
        if action == "inspect" and self.mode == "unreachable" and self.changes:
            raise OSError("private-remote-diagnostic")
        return super().execute(target, action, data)


def test_web_password_requires_current_secret_and_revokes_only_admin_sessions(app, client):
    person(app)
    with TestClient(app) as other, TestClient(app) as applicant:
        login(other)
        login(applicant, "applicant", "张三", "")
        assert client.put("/api/admin/password", json=change(current="wrong")).status_code == 400
        assert client.get("/api/auth/me").json()["role"] == "admin"
        for payload in [
            change("short"),
            change() | {"confirm_password": "different"},
            change(ADMIN_PASSWORD),
        ]:
            assert client.put("/api/admin/password", json=payload).status_code == 400
        response = client.put("/api/admin/password", json=change())
        assert response.status_code == 200 and response.json()["role"] == "anonymous"
        assert other.get("/api/accounts").status_code == 401
        assert applicant.get("/api/accounts").status_code == 200
        assert client.get("/api/auth/me").json()["role"] == "anonymous"
        with pytest.raises(DomainError):
            app.state.auth.login("admin", "admin", ADMIN_PASSWORD, "test")
        assert app.state.auth.login("admin", "admin", NEW_PASSWORD, "test") is None
    assert NEW_PASSWORD.encode() not in app.state.db.path.read_bytes()
    assert app.state.db.one("SELECT action FROM audit_logs WHERE action='admin.password.change'")


def test_password_endpoints_require_admin_csrf_and_reauthentication(app, client):
    target = machine(app)
    url = f"/api/admin/machines/{target['id']}/password"
    assert client.post(url, json=change(), headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert (
        client.put(
            "/api/admin/password", json=change(), headers={"X-CSRF-Token": "forged"}
        ).status_code
        == 403
    )
    assert client.post(url, json=change(current="wrong")).status_code == 400
    assert client.post(url, json=change() | {"confirm_password": "different"}).status_code == 400
    for invalid in ["bad:password", "bad\npassword", "bad\rpassword", "bad\x00password", "x" * 257]:
        response = client.post(url, json=change(invalid))
        assert response.status_code in (400, 422) and invalid not in response.text
    assert (
        client.post("/api/admin/machines/" + "f" * 32 + "/password", json=change()).status_code
        == 404
    )
    person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        for path, method in [
            (url, user.post),
            (url + "/check", user.post),
            ("/api/admin/password", user.put),
        ]:
            assert method(path, json=change()).status_code == 403
    with TestClient(app) as guest:
        assert guest.post(url, json=change()).status_code == 401


@pytest.mark.parametrize("mode", ["ok", "disconnect", "refuse"])
def test_machine_change_verifies_new_connection_and_never_replays(app, client, mode):
    target = machine(app)
    remote = PasswordRemote(app.state.service.secrets)
    app.state.service.remote = remote
    remote.mode = mode
    response = client.post(f"/api/admin/machines/{target['id']}/password", json=change())
    assert response.status_code == 200
    result = response.json()
    assert result["state"] == ("failed" if mode == "refuse" else "verified")
    current = app.state.db.one("SELECT * FROM machines WHERE id=?", (target["id"],))
    saved = app.state.service.secrets.decrypt(current["credentials"])
    assert saved["ssh_password"] == ("example-ssh-credential" if mode == "refuse" else NEW_PASSWORD)
    assert saved["sudo_password"] == "example-sudo-credential"
    assert remote.changes == 1
    assert NEW_PASSWORD not in json.dumps(client.get("/api/machines").json())
    assert NEW_PASSWORD.encode() not in app.state.db.path.read_bytes()
    assert "private-remote-diagnostic" not in response.text


def test_pending_change_survives_restart_and_blocks_competing_work(app, client):
    target = machine(app)
    remote = PasswordRemote(app.state.service.secrets)
    app.state.service.remote = remote
    remote.mode = "unreachable"
    url = f"/api/admin/machines/{target['id']}/password"
    response = client.post(url, json=change())
    assert response.status_code == 202 and response.json()["state"] == "pending"
    assert client.get("/api/machines").json()[0]["management_password_pending"] is True
    assert client.post(url, json=change()).status_code == 409
    owner = person(app)
    with pytest.raises(DomainError, match="密码"):
        apply(app, owner, target)
    from compute_control.db import Database
    from compute_control.services import Service

    app.state.service.db = Database(app.state.config.runtime_dir)
    restored = Service(app.state.service.db, app.state.service.secrets, remote, app.state.config)
    remote.mode = "ok"
    assert restored.check_management_password(target["id"])["state"] == "verified"
    assert remote.changes == 1
    assert client.get("/api/machines").json()[0]["management_password_pending"] is False


def test_same_login_and_sudo_password_rotate_together_and_busy_jobs_refuse(app, client):
    target = machine(app)
    vault = app.state.service.secrets
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "UPDATE machines SET credentials=? WHERE id=?",
            (
                vault.encrypt(
                    {
                        "ssh_password": "example-ssh-credential",
                        "sudo_password": "example-ssh-credential",
                    }
                ),
                target["id"],
            ),
        )
    remote = PasswordRemote(vault)
    app.state.service.remote = remote
    url = f"/api/admin/machines/{target['id']}/password"
    job = apply(app, person(app), target)
    assert client.post(url, json=change()).status_code == 409
    assert remote.changes == 0
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job["id"],))
    assert client.post(url, json=change()).json()["state"] == "verified"
    current = app.state.db.one("SELECT credentials FROM machines WHERE id=?", (target["id"],))
    saved = vault.decrypt(current["credentials"])
    assert saved["ssh_password"] == saved["sudo_password"] == NEW_PASSWORD
    assert saved["separate_sudo_password"] is False


def test_invalid_existing_credentials_do_not_reserve_change_or_leak_errors(app, client):
    target = machine(app)
    remote = PasswordRemote(app.state.service.secrets)
    remote.actual = "out-of-band-password"
    app.state.service.remote = remote
    response = client.post(f"/api/admin/machines/{target['id']}/password", json=change())
    assert response.status_code == 502 and "test authentication" not in response.text
    assert remote.changes == 0
    assert not app.state.db.all("SELECT * FROM machine_password_changes")


def test_pending_change_cannot_be_overwritten_by_machine_update(app, client):
    target = machine(app)
    remote = PasswordRemote(app.state.service.secrets)
    app.state.service.remote = remote
    remote.mode = "unreachable"
    url = f"/api/admin/machines/{target['id']}/password"
    assert client.post(url, json=change()).status_code == 202
    remote.mode = "ok"
    response = client.put(
        f"/api/admin/machines/{target['id']}",
        json={
            "name": target["name"],
            "host": target["host"],
            "port": 22,
            "username": "operator",
            "ssh_password": NEW_PASSWORD,
            "sudo_password": "example-sudo-credential",
            "available": True,
        },
    )
    assert response.status_code == 409
    assert client.post(url + "/check").json()["state"] == "verified"
