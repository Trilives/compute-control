"""Machine sudo credentials follow the login password unless explicitly separated."""

import pytest
from conftest import ADMIN_PASSWORD, person
from playwright.sync_api import expect
from test_machine_enrollment import machine_input
from test_management_passwords import NEW_PASSWORD, PasswordRemote, change


@pytest.mark.parametrize(
    ("separate", "sudo"), [(None, None), (False, "stale"), (True, "independent"), (True, "")]
)
def test_enrollment_normalizes_credentials_before_connection(app, client, separate, sudo):
    data = machine_input()
    data["available"] = True
    data.pop("sudo_password")
    if separate is not None:
        data["separate_sudo_password"] = separate
    if sudo is not None:
        data["sudo_password"] = sudo
    observed = []
    remote = app.state.service.remote
    execute = remote.execute

    def inspect(target, action, payload):
        observed.append(app.state.service.secrets.decrypt(target["credentials"]))
        return execute(target, action, payload)

    remote.execute = inspect
    response = client.post("/api/admin/machines", json=data)
    assert response.status_code == 201
    target = app.state.db.one("SELECT * FROM machines WHERE id=?", (response.json()["id"],))
    saved = app.state.service.secrets.decrypt(target["credentials"])
    expected = {
        "ssh_password": data["ssh_password"],
        "sudo_password": sudo if separate else data["ssh_password"],
        "separate_sudo_password": bool(separate),
    }
    assert saved == expected and observed[0] == expected
    visible = client.get("/api/machines").json()[0]
    assert visible["separate_sudo_password"] is bool(separate)
    assert "credentials" not in visible and "sudo_password" not in visible
    owner = person(app)
    applicant_machines = app.state.service.visible_machines(
        {"role": "applicant", "person_id": owner["id"]}
    )
    assert len(applicant_machines) == 1
    assert "separate_sudo_password" not in applicant_machines[0]


@pytest.mark.parametrize("separate", [False, True])
def test_explicit_mode_controls_rotation_even_when_passwords_match(app, client, separate):
    data = machine_input() | {
        "ssh_password": "example-ssh-credential",
        "sudo_password": "example-ssh-credential",
        "separate_sudo_password": separate,
    }
    response = client.post("/api/admin/machines", json=data)
    assert response.status_code == 201
    machine_id = response.json()["id"]
    remote = PasswordRemote(app.state.service.secrets)
    app.state.service.remote = remote
    result = client.post(f"/api/admin/machines/{machine_id}/password", json=change())
    assert result.json()["state"] == "verified" and remote.changes == 1
    saved = app.state.service.secrets.decrypt(
        app.state.db.one("SELECT credentials FROM machines WHERE id=?", (machine_id,))[
            "credentials"
        ]
    )
    assert saved["ssh_password"] == NEW_PASSWORD
    assert saved["sudo_password"] == (data["sudo_password"] if separate else NEW_PASSWORD)
    assert saved["separate_sudo_password"] is separate


def test_credential_update_changes_mode_only_after_successful_verification(app, client):
    data = machine_input() | {"separate_sudo_password": True}
    response = client.post("/api/admin/machines", json=data)
    assert response.status_code == 201
    machine_id = response.json()["id"]
    url = f"/api/admin/machines/{machine_id}"
    before = app.state.db.one("SELECT credentials FROM machines WHERE id=?", (machine_id,))
    from compute_control.security import DomainError

    app.state.service.remote.error = DomainError("连接不可用", 502)
    assert client.put(url, json=data | {"separate_sudo_password": False}).status_code == 502
    assert app.state.db.one("SELECT credentials FROM machines WHERE id=?", (machine_id,)) == before
    app.state.service.remote.error = None
    for separate in [False, True]:
        assert client.put(url, json=data | {"separate_sudo_password": separate}).status_code == 200
        assert client.get("/api/machines").json()[0]["separate_sudo_password"] is separate
        saved = app.state.service.secrets.decrypt(
            app.state.db.one("SELECT credentials FROM machines WHERE id=?", (machine_id,))[
                "credentials"
            ]
        )
        assert saved["sudo_password"] == (
            data["sudo_password"] if separate else data["ssh_password"]
        )


@pytest.mark.parametrize("same", [False, True])
def test_legacy_credentials_keep_their_working_mode_without_modifying_records(app, client, same):
    from conftest import machine

    target = machine(app)
    old = {"ssh_password": "legacy-login", "sudo_password": "legacy-login" if same else ""}
    encrypted = app.state.service.secrets.encrypt(old)
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE machines SET credentials=? WHERE id=?", (encrypted, target["id"]))
    assert client.get("/api/machines").json()[0]["separate_sudo_password"] is not same
    assert (
        app.state.db.one("SELECT credentials FROM machines WHERE id=?", (target["id"],))[
            "credentials"
        ]
        == encrypted
    )


def test_machine_form_defaults_to_linked_and_remembers_explicit_independent_mode(app, live_page):
    page = live_page
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.get_by_role("button", name="+ 添加机器", exact=True).click()
    form = page.locator("#machine-form")
    separate = form.get_by_role("checkbox", name="使用独立 sudo 密码", exact=True)
    sudo = form.locator("input[name=sudo_password]")
    expect(separate).not_to_be_checked()
    expect(sudo).not_to_be_visible()
    expect(sudo).to_be_disabled()
    separate.check()
    expect(sudo).to_be_visible()
    sudo.fill("discard-on-uncheck")
    separate.uncheck()
    expect(sudo).to_have_value("")
    separate.check()
    for field, value in machine_input().items():
        if field in ["name", "host", "username", "ssh_password", "sudo_password"]:
            form.locator(f"input[name={field}]").fill(value)
    form.get_by_role("button", name="检查并添加", exact=True).click()
    expect(page.locator("#machine-dialog")).not_to_be_visible()
    page.get_by_role("button", name="管理机器", exact=True).click()
    page.get_by_role("button", name="更新凭据", exact=True).click()
    expect(separate).to_be_checked()
    expect(sudo).to_be_visible()
    expect(sudo).to_have_value("")
    expect(form.locator("input[name=ssh_password]")).to_have_value("")
    separate.uncheck()
    form.locator("input[name=ssh_password]").fill("new-shared-password")
    form.get_by_role("button", name="检查并保存", exact=True).click()
    expect(page.locator("#machine-dialog")).not_to_be_visible()
    saved = app.state.service.secrets.decrypt(
        app.state.db.one("SELECT credentials FROM machines")["credentials"]
    )
    assert saved["ssh_password"] == saved["sudo_password"] == "new-shared-password"
    page.get_by_role("button", name="管理机器", exact=True).click()
    page.get_by_role("button", name="修改机器管理密码", exact=True).click()
    expect(page.locator("#management-password-sudo-mode")).to_contain_text("同步")
