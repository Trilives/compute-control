import time

from conftest import ADMIN_PASSWORD, login, person
from fastapi.testclient import TestClient

from compute_control.security import digest


def test_welcome_and_empty_start(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "管理员入口" in page.text and "申请者模式" in page.text
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert client.get("/api/machines").json() == []
    assert client.get("/api/accounts").json() == []
    assert client.get("/api/admin/settings").json()["default_quota"] == 3


def test_no_public_admin_bootstrap(app):
    with TestClient(app) as guest:
        assert guest.get("/api/admin/applicants").status_code == 401
        assert guest.post("/api/auth/init-admin", json={}).status_code == 404
        me = guest.get("/api/auth/me").json()
        assert me["role"] == "anonymous"
        assert guest.get("/api/admin/applicants").status_code == 401
        assert app.state.db.all("SELECT * FROM viewer_leases") == []


def test_csrf_and_session_rotation(client, app):
    previous = client.cookies.get("cc_session")
    old_csrf = client.headers["X-CSRF-Token"]
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "forged"}).status_code == 403
    result = client.post("/api/auth/logout")
    assert result.status_code == 200
    assert client.cookies.get("cc_session") != previous
    assert app.state.auth.session(previous) is None
    client.headers["X-CSRF-Token"] = old_csrf
    assert (
        client.post(
            "/api/auth/login",
            json={"role": "admin", "username": "admin", "password": ADMIN_PASSWORD},
        ).status_code
        == 403
    )


def test_applicant_has_only_name_login_and_no_admin_rights(app):
    person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        assert user.get("/api/auth/me").json()["person"]["effective_quota"] == 3
        assert user.get("/api/admin/applicants").status_code == 403
        assert user.put("/api/admin/settings/quota", json={"value": 20}).status_code == 403
        assert user.get("/api/machines").json() == []


def test_disable_revokes_existing_session(app, client):
    owner = person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        response = client.put(
            "/api/admin/applicants/" + owner["id"],
            json={
                "name": "张三",
                "enabled": False,
                "quota": None,
            },
        )
        assert response.status_code == 200
        assert user.get("/api/accounts").status_code == 401
        assert user.get("/api/auth/me").json()["role"] == "anonymous"


def test_rename_keeps_identity_and_personal_quota(app, client):
    owner = person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        client.put("/api/admin/applicants/" + owner["id"], json={"name": "张三-研发", "quota": 5})
        me = user.get("/api/auth/me").json()
        assert me["person_id"] == owner["id"]
        assert me["name"] == "张三-研发" and me["person"]["effective_quota"] == 5
    assert client.post("/api/admin/applicants", json={"name": "张三-研发"}).status_code == 409


def test_password_not_in_errors_or_database(client, app):
    marker = "private-validation-secret"
    response = client.post(
        "/api/auth/login", json={"role": "not-a-role", "username": "admin", "password": marker}
    )
    assert response.status_code == 422 and marker not in response.text
    assert ADMIN_PASSWORD.encode() not in app.state.db.path.read_bytes()
    assert marker.encode() not in app.state.db.path.read_bytes()


def test_expired_session_rejected(client, app):
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "UPDATE sessions SET expires=? WHERE token_hash=?",
            (time.time() - 1, digest(client.cookies.get("cc_session"))),
        )
    assert client.get("/api/accounts").status_code == 401


def test_failed_login_rate_limited_with_short_window_and_separate_roles(app):
    with TestClient(app) as user:
        csrf = user.get("/api/auth/me").json()["csrf"]
        user.headers["X-CSRF-Token"] = csrf
        for _ in range(20):
            assert (
                user.post(
                    "/api/auth/login", json={"role": "applicant", "username": "unknown"}
                ).status_code
                == 401
            )
        with app.state.db.connect(write=True) as conn:
            conn.executemany(
                "INSERT INTO login_attempts VALUES (?,?)",
                [("applicant:testclient", time.time())] * 80,
            )
        assert (
            user.post(
                "/api/auth/login", json={"role": "applicant", "username": "unknown"}
            ).status_code
            == 429
        )
        assert (
            user.post(
                "/api/auth/login",
                json={"role": "admin", "username": "admin", "password": ADMIN_PASSWORD},
            ).status_code
            == 200
        )
        with app.state.db.connect(write=True) as conn:
            conn.execute("UPDATE login_attempts SET ts=?", (time.time() - 61,))
        user.headers["X-CSRF-Token"] = user.get("/api/auth/me").json()["csrf"]
        assert (
            user.post(
                "/api/auth/login", json={"role": "applicant", "username": "unknown"}
            ).status_code
            == 401
        )
        assert len(app.state.db.all("SELECT * FROM login_attempts")) == 1


def test_successful_name_logins_do_not_consume_failure_limit(app):
    person(app)
    for _ in range(110):
        assert app.state.auth.login("applicant", "张三", "", "office-gateway")
    assert app.state.db.all("SELECT * FROM login_attempts") == []


def test_unregistered_name_response_includes_only_admin_contact_information(app):
    app.state.service.save_admin_contact({"name": "管理员甲", "contact": "分机 321"})
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE admin SET contact='备用联系说明'")
    person(app, "不应公开的申请人名单")
    with TestClient(app) as guest:
        guest.headers["X-CSRF-Token"] = guest.get("/api/auth/me").json()["csrf"]
        response = guest.post(
            "/api/auth/login", json={"role": "applicant", "username": "名单不存在", "password": ""}
        )
        assert response.status_code == 401
        assert response.json()["admin_contacts"] == [{"name": "管理员甲", "contact": "分机 321"}]
        assert response.json()["admin_contact"] == "备用联系说明"
        assert "不应公开的申请人名单" not in response.text
        assert guest.get("/api/admin-contacts").status_code == 401
        response = guest.post(
            "/api/auth/login", json={"role": "admin", "username": "admin", "password": "incorrect"}
        )
        assert response.status_code == 401 and "admin_contacts" not in response.json()


def test_unknown_applicant_browser_gets_contacts_and_one_acknowledgement(app, live_page):
    from playwright.sync_api import expect

    app.state.service.save_admin_contact({"name": "管理员甲", "contact": "分机 321"})
    page = live_page
    page.locator("#login-name").fill("尚未登记姓名")
    page.locator("#login-form button[type=submit]").click()
    expect(page.locator("#alert-dialog")).to_be_visible()
    expect(page.locator("#alert-message")).to_contain_text("管理员甲")
    expect(page.locator("#alert-message")).to_contain_text("分机 321")
    expect(page.locator("#alert-dialog").get_by_role("button")).to_have_count(1)
    page.locator("#alert-dialog").get_by_role("button", name="确定", exact=True).click()
    expect(page.locator("#alert-dialog")).not_to_be_visible()
    expect(page.locator("#login-name")).to_have_value("尚未登记姓名")
