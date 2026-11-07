"""Suspension is reversible and never archives, deletes data, or releases quota."""

import json

import pytest
from conftest import apply, login, machine, person
from fastapi.testclient import TestClient
from test_account_recovery import sandbox as archive_sandbox
from test_worker import worker

from compute_control import remote


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    return archive_sandbox.__wrapped__(tmp_path, monkeypatch)


def test_remote_suspend_and_resume_preserve_account_directory_and_password(sandbox, monkeypatch):
    monkeypatch.setattr(remote, "sudo_status", lambda _: {"is_admin": False})
    assert remote.suspend(sandbox.payload)["state"] == "suspended"
    assert sandbox.present["account"] is not None and sandbox.home.is_dir()
    assert not (sandbox.root / "archives").exists()
    assert not any(args[0] in ["userdel", "tar"] for args in sandbox.commands)
    assert json.loads(sandbox.receipt.read_text())["state"] == "suspended"
    assert remote.reconcile(sandbox.payload)["state"] == "suspended"
    assert remote.resume(sandbox.payload)["state"] == "active"
    assert sandbox.home.joinpath("work.txt").read_text() == "valuable research\n"


def test_suspension_busy_account_is_untouched(sandbox, monkeypatch):
    monkeypatch.setattr(remote, "active_processes", lambda *_: [123])
    with pytest.raises(remote.Refuse, match="仍被进程"):
        remote.suspend(sandbox.payload)
    assert sandbox.commands == []


def test_admin_suspend_resume_keeps_quota_and_member_cannot_control_it(app, client):
    owner, target = person(app), machine(app)
    created = apply(app, owner, target)
    runner = worker(app)
    runner.process_job(created)
    account_id = created["account_id"]
    original = app.state.service.remote.execute

    def execute(machine, action, data=None):
        if action == "suspend":
            return {"state": "suspended", "uid": 1001, "home": "/home/researcher"}
        if action == "resume":
            return {"state": "active", "uid": 1001, "home": "/home/researcher"}
        return original(machine, action, data)

    app.state.service.remote.execute = execute
    response = client.post(f"/api/accounts/{account_id}/suspend", json={"confirmed": True})
    assert response.status_code == 202
    runner.process_job(response.json())
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    assert account["state"] == "suspended" and account["occupies"] == 1
    assert app.state.service.person(owner["id"])["used"] == 1
    assert app.state.db.all("SELECT * FROM archives") == []
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        for action in ["suspend", "resume"]:
            assert (
                user.post(
                    f"/api/accounts/{account_id}/{action}", json={"confirmed": True}
                ).status_code
                == 403
            )
    response = client.post(f"/api/accounts/{account_id}/resume", json={"confirmed": True})
    assert response.status_code == 202
    runner.process_job(response.json())
    assert (
        app.state.db.one("SELECT state FROM accounts WHERE id=?", (account_id,))["state"]
        == "active"
    )


def test_browser_exposes_pause_and_resume_as_distinct_controls(app, live_page):
    from playwright.sync_api import expect
    from test_machine_management_ui import enter_admin

    owner, target = person(app), machine(app)
    runner = worker(app)
    runner.process_job(apply(app, owner, target))
    original = app.state.service.remote.execute

    def execute(machine, action, data=None):
        if action == "suspend":
            return {"state": "suspended", "uid": 1001, "home": "/home/researcher"}
        if action == "resume":
            return {"state": "active", "uid": 1001, "home": "/home/researcher"}
        return original(machine, action, data)

    app.state.service.remote.execute = execute
    page = live_page
    enter_admin(page)
    page.locator("#tabs").get_by_role("button", name="账号台账", exact=True).click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="暂停使用", exact=True).click()
    expect(page.locator("#notice")).to_contain_text("已提交后台任务")
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE kind='suspend'"))
    page.get_by_role("button", name="更新显示", exact=True).click()
    expect(page.get_by_role("button", name="启用账号", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="回收", exact=True)).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.url.endswith("/resume") and response.request.method == "POST"
    ) as submitted:
        page.get_by_role("button", name="启用账号", exact=True).click()
    assert submitted.value.status == 202
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE kind='resume'"))
    page.get_by_role("button", name="更新显示", exact=True).click()
    expect(page.get_by_role("button", name="暂停使用", exact=True)).to_be_visible()
