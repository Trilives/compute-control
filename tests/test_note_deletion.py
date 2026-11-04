"""Note deletion follows stable authorship, machine visibility, and the admin role."""

from conftest import ADMIN_PASSWORD, login, machine, person
from fastapi.testclient import TestClient
from playwright.sync_api import expect


def test_owners_delete_only_their_notes_and_admin_deletes_any(app, client):
    target = machine(app)
    first, second = person(app), person(app, "李四")
    url = f"/api/machines/{target['id']}/notes"
    admin_note = client.post(url, json={"content": "管理员说明"}).json()["id"]
    second_note = app.state.service.add_machine_note(
        {"role": "applicant", "person_id": second["id"]}, target["id"], "其他人说明"
    )["id"]
    before = len(app.state.service.remote.calls)
    with TestClient(app) as user:
        login(user, "applicant", first["name"], "")
        own = user.post(url, json={"content": "自己的最新说明"}).json()["id"]
        notes = {item["id"]: item for item in user.get(url).json()}
        assert notes[own]["can_delete"] is True
        assert notes[admin_note]["can_delete"] is False
        assert notes[second_note]["can_delete"] is False
        assert all("author_key" not in item for item in notes.values())
        assert user.delete(f"{url}/{admin_note}").status_code == 403
        assert user.delete(f"{url}/{second_note}").status_code == 403
        assert user.delete(f"{url}/{own}").json() == {"deleted": True}
        assert user.delete(f"{url}/{own}").status_code == 404
        assert all(item["id"] != own for item in user.get(url).json())
    assert all(item["can_delete"] for item in client.get(url).json())
    assert client.delete(f"{url}/{second_note}").status_code == 200
    assert client.get("/api/machines").json()[0]["latest_note"]["content"] == "管理员说明"
    assert client.delete(f"{url}/{admin_note}").status_code == 200
    assert client.get("/api/machines").json()[0]["latest_note"] is None
    assert client.get(url).json() == []
    audits = app.state.db.all("SELECT * FROM audit_logs WHERE action='machine.note.delete'")
    assert len(audits) == 3 and audits[0]["actor"] == "applicant:" + first["id"]
    assert len(app.state.service.remote.calls) == before


def test_deletion_requires_csrf_and_matching_visible_machine(app, client):
    first, second = machine(app), machine(app, host="192.0.2.2")
    owner = person(app)
    url = f"/api/machines/{first['id']}/notes"
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        note_id = user.post(url, json={"content": "只在可见机器操作"}).json()["id"]
        other_url = f"/api/machines/{second['id']}/notes/{note_id}"
        assert user.delete(other_url).status_code == 404
        token = user.headers.pop("X-CSRF-Token")
        assert user.delete(f"{url}/{note_id}").status_code == 403
        user.headers["X-CSRF-Token"] = token
        with app.state.db.connect(write=True) as conn:
            conn.execute("UPDATE machines SET available=0 WHERE id=?", (first["id"],))
        assert user.delete(f"{url}/{note_id}").status_code == 404
    with TestClient(app) as guest:
        assert guest.delete(f"{url}/{note_id}").status_code == 401
    assert app.state.db.one("SELECT id FROM machine_notes WHERE id=?", (note_id,))
    assert client.delete(f"{url}/{note_id}").status_code == 200


def test_note_author_ownership_survives_name_change(app, client):
    target, owner = machine(app), person(app)
    note_id = app.state.service.add_machine_note(
        {"role": "applicant", "person_id": owner["id"]}, target["id"], "改名前写的备注"
    )["id"]
    app.state.service.save_person(
        {"name": "更名后的姓名", "enabled": True, "quota": None}, owner["id"]
    )
    with TestClient(app) as user:
        login(user, "applicant", "更名后的姓名", "")
        url = f"/api/machines/{target['id']}/notes"
        assert user.get(url).json()[0]["can_delete"] is True
        assert user.delete(f"{url}/{note_id}").status_code == 200


def test_browser_owner_and_admin_delete_controls_and_draft_preservation(app, live_page):
    target, owner = machine(app), person(app)
    service = app.state.service
    service.add_machine_note({"role": "admin"}, target["id"], "保留管理员备注")
    service.add_machine_note(
        {"role": "applicant", "person_id": owner["id"]}, target["id"], "自己的可删备注"
    )
    page = live_page
    page.locator("#login-name").fill(owner["name"])
    page.locator("#login-form button[type=submit]").click()
    expect(page.locator("#console")).to_be_visible()
    page.get_by_role("button", name="机器备注", exact=True).click()
    history = page.locator("#machine-notes-history")
    expect(history.get_by_role("button", name="删除备注", exact=True)).to_have_count(1)
    page.locator("#machine-notes-form textarea").fill("尚未提交的草稿")
    page.once("dialog", lambda dialog: dialog.dismiss())
    history.get_by_role("button", name="删除备注", exact=True).click()
    expect(history).to_contain_text("自己的可删备注")
    page.once("dialog", lambda dialog: dialog.accept())
    history.get_by_role("button", name="删除备注", exact=True).click()
    expect(history).not_to_contain_text("自己的可删备注")
    expect(history).to_contain_text("保留管理员备注")
    expect(page.locator("#machine-notes-form textarea")).to_have_value("尚未提交的草稿")
    page.locator("#machine-notes-dialog .dialog-heading button").click()
    expect(page.locator("#content")).not_to_contain_text("自己的可删备注")
    page.get_by_role("button", name="退出", exact=True).click()
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.get_by_role("button", name="机器备注", exact=True).click()
    expect(history.get_by_role("button", name="删除备注", exact=True)).to_have_count(1)
    page.once("dialog", lambda dialog: dialog.accept())
    history.get_by_role("button", name="删除备注", exact=True).click()
    expect(history).to_contain_text("暂无备注")
    assert app.state.db.all("SELECT * FROM machine_notes") == []
