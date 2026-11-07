"""Administrator allocation and recovery work through the visible controls."""

import json

from conftest import login, machine, person
from fastapi.testclient import TestClient
from playwright.sync_api import expect
from test_account_recovery import recycled
from test_machine_management_ui import enter_admin
from test_worker import worker


def test_closed_machine_allocation_from_machine_menu(app, live_page):
    owner, target = person(app), machine(app, available=False)
    page = live_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    enter_admin(page)
    page.get_by_role("button", name="管理机器", exact=True).click()
    page.get_by_role("button", name="为人员分配账号", exact=True).click()
    form = page.locator("#account-form")
    expect(form).to_be_visible()
    expect(form.locator("[name=machine_id]")).to_have_value(target["id"])
    expect(form.locator("[name=person_id]")).to_have_value(owner["id"])
    form.locator("[name=username]").fill("dedicated")
    form.locator("[name=password]").fill("test-password")
    form.locator("[type=submit]").click()
    expect(form).not_to_be_visible()
    job = app.state.db.one("SELECT * FROM jobs WHERE kind='create'")
    assert json.loads(job["params"])["admin_allocation"]
    worker(app).process_job(job)
    assert app.state.db.one("SELECT state FROM accounts")["state"] == "active"
    assert app.state.db.one("SELECT available FROM machines")["available"] == 0
    assert not errors


def test_recovery_button_restores_record_and_is_admin_only(app, live_page):
    owner, account_id, runner = recycled(app)
    page = live_page
    enter_admin(page)
    page.locator("#tabs").get_by_role("button", name="回收站", exact=True).click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="恢复账号", exact=True).click()
    page.locator("#tabs").get_by_role("button", name="账号台账", exact=True).click()
    expect(page.locator("#content")).to_contain_text("账号恢复中")
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE kind='restore'"))
    page.get_by_role("button", name="更新显示", exact=True).click()
    expect(page.locator("#content")).to_contain_text("可使用")
    expect(page.get_by_role("button", name="恢复账号", exact=True)).to_have_count(0)
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        assert (
            user.post(f"/api/accounts/{account_id}/restore", json={"confirmed": True}).status_code
            == 403
        )
    assert app.state.service.person(owner["id"])["used"] == 1


def test_retired_records_only_appear_in_admin_recycle_bin(app, live_page):
    _, account_id, runner = recycled(app)
    page = live_page
    enter_admin(page)
    page.locator("#tabs").get_by_role("button", name="账号台账", exact=True).click()
    expect(page.locator("#content")).not_to_contain_text("researcher")
    page.locator("#tabs").get_by_role("button", name="回收站", exact=True).click()
    expect(page.locator("#content")).to_contain_text("researcher")
    expect(page.get_by_role("button", name="恢复账号", exact=True)).to_be_visible()
