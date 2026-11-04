"""Review role navigation, operation placement, modal controls and responsive layout."""

import copy
from pathlib import Path

import pytest
from conftest import ADMIN_PASSWORD, REPORT, apply, machine, person
from playwright.sync_api import expect
from test_worker import worker


@pytest.mark.parametrize("role", ["admin", "applicant"])
def test_navigation_buttons_and_responsive_dialogs(app, live_page, role):
    owner = person(app)
    person(app, "接收人")
    for index in range(3):
        target = machine(app, f"192.0.2.{index + 1}")
        report = copy.deepcopy(REPORT)
        report.update(
            identity=target["identity"],
            cpu_percent=25,
            memory={"MemTotal": 4096, "MemAvailable": 2048},
            system_tools=[
                {"name": "Git", "installed": True, "version": "test", "path": "/usr/bin/git"}
            ],
        )
        app.state.service.save_snapshot(target["id"], report, False)
        if index == 0:
            worker(app).process_job(apply(app, owner, target))
    page = live_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    if role == "admin":
        page.locator("#admin-entry").click()
        page.locator("#login-name").fill("admin")
        page.locator("#login-password").fill(ADMIN_PASSWORD)
    else:
        page.locator("#login-name").fill("张三")
    page.locator("#login-form button[type=submit]").click()
    expect(page.locator(".machine-grid > article")).to_have_count(3)
    tabs = ["当前状态", "账号台账", "sudo 权限申请", "后台任务"]
    if role == "admin":
        tabs += ["申请者与额度"]
    tabs += ["公告"]
    if role == "admin":
        tabs += ["管理员名单", "归档记录", "操作记录"]
    expect(page.locator("#tabs button")).to_have_text(tabs)
    for title in tabs:
        page.locator("#tabs").get_by_role("button", name=title, exact=True).click()
        expect(page.locator("#page-title")).to_have_text(title)
        page.locator("#page-actions").get_by_role("button", name="更新显示").click()
        expect(page.locator("#page-title")).to_have_text(title)
        if title == "申请者与额度":
            expect(page.get_by_role("textbox", name="补充联系说明")).to_have_count(0)
        if title == "管理员名单":
            expect(page.get_by_role("textbox", name="补充联系说明")).to_be_visible()
    page.locator("#tabs").get_by_role("button", name="当前状态", exact=True).click()
    expect(page.locator(".machine-grid > article")).to_have_count(3)
    if role == "admin":
        expect(page.locator(".machine-management").first.locator("button")).to_have_text(
            ["关闭申请", "更新凭据", "修改机器管理密码", "重启机器"]
        )
        page.get_by_role("button", name="更新凭据", exact=True).first.click()
        expect(page.locator("#machine-dialog .dialog-heading")).to_contain_text("更新机器与凭据")
        expect(page.locator("#machine-form button[type=submit]")).to_have_text("检查并保存")
        page.locator("#machine-dialog .dialog-heading button").click()
    else:
        assert page.locator("#system-dependencies").evaluate(
            "node => node.nextElementSibling.id === 'contact-admin'"
        )
    preview = Path("runtime/previews")
    preview.mkdir(parents=True, exist_ok=True)
    for width in [1440, 1024, 768, 390]:
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator(".machine-grid button").evaluate_all("""nodes => nodes.every(node => {
            const r = node.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth;
        })""")
        page.screenshot(path=str(preview / f"review-{role}-{width}.png"), full_page=True)
        page.get_by_role("button", name="机器备注", exact=True).first.click()
        expect(page.locator("#machine-notes-dialog")).to_be_visible()
        page.locator("#machine-notes-form textarea").fill("未提交的备注草稿")
        page.locator("#machine-notes-dialog .dialog-heading button").click()
        page.locator("#operation-guide").click()
        expect(page.locator("#guide-content")).not_to_be_empty()
        assert page.locator("#guide-dialog .dialog-body").evaluate("node => node.scrollTop === 0")
        page.locator("#guide-dialog .dialog-body").evaluate(
            "node => node.scrollTop = node.scrollHeight"
        )
        expect(page.locator("#guide-dialog .dialog-heading button")).to_be_in_viewport()
        page.locator("#guide-dialog .dialog-heading button").click()
    assert app.state.db.all("SELECT * FROM machine_notes") == []
    assert errors == []
