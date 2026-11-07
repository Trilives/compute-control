"""Archive rows select the exact archive and require a password for legacy records."""

import json
import secrets

import pytest
from test_account_recovery import recycled

TEST_RECOVERY_PASSWORD = secrets.token_urlsafe(18)


def test_archive_restore_requests_password_when_not_saved(app, client):
    _, account_id, _ = recycled(app)
    archive = app.state.db.one("SELECT * FROM archives")
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE accounts SET credential_cipher=NULL WHERE id=?", (account_id,))
    rows = client.get("/api/admin/archives").json()
    assert rows[0]["password_available"] is False
    response = client.post(f"/api/admin/archives/{archive['id']}/restore", json={"confirmed": True})
    assert response.status_code == 400
    assert (
        app.state.db.one("SELECT occupies FROM accounts WHERE id=?", (account_id,))["occupies"] == 0
    )
    response = client.post(
        f"/api/admin/archives/{archive['id']}/restore",
        json={"confirmed": True, "password": "new-legacy-password"},
    )
    assert response.status_code == 202
    assert "new-legacy-password" not in response.text and "params" not in response.json()
    job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (response.json()["id"],))
    assert "new-legacy-password" not in job["params"]
    params = json.loads(job["params"])
    assert params["archive_id"] == archive["id"] and params["archive_path"] == archive["path"]
    assert (
        app.state.service.secrets.decrypt(params["restore_credential_cipher"])["password"]
        == "new-legacy-password"
    )


def test_archive_restore_uses_saved_password_and_persists_after_success(app, client):
    _, account_id, runner = recycled(app)
    archive = app.state.db.one("SELECT * FROM archives")
    response = client.post(f"/api/admin/archives/{archive['id']}/restore", json={"confirmed": True})
    assert response.status_code == 202
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE id=?", (response.json()["id"],)))
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    assert account["state"] == "active"
    assert app.state.service.secrets.decrypt(account["credential_cipher"])["password"]


def test_deleted_archive_cannot_restore_and_member_cannot_request(app, client):
    _, account_id, _ = recycled(app)
    archive = app.state.db.one("SELECT * FROM archives")
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE archives SET deleted=1 WHERE id=?", (archive["id"],))
    assert (
        client.post(
            f"/api/admin/archives/{archive['id']}/restore", json={"confirmed": True}
        ).status_code
        == 404
    )
    assert (
        app.state.db.one("SELECT occupies FROM accounts WHERE id=?", (account_id,))["occupies"] == 0
    )


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    from test_account_recovery import sandbox as recovery_sandbox

    return recovery_sandbox.__wrapped__(tmp_path, monkeypatch)


def test_legacy_remote_recovery_can_take_new_password_without_saved_hash(sandbox, monkeypatch):
    import os
    from pathlib import Path
    from types import SimpleNamespace

    from compute_control import remote

    result = remote.retire(sandbox.payload)
    remote.cleanup(sandbox.payload)
    receipt = json.loads(sandbox.receipt.read_text())
    receipt.pop("restore")
    receipt["archive"] = str(sandbox.root / "missing-newer-archive.tar")
    sandbox.receipt.write_text(json.dumps(receipt))
    metadata_path = Path(result["archive"]).parent / "retirement-metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata.pop("restore")
    metadata_path.write_text(json.dumps(metadata))
    previous = remote.run
    calls = []

    def run(args, input=None, check=True):
        calls.append((args, input))
        if args[0] == "useradd":
            sandbox.present["account"] = SimpleNamespace(
                pw_uid=os.getuid(), pw_dir=str(sandbox.home)
            )
            return ""
        return previous(args, input, check)

    monkeypatch.setattr(remote, "run", run)
    monkeypatch.setattr(remote, "sudo_status", lambda _: {"is_admin": False})
    payload = dict(
        sandbox.payload,
        archive_path=result["archive"],
        archive_checksum=result["checksum"],
        archive_size=result["size"],
        password=TEST_RECOVERY_PASSWORD,
    )
    restored = remote.restore(payload)
    assert restored["state"] == "active"
    assert any(args == ["chpasswd"] and TEST_RECOVERY_PASSWORD in stdin for args, stdin in calls)
    assert TEST_RECOVERY_PASSWORD not in json.dumps(restored) + sandbox.receipt.read_text()
    assert all(TEST_RECOVERY_PASSWORD not in " ".join(args) for args, _ in calls)
    assert sandbox.home.joinpath("work.txt").read_text() == "valuable research\n"


