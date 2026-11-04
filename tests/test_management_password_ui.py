"""Admin password forms and fixed remote operations, without touching host credentials."""

from types import SimpleNamespace

import pytest
from conftest import ADMIN_PASSWORD, machine, person
from playwright.sync_api import expect
from test_management_passwords import NEW_PASSWORD, PasswordRemote

from compute_control import remote


def test_password_forms_rotate_machine_then_web_and_clear_inputs(app, live_page):
    target = machine(app)
    app.state.service.remote = PasswordRemote(app.state.service.secrets)
    page = live_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.get_by_role("button", name="管理机器", exact=True).click()
    page.get_by_role("button", name="修改机器管理密码", exact=True).click()
    form = page.locator("#management-password-form")
    expect(form).to_contain_text(target["username"])
    form.locator("[name=current_password]").fill(ADMIN_PASSWORD)
    form.locator("[name=new_password]").fill(NEW_PASSWORD)
    form.locator("[name=confirm_password]").fill(NEW_PASSWORD)
    page.once("dialog", lambda dialog: dialog.accept())
    form.locator("[type=submit]").click()
    expect(page.locator("#management-password-dialog")).not_to_be_visible()
    expect(form.locator("[name=new_password]")).to_have_value("")
    page.get_by_role("button", name="修改网页管理密码", exact=True).click()
    form = page.locator("#web-password-form")
    form.locator("[name=current_password]").fill(ADMIN_PASSWORD)
    form.locator("[name=new_password]").fill(NEW_PASSWORD)
    form.locator("[name=confirm_password]").fill("mismatch-password")
    form.locator("[type=submit]").click()
    expect(page.locator("#alert-message")).to_contain_text("两次")
    page.locator("#alert-dialog").get_by_role("button", name="确定", exact=True).click()
    form.locator("[name=confirm_password]").fill(NEW_PASSWORD)
    form.locator("[type=submit]").click()
    expect(page.locator("#welcome")).to_be_visible()
    expect(form.locator("[name=current_password]")).to_have_value("")
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(NEW_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    expect(page.locator("#console")).to_be_visible()
    page.get_by_role("button", name="修改网页管理密码", exact=True).click()
    form.locator("[name=new_password]").fill("temporary-form-secret")
    page.locator("#web-password-dialog .dialog-heading button").click()
    expect(form.locator("[name=new_password]")).to_have_value("")
    page.get_by_role("button", name="退出", exact=True).click()
    owner = person(app)
    page.locator("#login-name").fill(owner["name"])
    page.locator("#login-form button[type=submit]").click()
    expect(page.get_by_role("button", name="修改网页管理密码", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="修改机器管理密码", exact=True)).to_have_count(0)
    assert not errors


def test_remote_management_password_is_bound_to_connection_user_and_idempotent(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(remote, "safe_directory", lambda *args: tmp_path)
    monkeypatch.setattr(
        remote,
        "local_user",
        lambda name: [name, "x", "1000", "1000", "", "/home/operator", "/bin/bash"],
    )
    monkeypatch.setattr(
        remote, "user_exists", lambda name: SimpleNamespace(pw_uid=1000, pw_dir="/home/operator")
    )
    calls = []
    monkeypatch.setattr(
        remote, "run", lambda args, input=None, **kwargs: calls.append((args, input))
    )
    payload = {"admin_username": "operator", "change_id": "a" * 32, "password": NEW_PASSWORD}
    assert remote.management_password(payload)["state"] == "changed"
    assert calls == [(["chpasswd"], f"operator:{NEW_PASSWORD}\n")]
    assert remote.management_password(payload)["state"] == "changed"
    assert len(calls) == 1
    assert remote.management_password_status(payload)["state"] == "changed"
    assert NEW_PASSWORD not in next(tmp_path.glob("*.json")).read_text()
    for password in [
        "x",
        "bad:new-password",
        "bad\nnew-password",
        "bad\rnew-password",
        "bad\x00new-password",
    ]:
        with pytest.raises(remote.Refuse):
            remote.management_password(payload | {"password": password})
    with pytest.raises(remote.Refuse):
        remote.management_password(payload | {"change_id": "../escape"})
    monkeypatch.setattr(remote, "local_user", lambda name: None)
    with pytest.raises(remote.Refuse):
        remote.management_password(payload | {"change_id": "b" * 32})
    assert len(calls) == 1


def test_remote_failed_change_is_recorded_without_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr(remote, "safe_directory", lambda *args: tmp_path)
    monkeypatch.setattr(
        remote, "local_user", lambda name: [name, "x", "1000", "1000", "", "/home/operator"]
    )
    monkeypatch.setattr(
        remote, "user_exists", lambda name: SimpleNamespace(pw_uid=1000, pw_dir="/home/operator")
    )

    def fail(*args):
        raise remote.Refuse("tool failed")

    monkeypatch.setattr(remote, "run", fail)
    payload = {"admin_username": "operator", "change_id": "c" * 32, "password": NEW_PASSWORD}
    with pytest.raises(remote.Refuse):
        remote.management_password(payload)
    assert remote.management_password_status(payload)["state"] == "failed"
    assert "tool failed" not in next(tmp_path.glob("*.json")).read_text()


def test_recovery_cancels_unsent_change_so_late_execution_cannot_rotate(monkeypatch, tmp_path):
    monkeypatch.setattr(remote, "safe_directory", lambda *args: tmp_path)
    monkeypatch.setattr(
        remote, "local_user", lambda name: [name, "x", "1000", "1000", "", "/home/operator"]
    )
    monkeypatch.setattr(
        remote, "user_exists", lambda name: SimpleNamespace(pw_uid=1000, pw_dir="/home/operator")
    )
    calls = []
    monkeypatch.setattr(remote, "run", lambda *args: calls.append(args))
    payload = {"admin_username": "operator", "change_id": "d" * 32, "password": NEW_PASSWORD}
    assert (
        remote.management_password_status(payload | {"cancel_if_not_started": True})["state"]
        == "cancelled"
    )
    with pytest.raises(remote.Refuse):
        remote.management_password(payload)
    assert not calls
