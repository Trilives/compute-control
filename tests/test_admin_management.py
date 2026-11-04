import sqlite3
import time

from conftest import apply, login, machine, person
from fastapi.testclient import TestClient
from test_worker import TEST_MACHINE_CREDENTIAL, MemoryMailbox

from compute_control.db import SCHEMA, Database
from compute_control.worker import Worker


def active_account(app, owner=None, target=None):
    owner = owner or person(app)
    target = target or machine(app)
    job = apply(app, owner, target)
    Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox()).process_job(job)
    return owner, target, job["account_id"]


def test_delete_name_revokes_session_but_preserves_accounts(app, client):
    owner, target, account_id = active_account(app)
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        assert client.delete("/api/admin/applicants/" + owner["id"]).status_code == 200
        assert client.get("/api/admin/applicants").json() == []
        assert user.get("/api/accounts").status_code == 401
        assert client.get("/api/accounts").json()[0]["id"] == account_id
        rows = client.get("/api/admin/applicants?include_deleted=true").json()
        assert rows[0]["deleted_at"] and not rows[0]["enabled"]
        restored = client.post("/api/admin/applicants", json={"name": owner["name"]}).json()
        assert restored["id"] == owner["id"] and restored["used"] == 1


def test_contact_is_plain_text_and_applicant_cannot_edit(app, client):
    text = "管理员：测试\n分机 123\n<script>alert(1)</script>"
    assert client.put("/api/admin/settings/contact", json={"text": text}).status_code == 200
    person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        assert user.get("/api/auth/me").json()["admin_contact"] == text
        assert user.put("/api/admin/settings/contact", json={"text": "forged"}).status_code == 403
    assert client.get("/api/admin/settings").json()["admin_contact"] == text
    assert client.put("/api/admin/settings/contact", json={"text": "x" * 2001}).status_code == 422


def test_password_encrypted_hidden_until_owner_inactive(app, client):
    owner, target, account_id = active_account(app)
    row = app.state.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    assert TEST_MACHINE_CREDENTIAL not in row["credential_cipher"]
    assert "credential_cipher" not in client.get("/api/accounts").json()[0]
    assert (
        client.post(
            "/api/admin/accounts/" + account_id + "/password/reveal", json={"confirmed": True}
        ).status_code
        == 403
    )
    client.delete("/api/admin/applicants/" + owner["id"])
    result = client.post(
        "/api/admin/accounts/" + account_id + "/password/reveal", json={"confirmed": True}
    )
    assert result.json()["password"] == TEST_MACHINE_CREDENTIAL
    assert app.state.db.one("SELECT id FROM audit_logs WHERE action='account.password.reveal'")
    assert TEST_MACHINE_CREDENTIAL not in str(app.state.db.all("SELECT * FROM audit_logs"))


def test_reassignment_moves_ownership_and_obeys_target_quota(app, client):
    owner, target, account_id = active_account(app)
    receiver = person(app, "李四", quota=0)
    endpoint = "/api/admin/accounts/" + account_id + "/assignment"
    assert client.put(endpoint, json={"person_id": receiver["id"]}).status_code == 409
    client.put("/api/admin/applicants/" + receiver["id"], json={"name": "李四", "quota": 3})
    calls = len(app.state.service.remote.calls)
    assert client.put(endpoint, json={"person_id": receiver["id"]}).status_code == 200
    assert len(app.state.service.remote.calls) == calls
    assert app.state.service.person(owner["id"])["used"] == 0
    assert app.state.service.person(receiver["id"])["used"] == 1
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        assert user.get("/api/accounts").json() == []
        login(user, "applicant", receiver["name"], "")
        assert user.get("/api/accounts").json()[0]["id"] == account_id
        assert user.put(endpoint, json={"person_id": owner["id"]}).status_code == 403


