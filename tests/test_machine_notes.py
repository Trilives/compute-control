from conftest import ADMIN_PASSWORD, login, machine, person
from fastapi.testclient import TestClient
from playwright.sync_api import expect


def test_all_visible_members_can_append_attributed_notes_without_remote_changes(app, client):
    target = machine(app)
    first, second = person(app), person(app, "李四")
    before = len(app.state.service.remote.calls)
    url = "/api/machines/" + target["id"] + "/notes"
    assert client.post(url, json={"content": "管理员说明"}).status_code == 201
    for owner in [first, second]:
        with TestClient(app) as user:
            login(user, "applicant", owner["name"], "")
            assert (
                user.post(url, json={"content": owner["name"] + "补充\n环境正常"}).status_code
                == 201
            )
    notes = client.get(url).json()
    assert {note["author_name"] for note in notes} == {"管理员", "张三", "李四"}
    assert len(notes) == 3
    summary = client.get("/api/machines").json()[0]["latest_note"]
    assert summary["total"] == 3 and summary["author_name"] == "李四"
    assert len(app.state.service.remote.calls) == before
    assert len(app.state.db.all("SELECT * FROM audit_logs WHERE action='machine.note.add'")) == 3
    app.state.service.save_person(
        {"name": "李四改名", "quota": None, "enabled": True}, second["id"]
    )
    assert client.get(url).json()[0]["author_name"] == "李四"


def test_note_visibility_validation_and_author_cannot_be_forged(app, client):
    target = machine(app, available=False)
    person(app)
    url = "/api/machines/" + target["id"] + "/notes"
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        assert user.get(url).status_code == 404
        assert user.post(url, json={"content": "不能写不可见机器"}).status_code == 404
    for value, status in [("   ", 400), ("x" * 4001, 422)]:
        assert client.post(url, json={"content": value}).status_code == status
    assert client.post(url, json={"content": "伪造", "author_name": "其他人"}).status_code == 422
    with TestClient(app) as guest:
        assert guest.get(url).status_code == 401
        assert guest.post(url, json={"content": "匿名"}).status_code == 401
    assert app.state.db.all("SELECT * FROM machine_notes") == []


def test_machine_notes_button_and_user_admin_append_browser_flow(app, live_page):
    target = machine(app)
    owner = person(app)
    app.state.service.add_machine_note({"role": "admin"}, target["id"], "管理员说明：共享机器")
    page = live_page
    page.locator("#login-name").fill(owner["name"])
    page.locator("#login-form button[type=submit]").click()
    expect(page.locator("#content")).to_contain_text("管理员说明：共享机器")
    page.get_by_role("button", name="机器备注", exact=True).click()
    expect(page.locator("#machine-notes-title")).to_contain_text(target["name"])
    expect(page.locator("#machine-notes-history")).to_contain_text("管理员说明：共享机器")
    page.locator("#machine-notes-form textarea[name=content]").fill(
        "使用注意\n<img src=x onerror=alert(1)>"
    )
    page.locator("#machine-notes-form button[type=submit]").click()
    expect(page.locator("#machine-notes-dialog")).not_to_be_visible()
    expect(page.locator("#content")).to_contain_text("使用注意")
    page.get_by_role("button", name="机器备注", exact=True).click()
    expect(page.locator("#machine-notes-history")).to_contain_text(owner["name"])
    expect(page.locator("#machine-notes-history img")).to_have_count(0)
    page.locator("#machine-notes-dialog .dialog-heading").get_by_role(
        "button", name="关闭", exact=True
    ).click()
    page.get_by_role("button", name="退出", exact=True).click()
    page.get_by_role("button", name="管理员入口", exact=True).click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.get_by_role("button", name="机器备注", exact=True).click()
    expect(page.locator("#machine-notes-history")).to_contain_text("使用注意")
    page.locator("#machine-notes-form textarea[name=content]").fill("管理员后续补充")
    page.locator("#machine-notes-form button[type=submit]").click()
    expect(page.locator("#content")).to_contain_text("管理员后续补充")
    assert len(app.state.db.all("SELECT * FROM machine_notes")) == 3
