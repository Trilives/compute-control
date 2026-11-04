import copy

from conftest import ADMIN_PASSWORD, REPORT, apply, machine, person
from playwright.sync_api import expect
from test_worker import MemoryMailbox

from compute_control.worker import Worker


def test_pinned_login_contact_requests_admin_decision_and_collapsible_comparison(app, live_page):
    owner = person(app)
    targets = [machine(app), machine(app, host="192.0.2.2")]
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    task = apply(app, owner, targets[0])
    runner.process_job(task)
    for target in targets:
        report = copy.deepcopy(REPORT) | {
            "identity": target["identity"],
            "cpu_percent": 25,
            "memory": {"MemTotal": 1024, "MemAvailable": 512},
            "system_tools": [
                {
                    "name": "Git",
                    "installed": True,
                    "version": target["name"],
                    "path": "/usr/bin/git",
                }
            ],
            "users": [
                {
                    "username": "researcher",
                    "uid": 1001,
                    "home": "/home/researcher",
                    "is_admin": False,
                }
            ],
        }
        app.state.service.save_snapshot(target["id"], report, False)
    app.state.service.publish_announcement(
        {"title": "普通历史公告", "content": "不自动弹窗", "pinned": False}
    )
    pinned = app.state.service.publish_announcement(
        {"title": "停机通知", "content": "<script>只作为文本</script>", "pinned": True}
    )
    app.state.service.save_admin_contact({"name": "值班甲", "contact": "分机 123"})
    page = live_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.locator("#login-name").fill(owner["name"])
    page.locator("#login-form button[type=submit]").click()
    expect(page.locator("#pinned-dialog")).to_be_visible()
    expect(page.locator("#pinned-content")).to_contain_text("停机通知")
    expect(page.locator("#pinned-content")).not_to_contain_text("普通历史公告")
    expect(page.locator("#pinned-content script")).to_have_count(0)
    page.get_by_role("button", name="我已了解", exact=True).click()
    page.get_by_role("button", name="联系管理员", exact=True).click()
    expect(page.locator("#contact-directory")).to_contain_text("值班甲")
    expect(page.locator("#contact-admin-dialog")).to_contain_text("所有申请提交后")
    page.locator("#contact-admin-dialog").get_by_role("button", name="关闭", exact=True).click()
    expect(page.locator(".line-chart")).to_have_count(0)
    expect(page.get_by_role("button", name="机器账号与管理员权限", exact=True)).to_have_count(0)
    tool_buttons = page.get_by_role("button", name="系统开发工具 1 / 1 项可用", exact=True)
    expect(tool_buttons).to_have_count(2)
    for index, target in enumerate(targets):
        tool_buttons.nth(index).click()
        expect(page.locator("#machine-info-dialog")).to_be_visible()
        expect(page.locator("#machine-info-title")).to_have_text(target["name"] + " · 系统开发工具")
        expect(page.locator("#machine-info-content")).to_contain_text(target["name"])
        page.locator("#machine-info-dialog").get_by_role("button", name="关闭", exact=True).click()
    summaries = page.locator(".history-details > summary")
    summaries.nth(0).click()
    summaries.nth(1).click()
    expect(page.locator(".history-details[open]")).to_have_count(2)
    expect(page.locator(".line-chart")).to_have_count(4)
    page.reload()
    expect(page.locator("#pinned-dialog")).not_to_be_visible()
    expect(page.locator(".line-chart")).to_have_count(0)
    page.get_by_role("button", name="公告", exact=True).click()
    expect(page.locator("#content")).to_contain_text("普通历史公告")
    page.get_by_role("button", name="申请管理权限", exact=True).click()
    page.locator("#permission-form textarea[name=reason]").fill("需要安装编译依赖")
    page.locator("#permission-form button[type=submit]").click()
    expect(page.locator("#page-title")).to_have_text("权限申请")
    expect(page.locator("#content")).to_contain_text("需要安装编译依赖")
    page.get_by_role("button", name="退出", exact=True).click()
    page.get_by_role("button", name="管理员入口", exact=True).click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.get_by_role("button", name="权限申请", exact=True).click()
    assert (
        page.evaluate("""async () => {
        await Promise.all([load(), load()]);
        return [...document.querySelectorAll('#content button')].filter(node => node.textContent === '处理申请').length;
    }""")
        == 1
    )
    page.get_by_role("button", name="处理申请", exact=True).click()
    page.locator("#permission-decision-form select[name=action]").select_option("help_install")
    page.locator("#permission-decision-form textarea[name=response]").fill("由管理员安装")
    page.locator("#permission-decision-form button[type=submit]").click()
    expect(page.locator("#content")).to_contain_text("由管理员安装")
    page.get_by_role("button", name="账号台账", exact=True).click()
    expect(page.get_by_role("switch", name="researcher 管理员权限", exact=True)).to_have_count(1)
    page.get_by_role("button", name="当前状态", exact=True).click()
    account_buttons = page.get_by_role("button", name="机器账号与管理员权限", exact=True)
    expect(account_buttons).to_have_count(2)
    account_buttons.nth(0).click()
    expect(page.locator("#machine-info-title")).to_have_text(
        targets[0]["name"] + " · 机器账号与管理员权限"
    )
    expect(page.locator("#machine-info-content")).to_contain_text("researcher")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("switch", name="researcher 管理员权限", exact=True).check()
    expect(page.locator("#page-title")).to_have_text("后台任务")
    expect(page.locator("#machine-info-dialog")).not_to_be_visible()
    queued = app.state.db.one("SELECT * FROM jobs WHERE kind='set_privilege'")
    assert queued is not None
    runner.process_job(queued)
    page.get_by_role("button", name="账号台账", exact=True).click()
    expect(page.get_by_role("switch", name="researcher 管理员权限", exact=True)).to_be_checked()
    page.get_by_role("button", name="公告", exact=True).click()
    page.get_by_role("button", name="发布公告", exact=True).click()
    page.locator("#announcement-form input[name=title]").fill("新公告")
    page.locator("#announcement-form textarea[name=content]").fill("公告正文")
    page.locator("#announcement-form button[type=submit]").click()
    expect(page.locator("#content")).to_contain_text("新公告")
    assert app.state.service.announcements()[0]["pinned"] == 1
    app.state.service.change_announcement(pinned["id"], delete=True)
    page.get_by_role("button", name="管理员名单", exact=True).click()
    page.get_by_role("button", name="添加管理员联系人", exact=True).click()
    page.locator("#contact-form input[name=name]").fill("值班乙")
    page.locator("#contact-form textarea[name=contact]").fill("IM 联系")
    page.locator("#contact-form button[type=submit]").click()
    expect(page.locator("#content")).to_contain_text("值班乙")
    assert errors == []