def test_explicit_legacy_adoption_does_not_create_user(app, client):
    owner, target = person(app), machine(app)
    report = app.state.service.remote.execute(target, "inspect")
    report["users"] = [{"username": "legacy", "uid": 1007, "home": "/home/legacy"}]
    app.state.service.save_snapshot(target["id"], report, False)
    response = client.post(
        "/api/admin/accounts/adopt",
        json={
            "machine_id": target["id"],
            "username": "legacy",
            "person_id": owner["id"],
        },
    )
    assert response.status_code == 202
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE id=?", (response.json()["id"],)))
    account = client.get("/api/accounts").json()[0]
    assert account["source"] == "imported" and account["state"] == "active"
    assert app.state.service.remote.calls[-1][1] == "adopt"
    client.delete("/api/admin/applicants/" + owner["id"])
    result = client.post(
        "/api/admin/accounts/" + account["id"] + "/password/reveal", json={"confirmed": True}
    )
    assert result.status_code == 409 and "从未留存" in result.json()["detail"]
    assert (
        client.put(
            "/api/admin/accounts/" + account["id"] + "/password/record",
            json={"password": "known-old-password"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/admin/accounts/" + account["id"] + "/password/reveal", json={"confirmed": True}
        ).json()["password"]
        == "known-old-password"
    )


def test_password_reset_job_never_returns_cipher_and_updates_after_success(app, client):
    owner, target, account_id = active_account(app)
    response = client.post(
        "/api/admin/accounts/" + account_id + "/password/reset", json={"password": "123"}
    )
    assert response.status_code == 202 and "params" not in response.json()
    assert "credential_cipher" not in client.get("/api/jobs").text
    job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (response.json()["id"],))
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox("123"))
    runner.process_job(job)
    client.delete("/api/admin/applicants/" + owner["id"])
    result = client.post(
        "/api/admin/accounts/" + account_id + "/password/reveal", json={"confirmed": True}
    )
    assert result.json()["password"] == "123"


def test_monthly_count_excludes_ssh_admin_and_root(app):
    target = machine(app)
    with app.state.db.connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO login_events VALUES (?,?,?)",
            [
                (target["id"], name, time.time())
                for name in ["operator", "root", "legacy", "worker"]
            ],
        )
    result = app.state.service.visible_machines({"role": "admin"})[0]
    assert result["active_users_30d"] == 2


def test_v1_migration_keeps_data_and_creates_consistent_backup(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    schema = (
        SCHEMA.replace(",\n contact TEXT NOT NULL DEFAULT ''", "")
        .replace(", deleted_at REAL", "")
        .replace(", credential_cipher TEXT, source TEXT NOT NULL DEFAULT 'created'", "")
    )
    with sqlite3.connect(runtime / "control.sqlite3") as conn:
        conn.executescript(schema)
        conn.execute("INSERT INTO admin VALUES (1,'operator','test-hash')")
        conn.execute("INSERT INTO applicants VALUES ('person','历史姓名',0,5)")
        conn.execute("PRAGMA user_version=1")
    db = Database(runtime)
    assert db.one("SELECT * FROM admin")["password_hash"] == "test-hash"
    assert db.one("SELECT * FROM admin")["contact"] == ""
    assert db.one("SELECT * FROM applicants")["name"] == "历史姓名"
    assert len(list((runtime / "backups").glob("before-v4-from-v1-*.sqlite3"))) == 1
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    Database(runtime)
    assert len(list((runtime / "backups").glob("before-v4-from-v1-*.sqlite3"))) == 1


def test_reassignment_and_explicit_adoption_allow_more_than_one_account_on_machine(app, client):
    _, target, first_account = active_account(app)
    second = person(app, "李四")
    job = apply(app, second, target, username="second_user")
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    runner.process_job(job)
    result = client.put(
        "/api/admin/accounts/" + first_account + "/assignment", json={"person_id": second["id"]}
    )
    assert result.status_code == 200
    assert app.state.service.person(second["id"])["used"] == 2
    report = app.state.service.remote.execute(target, "inspect")
    report["users"] = [{"username": "legacy_extra", "uid": 1009, "home": "/home/legacy_extra"}]
    app.state.service.save_snapshot(target["id"], report, False)
    adopted = client.post(
        "/api/admin/accounts/adopt",
        json={"machine_id": target["id"], "username": "legacy_extra", "person_id": second["id"]},
    )
    assert adopted.status_code == 202
    assert app.state.service.person(second["id"])["used"] == 3
