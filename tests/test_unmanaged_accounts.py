"""Administrative machine inventory, privilege filtering and review navigation."""

from conftest import apply, login, machine, person
from fastapi.testclient import TestClient
from playwright.sync_api import expect
from test_machine_management_ui import enter_admin
from test_worker import worker


def observed(name, uid, *, is_admin=False, home=None):
    return {"username": name, "uid": uid, "home": home or f"/home/{name}", "is_admin": is_admin}


def save_inventory(app, target, users, system_accounts=None, existing=()):
    report = app.state.service.remote.execute(target, "inspect")
    report.update(users=users, existing_usernames=list(existing))
    if system_accounts is not None:
        report["system_accounts"] = system_accounts
    app.state.service.save_snapshot(target["id"], report, False)


def test_unmanaged_inventory_checks_machine_identity_and_privileged_accounts(app, client):
    owner = person(app)
    first, second = machine(app), machine(app, "192.0.2.2", available=False)
    runner = worker(app)
    runner.process_job(apply(app, owner, first, username="tracked"))
    runner.process_job(apply(app, owner, first, username="replaced"))
    save_inventory(
        app,
        first,
        [
            observed("tracked", 1001),
            observed("replaced", 1012),
            observed("operator", 1000),
            observed("privileged", 1013, is_admin=True),
            observed("root_alias", 0),
            observed("legacy", 1005, is_admin=None),
        ],
        [observed("daemon", 21, home="/var/lib/daemon"), observed("sysadmin", 60, is_admin=True)],
    )
    save_inventory(app, second, [observed("tracked", 1001)], existing=["legacy_service"])
    inventory = {row["id"]: row for row in client.get("/api/admin/unmanaged-accounts").json()}
    observed_first = {row["username"]: row for row in inventory[first["id"]]["accounts"]}
    assert set(observed_first) == {"replaced", "legacy"}
    assert (
        observed_first["replaced"]["same_name_record"]
        and not observed_first["replaced"]["can_adopt"]
    )
    assert not observed_first["legacy"]["can_adopt"] and not observed_first["legacy"]["sudo_known"]
    assert {item["username"] for item in inventory[second["id"]]["accounts"]} == {"tracked"}
    assert all(machine["complete_sample"] for machine in inventory.values())

    with TestClient(app) as applicant:
        login(applicant, "applicant", owner["name"], "")
        assert applicant.get("/api/admin/unmanaged-accounts").status_code == 403
        assert "system_accounts" not in applicant.get("/api/machines").json()[0]["snapshot"]


def test_admin_can_filter_inventory_and_open_existing_adoption_form(app, live_page):
    person(app)
    first, second = machine(app), machine(app, "192.0.2.2")
    save_inventory(app, first, [observed("alpha", 1001)], [observed("daemon", 21)])
    save_inventory(app, second, [observed("beta", 1002)], [observed("nobody", 65534)])
    page = live_page
    enter_admin(page)
    page.get_by_role("button", name="审阅", exact=True).click()
    view = page.locator("section").filter(
        has=page.get_by_role("heading", name="未接管账号", exact=True)
    )
    expect(view.get_by_role("row").filter(has_text="alpha")).to_be_visible()
    expect(view.get_by_role("row").filter(has_text="beta")).to_be_visible()
    expect(page.locator('#tabs [aria-current="page"]')).to_have_css("font-weight", "400")
    review_labels = view.get_by_role("columnheader").all_text_contents()
    review_cells = view.get_by_role("row").filter(has_text="beta").get_by_role("cell")
    review_values = review_cells.all_text_contents()
    page.get_by_role("button", name="当前状态", exact=True).click()
    card = page.locator(".machine-grid article").filter(
        has=page.get_by_role("heading", name=second["name"], exact=True)
    )
    card.get_by_role("button", name="管理机器", exact=True).click()
    page.get_by_role("button", name="机器账号与 sudo 权限", exact=True).click()
    dialog = page.locator("#machine-info-dialog")
    expect(dialog.get_by_role("columnheader")).to_have_text(review_labels[1:])
    expect(dialog.get_by_role("row").filter(has_text="beta").get_by_role("cell")).to_have_text(
        review_values[1:]
    )
    expect(dialog.get_by_role("row").filter(has_text="nobody")).to_have_count(0)
    dialog.get_by_role("button", name="关闭", exact=True).click()
    page.get_by_role("button", name="审阅", exact=True).click()
    view.get_by_role("combobox", name="按机器筛选未接管账号").select_option(second["id"])
    expect(view.get_by_role("row").filter(has_text="alpha")).to_have_count(0)
    view.get_by_role("searchbox", name="搜索未接管账号").fill("1002")
    view.get_by_role("row").filter(has_text="beta").get_by_role("button", name="接管").click()
    form = page.locator("#adopt-form")
    expect(form.locator("[name=machine_id]")).to_have_value(second["id"])
    expect(form.locator("[name=username]")).to_have_value("beta")
    expect(form.locator('button[type="submit"]')).to_have_css("font-weight", "400")
    assert not app.state.db.all("SELECT id FROM jobs WHERE kind='adopt'")
