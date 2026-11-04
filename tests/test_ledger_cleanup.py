import sqlite3
import time

import pytest
from conftest import apply, login, machine, person
from fastapi.testclient import TestClient
from test_worker import MemoryMailbox

from compute_control.db import Database
from compute_control.worker import Worker


def retired_account(app):
    owner, target = person(app), machine(app)
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    created = apply(app, owner, target)
    runner.process_job(created)
    account_id = created["account_id"]
    runner.process_job(app.state.service.retire_account({"role": "admin"}, account_id))
    runner.process_job(app.state.service.admin_job("cleanup", account_id))
    return owner, account_id


def test_clear_retired_ledger_is_reversible_idempotent_and_preserves_all_history(app, client):
    _, account_id = retired_account(app)
    before = {
        table: app.state.db.all("SELECT * FROM " + table)
        for table in ["accounts", "archives", "jobs"]
    }
    calls = len(app.state.service.remote.calls)
    url = "/api/accounts/" + account_id + "/ledger/"
    assert client.get("/api/accounts").json()[0]["can_clear_ledger"] is True
    assert client.post(url + "clear", json={"confirmed": False}).status_code == 400
    assert client.post(url + "clear", json={"confirmed": True}).status_code == 200
    assert client.post(url + "clear", json={"confirmed": True}).status_code == 200
    assert client.get("/api/accounts").json() == []
    rows = client.get("/api/accounts?include_cleared=true").json()
    assert len(rows) == 1 and rows[0]["ledger_cleared_at"]
    assert "credential_cipher" not in rows[0]
    assert (
        len(app.state.db.all("SELECT * FROM audit_logs WHERE action='account.ledger.clear'")) == 1
    )
    for table, original in before.items():
        assert app.state.db.all("SELECT * FROM " + table) == original
    assert len(client.get("/api/jobs").json()) == len(before["jobs"])
    assert client.get("/api/admin/archives").json()[0]["account_id"] == account_id
    assert client.post(url + "restore", json={"confirmed": True}).status_code == 200
    assert client.get("/api/accounts").json()[0]["id"] == account_id
    assert len(app.state.service.remote.calls) == calls


def test_cleanup_is_separate_for_admin_and_owner_and_forbids_other_people(app, client):
    owner, account_id = retired_account(app)
    other = person(app, "李四")
    url = "/api/accounts/" + account_id + "/ledger/"
    with TestClient(app) as user:
        login(user, "applicant", other["name"], "")
        for action in ["clear", "restore"]:
            assert user.post(url + action, json={"confirmed": True}).status_code == 404
        assert user.get("/api/accounts?include_cleared=true").json() == []
        login(user, "applicant", owner["name"], "")
        assert user.post(url + "clear", json={"confirmed": True}).status_code == 200
        assert user.get("/api/accounts").json() == []
        assert len(client.get("/api/accounts").json()) == 1
        login(user, "applicant", owner["name"], "")
        assert user.get("/api/accounts").json() == []
        assert client.post(url + "clear", json={"confirmed": True}).status_code == 200
        assert client.post(url + "restore", json={"confirmed": True}).status_code == 200
        assert user.get("/api/accounts").json() == []
        assert user.post(url + "restore", json={"confirmed": True}).status_code == 200
        assert len(user.get("/api/accounts").json()) == 1


@pytest.mark.parametrize(
    "state,occupies",
    [
        ("active", 1),
        ("creating", 1),
        ("archive_failed", 1),
        ("needs_reconcile", 1),
        ("home_cleanup_pending", 0),
        ("cancelled", 0),
        ("retired", 1),
    ],
)
def test_unfinished_or_not_retired_records_cannot_be_cleared(app, client, state, occupies):
    _, account_id = retired_account(app)
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "UPDATE accounts SET state=?,occupies=? WHERE id=?", (state, occupies, account_id)
        )
    assert client.get("/api/accounts").json()[0]["can_clear_ledger"] is False
    response = client.post(
        "/api/accounts/" + account_id + "/ledger/clear", json={"confirmed": True}
    )
    assert response.status_code == 409
    assert app.state.db.all("SELECT * FROM account_ledger_dismissals") == []


def test_pending_archive_operation_blocks_ledger_cleanup(app, client):
    _, account_id = retired_account(app)
    target = app.state.db.one("SELECT machine_id FROM accounts WHERE id=?", (account_id,))[
        "machine_id"
    ]
    now = time.time()
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO jobs(id,account_id,machine_id,kind,state,created,updated,request_key) VALUES (?,?,?,?,?,?,?,?)",
            ("busy", account_id, target, "delete_archive", "queued", now, now, "unique-busy"),
        )
    assert client.get("/api/accounts").json()[0]["can_clear_ledger"] is False
    assert (
        client.post(
            "/api/accounts/" + account_id + "/ledger/clear", json={"confirmed": True}
        ).status_code
        == 409
    )


def test_v3_migration_backups_and_retains_records_without_duplicate_history(app):
    _, account_id = retired_account(app)
    snapshots = app.state.db.all("SELECT * FROM machine_samples")
    with app.state.db.connect(write=True) as conn:
        conn.execute("DROP TABLE account_ledger_dismissals")
        conn.execute("DROP TABLE machine_notes")
        conn.execute(
            "CREATE UNIQUE INDEX person_machine ON accounts(person_id,machine_id) WHERE occupies=1"
        )
        conn.execute("PRAGMA user_version=3")
    db = Database(app.state.config.runtime_dir)
    assert db.one("SELECT * FROM accounts WHERE id=?", (account_id,))["state"] == "retired"
    assert db.all("SELECT * FROM account_ledger_dismissals") == []
    assert db.all("SELECT * FROM machine_samples") == snapshots
    assert db.one("SELECT name FROM sqlite_master WHERE name='person_machine'") is None
    owner = app.state.db.one("SELECT * FROM applicants")
    target = app.state.db.one("SELECT * FROM machines")
    assert apply(app, owner, target, username="new_user_one")["id"]
    assert apply(app, owner, target, username="new_user_two")["id"]
    backups = list((app.state.config.runtime_dir / "backups").glob("before-v4-from-v3-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name='account_ledger_dismissals'"
            ).fetchone()
            is None
        )
    Database(app.state.config.runtime_dir)
    assert (
        len(list((app.state.config.runtime_dir / "backups").glob("before-v4-from-v3-*.sqlite3")))
        == 1
    )


def test_ledger_cleanup_and_restore_browser_flow(app, live_page):
    from conftest import ADMIN_PASSWORD
    from playwright.sync_api import expect

    retired_account(app)
    page = live_page
    page.get_by_role("button", name="管理员入口", exact=True).click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.get_by_role("button", name="账号台账", exact=True).click()
    expect(page.get_by_role("button", name="清理台账", exact=True)).to_be_visible()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="清理台账", exact=True).click()
    expect(page.get_by_role("button", name="清理台账", exact=True)).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="清理台账", exact=True).click()
    expect(page.get_by_role("button", name="清理台账", exact=True)).to_have_count(0)
    page.get_by_label("显示已清理账号", exact=True).check()
    expect(page.locator("#content")).to_contain_text("已清理")
    page.get_by_role("button", name="恢复显示", exact=True).click()
    expect(page.get_by_role("button", name="清理台账", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="恢复显示", exact=True)).to_have_count(0)
