"""Machine-only admin actions stay behind one menu and retain their original targets."""

import time

from conftest import ADMIN_PASSWORD, machine, person
from playwright.sync_api import expect


def enter_admin(page):
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()


def test_machine_menu_groups_operations_and_preserves_safe_interactions(app, live_page):
    owner = person(app)
    first, second = machine(app), machine(app, "192.0.2.2")
    page = live_page
    enter_admin(page)
    expect(page.locator(".machine-management").first.locator("button")).to_have_text(["管理机器"])
    writes = []
    page.on(
        "request", lambda request: writes.append(request.url) if request.method != "GET" else None
    )
    menu = page.locator("#machine-management-dialog")
    for width in [1440, 390]:
        page.set_viewport_size({"width": width, "height": 900})
        page.get_by_role("button", name="管理机器", exact=True).first.click()
        expect(menu).to_be_visible()
        expect(menu.locator("h2")).to_have_text(first["name"] + " · 管理机器")
        for name in [
            "关闭申请",
            "更新凭据",
            "修改机器管理密码",
            "机器账号与 sudo 权限",
            "重启机器",
        ]:
            expect(menu.get_by_role("button", name=name, exact=True)).to_be_visible()
        assert menu.evaluate("node=>node.scrollWidth <= node.clientWidth")
        menu.locator(".dialog-heading button").click()
        expect(menu).not_to_be_visible()
    assert writes == []
    page.get_by_role("button", name="管理机器", exact=True).nth(1).click()
    menu.get_by_role("button", name="更新凭据", exact=True).click()
    expect(menu).not_to_be_visible()
    expect(page.locator("#machine-form [name=host]")).to_have_value(second["host"])
    expect(page.locator("#machine-form [name=ssh_password]")).to_have_value("")
    page.locator("#machine-dialog .dialog-heading button").click()
    page.get_by_role("button", name="管理机器", exact=True).nth(1).click()
    menu.get_by_role("button", name="修改机器管理密码", exact=True).click()
    expect(menu).not_to_be_visible()
    expect(page.locator("#management-password-target")).to_contain_text(second["host"])
    page.locator("#management-password-dialog .dialog-heading button").click()
    page.get_by_role("button", name="管理机器", exact=True).first.click()
    menu.get_by_role("button", name="关闭申请", exact=True).click()
    expect(page.locator(".machine-grid > article").first).to_contain_text("暂不开放")
    assert (
        app.state.db.one("SELECT available FROM machines WHERE id=?", (first["id"],))["available"]
        == 0
    )
    assert (
        app.state.db.one("SELECT available FROM machines WHERE id=?", (second["id"],))["available"]
        == 1
    )
    page.get_by_role("button", name="管理机器", exact=True).first.click()
    page.once("dialog", lambda dialog: dialog.dismiss())
    menu.get_by_role("button", name="重启机器", exact=True).click()
    assert not app.state.db.all("SELECT id FROM jobs WHERE kind='reboot'")
    page.get_by_role("button", name="退出", exact=True).click()
    page.locator("#login-name").fill(owner["name"])
    page.locator("#login-form button[type=submit]").click()
    expect(page.get_by_role("button", name="管理机器", exact=True)).to_have_count(0)
    expect(menu.locator(".admin-action")).to_have_count(0)


def test_pending_password_check_remains_available_inside_machine_menu(app, live_page):
    target = machine(app)
    candidate = app.state.service.secrets.encrypt(
        {"ssh_password": "test-candidate", "sudo_password": ""}
    )
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO machine_password_changes VALUES (?,?,?,'pending',?,?)",
            (target["id"], "a" * 32, candidate, time.time(), time.time()),
        )
    page = live_page
    enter_admin(page)
    page.get_by_role("button", name="管理机器", exact=True).click()
    menu = page.locator("#machine-management-dialog")
    expect(menu.get_by_role("button", name="核查管理密码变更", exact=True)).to_be_visible()
    expect(menu.get_by_role("button", name="修改机器管理密码", exact=True)).to_have_count(0)
    menu.get_by_role("button", name="核查管理密码变更", exact=True).click()
    expect(menu).not_to_be_visible()
    expect(page.locator("#notice")).to_contain_text("已同步")
    page.get_by_role("button", name="管理机器", exact=True).click()
    expect(menu.get_by_role("button", name="修改机器管理密码", exact=True)).to_be_visible()
