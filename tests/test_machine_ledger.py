"""Account rows are organized under their machine without mixing account ownership."""

from conftest import ADMIN_PASSWORD, apply, machine, person
from playwright.sync_api import expect
from test_worker import worker


def test_ledger_groups_accounts_by_machine_and_keeps_permissions(app, live_page):
    first, second = person(app), person(app, "李四")
    one, two = machine(app), machine(app, host="192.0.2.2")
    runner = worker(app)
    for owner, target, username in [
        (first, one, "zhang_one"),
        (second, one, "li_one"),
        (first, two, "zhang_two"),
    ]:
        runner.process_job(apply(app, owner, target, username=username))
    page = live_page
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.locator("#tabs").get_by_role("button", name="账号台账", exact=True).click()
    groups = page.locator(".account-machine-group")
    expect(groups).to_have_count(2)
    first_group = groups.filter(has=page.get_by_role("heading", name=one["name"], exact=True))
    second_group = groups.filter(has=page.get_by_role("heading", name=two["name"], exact=True))
    expect(first_group.locator("tbody tr")).to_have_count(2)
    expect(first_group).to_contain_text("zhang_one")
    expect(first_group).to_contain_text("li_one")
    expect(first_group).not_to_contain_text("zhang_two")
    expect(second_group.locator("tbody tr")).to_have_count(1)
    expect(second_group).to_contain_text("zhang_two")
    expect(page.get_by_role("switch")).to_have_count(3)
    page.get_by_role("button", name="退出", exact=True).click()
    page.locator("#login-name").fill(first["name"])
    page.locator("#login-form button[type=submit]").click()
    page.locator("#tabs").get_by_role("button", name="账号台账", exact=True).click()
    expect(groups).to_have_count(2)
    expect(page.locator("#content")).not_to_contain_text("li_one")
    expect(page.get_by_role("switch")).to_have_count(0)
