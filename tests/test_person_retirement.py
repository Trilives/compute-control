"""Removing a person requires reviewing the exact account set and queues recovery-safe retirement."""

from conftest import apply, machine, person
from test_worker import worker


def review(client, owner):
    response = client.get(f"/api/admin/applicants/{owner['id']}/deletion")
    assert response.status_code == 200
    return response.json()


def remove(client, owner, plan):
    return client.request(
        "DELETE",
        f"/api/admin/applicants/{owner['id']}",
        json={"confirmed": True, "review_token": plan["review_token"]},
    )


def test_person_deletion_reviews_and_recycles_every_account(app, client):
    owner = person(app)
    runner = worker(app)
    for i in range(2):
        runner.process_job(
            apply(app, owner, machine(app, f"192.0.2.{i + 1}"), username=f"user_{i}")
        )
    plan = review(client, owner)
    assert len(plan["accounts"]) == 2
    assert not plan["blocked"]
    assert client.delete(f"/api/admin/applicants/{owner['id']}").status_code == 400
    response = remove(client, owner, plan)
    assert response.status_code == 200
    assert response.json()["retirement_jobs"] == 2
    for job in app.state.db.all("SELECT * FROM jobs WHERE kind='retire'"):
        runner.process_job(job)
    assert all(
        a["state"] == "retired" and not a["occupies"]
        for a in app.state.db.all("SELECT * FROM accounts")
    )
    assert len(app.state.db.all("SELECT * FROM archives")) == 2


def test_stale_deletion_review_cannot_delete_new_account(app, client):
    owner = person(app)
    plan = review(client, owner)
    apply(app, owner, machine(app))
    assert remove(client, owner, plan).status_code == 409
    assert app.state.service.person(owner["id"])["enabled"]


def test_person_deletion_cancels_unstarted_creation_without_remote_changes(app, client):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    plan = review(client, owner)
    assert remove(client, owner, plan).status_code == 200
    runner = worker(app)
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE id=?", (job["id"],)))
    assert all(action != "create" for _, action in app.state.service.remote.calls)
    assert app.state.db.one("SELECT state FROM accounts")["state"] == "cancelled"


def test_deletion_dialog_lists_all_accounts_and_cancel_is_safe(app, live_page):
    from playwright.sync_api import expect
    from test_machine_management_ui import enter_admin

    owner = person(app)
    runner = worker(app)
    for i in range(2):
        runner.process_job(
            apply(app, owner, machine(app, f"192.0.2.{i + 1}"), username=f"user_{i}")
        )
    page = live_page
    enter_admin(page)
    page.locator("#tabs").get_by_role("button", name="申请者与额度", exact=True).click()
    page.get_by_role("button", name="删除姓名", exact=True).click()
    dialog = page.locator("#person-delete-dialog")
    expect(dialog.locator("tbody tr")).to_have_count(2)
    expect(dialog).to_contain_text("user_0")
    expect(dialog).to_contain_text("user_1")
    dialog.get_by_role("button", name="取消", exact=True).click()
    assert app.state.service.person(owner["id"])["enabled"]
    assert not app.state.db.all("SELECT id FROM jobs WHERE kind='retire'")
    page.get_by_role("button", name="删除姓名", exact=True).click()
    dialog.get_by_role("button", name="确认删除姓名并回收账号", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.locator("#page-title")).to_have_text("后台任务")
    assert len(app.state.db.all("SELECT id FROM jobs WHERE kind='retire'")) == 2


def test_running_mutation_blocks_removal_and_keeps_person_enabled(app, client):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE jobs SET state='running',step='remote' WHERE id=?", (job["id"],))
    plan = review(client, owner)
    assert plan["blocked"]
    assert remove(client, owner, plan).status_code == 409
    assert app.state.service.person(owner["id"])["enabled"]


def test_machine_maintenance_blocks_destructive_person_removal(app, client):
    owner, target = person(app), machine(app)
    worker(app).process_job(apply(app, owner, target))
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO machine_password_changes VALUES (?,?,?,'pending',0,0)",
            (target["id"], "a" * 32, "test-encrypted-candidate"),
        )
    plan = review(client, owner)
    assert plan["blocked"]
    assert remove(client, owner, plan).status_code == 409
    assert app.state.service.person(owner["id"])["enabled"]


def test_repeated_person_removal_does_not_duplicate_queued_retirement(app, client):
    owner, target = person(app), machine(app)
    worker(app).process_job(apply(app, owner, target))
    assert remove(client, owner, review(client, owner)).json()["retirement_jobs"] == 1
    assert remove(client, owner, review(client, owner)).json()["retirement_jobs"] == 0
    assert len(app.state.db.all("SELECT id FROM jobs WHERE kind='retire'")) == 1
