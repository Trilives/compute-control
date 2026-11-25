"""The administrator reviews live blockers and explicitly confirms forced recycling."""

import json

from conftest import apply, login, machine, person
from fastapi.testclient import TestClient
from playwright.sync_api import expect
from test_machine_management_ui import enter_admin
from test_worker import worker

from compute_control.security import DomainError


def test_blocked_retirement_joins_sudo_reviews_and_force_needs_confirmation(
    app, client, live_page, monkeypatch
):
    owner, target = person(app), machine(app)
    created = apply(app, owner, target)
    runner = worker(app)
    runner.process_job(created)
    account_id = created["account_id"]
    remote = app.state.service.remote
    execute = remote.execute
    review = {
        "boot_id": "cb0589b3-a152-45c5-b31f-d5723b9b3a80",
        "processes": [
            {"pid": 4321, "name": "trainer", "uid": 1001, "start_time": 5678},
            {"pid": 4322, "name": "external", "uid": 1002, "start_time": 5679},
        ],
    }
    report = {
        "processes": [
            {"pid": 4321, "name": "trainer", "uid": 1001},
            {"pid": 4322, "name": "external", "uid": 1002},
        ],
        "schedule_blocker": "存在 cron 调度",
        "target_uid": 1001,
    }

    def checked(machine, action, data=None):
        if action == "retirement_activity":
            assert machine["id"] == target["id"]
            assert data == {"account_id": account_id, "username": "researcher"}
            return report
        if action == "retire" and not data.get("process_review"):
            raise DomainError("账号或主目录仍被进程使用，请先结束工作", 502)
        return execute(machine, action, data)

    monkeypatch.setattr(remote, "execute", checked)
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        assert (
            user.post(
                "/api/permission-requests", json={"account_id": account_id, "reason": "编译依赖"}
            ).status_code
            == 201
        )
        path = f"/api/accounts/{account_id}/retire"
        original = user.post(path, json={"confirmed": True}).json()
        assert user.get("/api/admin/retirement-reviews").status_code == 403
        assert (
            user.post(path, json={"confirmed": True, "process_review": review}).status_code == 403
        )

    page = live_page
    enter_admin(page)
    tab = page.locator("#tabs").get_by_role("button", name="审阅", exact=True)
    tab.click()
    expect(page.get_by_role("heading", name="受阻回收")).to_be_visible()
    expect(page.get_by_role("button", name="处理申请", exact=True)).to_be_visible()
    expect(page.locator("#content")).to_contain_text("编译依赖")
    expect(page.locator("#content")).to_contain_text("暂无待审阅的回收任务")
    runner.process_job(original)
    expect(page.locator("#content")).to_contain_text("账号或主目录仍被进程使用")
    stale = {"confirmed": True, "review_job_id": "f" * 32, "process_review": review}
    assert client.post(path, json=stale).status_code == 409
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        assert user.post(path, json={"confirmed": True}).status_code == 409
    open_review = page.get_by_role("button", name="审阅回收", exact=True)
    open_review.click()
    dialog = page.get_by_role("dialog", name="审阅回收")
    force = dialog.get_by_role("button", name="强制回收", exact=True)
    expect(dialog.get_by_role("row", name="4321 trainer 1001")).to_be_visible()
    expect(dialog).to_contain_text("存在 cron 调度")
    expect(dialog).to_contain_text("其他账号的 1 个进程")
    expect(force).to_have_count(0)
    report.update(schedule_blocker=None, process_review=review)
    dialog.get_by_role("button", name="重新检查活动").click()
    expect(force).to_be_visible()

    prompts = []
    page.once("dialog", lambda prompt: (prompts.append(prompt.message), prompt.dismiss()))
    force.click()
    expect(dialog).to_be_visible()
    assert "确认强制回收" in prompts[0] and "1 个进程属于其他账号" in prompts[0]
    assert "external（PID 4322，UID 1002）" in prompts[0]
    assert len(app.state.db.all("SELECT id FROM jobs WHERE kind='retire'")) == 1

    page.once("dialog", lambda prompt: prompt.accept())
    force.click()
    expect(dialog).not_to_be_visible()
    expect(page.locator("#page-title")).to_have_text("后台任务")
    requested = app.state.db.one("SELECT * FROM jobs WHERE kind='retire' ORDER BY created DESC")
    assert requested["id"] != original["id"] and requested["account_id"] == account_id
    assert json.loads(requested["params"]) == {"process_review": review}
    runner.process_job(requested)
    tab.click()
    expect(page.locator("#content")).to_contain_text("暂无待审阅的回收任务")
    expect(page.get_by_role("button", name="处理申请", exact=True)).to_be_visible()