def test_selected_archive_must_match_its_recorded_digest_before_restoring(sandbox):
    from compute_control import remote

    result = remote.retire(sandbox.payload)
    remote.cleanup(sandbox.payload)
    with pytest.raises(remote.Refuse, match="摘要"):
        remote.restore(
            dict(
                sandbox.payload,
                archive_path=result["archive"],
                archive_checksum="0" * 64,
                archive_size=result["size"],
                password=TEST_RECOVERY_PASSWORD,
            )
        )
    assert not sandbox.home.exists() and sandbox.present["account"] is None


def test_archive_row_prompts_for_legacy_password_and_has_no_separate_recycle_tab(app, live_page):
    from playwright.sync_api import expect
    from test_machine_management_ui import enter_admin

    _, account_id, runner = recycled(app)
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE accounts SET credential_cipher=NULL WHERE id=?", (account_id,))
    page = live_page
    enter_admin(page)
    expect(page.locator("#tabs").get_by_role("button", name="回收站", exact=True)).to_have_count(0)
    page.locator("#tabs").get_by_role("button", name="归档记录", exact=True).click()
    page.get_by_role("button", name="恢复账号", exact=True).click()
    form = page.locator("#archive-restore-form")
    expect(form).to_be_visible()
    assert form.locator("[name=password]").evaluate("node=>node.required")
    form.locator("[name=password]").fill(TEST_RECOVERY_PASSWORD)
    with page.expect_response(
        lambda r: r.url.endswith("/restore") and r.request.method == "POST"
    ) as submitted:
        form.locator("[type=submit]").click()
    assert submitted.value.status == 202
    expect(form.locator("[name=password]")).to_have_value("")
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE kind='restore'"))
    assert app.state.db.one("SELECT state FROM accounts")["state"] == "active"


def test_reconciliation_keeps_new_recovery_password_after_lost_response(app, client):
    from compute_control.security import DomainError

    _, account_id, runner = recycled(app)
    archive = app.state.db.one("SELECT * FROM archives")
    response = client.post(
        f"/api/admin/archives/{archive['id']}/restore",
        json={"confirmed": True, "password": TEST_RECOVERY_PASSWORD},
    )
    job_id = response.json()["id"]
    seen = []

    def execute(machine, action, data=None):
        if action == "restore":
            seen.append(data)
            raise DomainError("连接中断，需核查", 502)
        assert action == "reconcile"
        return {
            "state": "active",
            "uid": 1001,
            "home": "/home/researcher",
            "last_restore_job": job_id,
        }

    app.state.service.remote.execute = execute
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE id=?", (job_id,)))
    assert seen[0]["password"] == TEST_RECOVERY_PASSWORD
    assert "restore_credential_cipher" not in seen[0]
    runner.process_job(app.state.service.admin_job("reconcile", account_id))
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    assert (
        app.state.service.secrets.decrypt(account["credential_cipher"])["password"]
        == TEST_RECOVERY_PASSWORD
    )
    assert app.state.db.one("SELECT state FROM jobs WHERE id=?", (job_id,))["state"] == "succeeded"


def test_member_cannot_restore_an_archive(app, client):
    from conftest import login
    from fastapi.testclient import TestClient

    owner, _, _ = recycled(app)
    archive = app.state.db.one("SELECT * FROM archives")
    with TestClient(app) as member:
        login(member, "applicant", owner["name"], "")
        assert (
            member.post(
                f"/api/admin/archives/{archive['id']}/restore",
                json={"confirmed": True, "password": TEST_RECOVERY_PASSWORD},
            ).status_code
            == 403
        )
