from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import apply, login, machine, person
from fastapi.testclient import TestClient

from compute_control.security import DomainError
from compute_control.services import new_id


def test_quota_reservation_is_atomic_under_concurrency(app):
    owner = person(app)
    targets = [machine(app, f"192.0.2.{i}") for i in range(1, 9)]

    def submit(target):
        try:
            return apply(app, owner, target)["id"]
        except DomainError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, targets))
    assert sum(bool(item) for item in results) == 3
    assert app.state.service.person(owner["id"])["used"] == 3


def test_idempotency_allows_multiple_accounts_on_same_host_with_total_quota(app):
    owner, target = person(app), machine(app)
    key = new_id()
    first = apply(app, owner, target, key=key)
    assert apply(app, owner, target, key=key)["id"] == first["id"]
    assert app.state.service.person(owner["id"])["used"] == 1
    apply(app, owner, target, username="different")
    apply(app, owner, target, username="third")
    assert app.state.service.person(owner["id"])["used"] == 3
    with pytest.raises(DomainError, match="上限"):
        apply(app, owner, target, username="fourth")


def test_admin_proxy_also_obeys_quota(app):
    owner, target = person(app, quota=0), machine(app)
    with pytest.raises(DomainError, match="上限"):
        app.state.service.request_account(
            {"role": "admin"}, target["id"], "researcher", "example-password", new_id(), owner["id"]
        )


def test_personal_quota_and_lowering_do_not_remove_accounts(app, client):
    owner = person(app, quota=4)
    for index in range(4):
        apply(app, owner, machine(app, f"192.0.2.{index + 1}"))
    client.put("/api/admin/applicants/" + owner["id"], json={"name": "张三", "quota": 1})
    assert app.state.service.person(owner["id"])["used"] == 4
    with pytest.raises(DomainError, match="上限"):
        apply(app, owner, machine(app, "192.0.2.5"))


def test_other_person_cannot_access_or_recycle_account(app):
    owner, stranger, target = person(app), person(app, "李四"), machine(app)
    job = apply(app, owner, target)
    with TestClient(app) as user:
        login(user, "applicant", stranger["name"], "")
        assert user.get("/api/accounts").json() == []
        assert user.get("/api/jobs").json() == []
        assert (
            user.post(
                "/api/accounts/" + job["account_id"] + "/retire", json={"confirmed": True}
            ).status_code
            == 404
        )
        assert (
            user.post(
                "/api/jobs/" + job["id"] + "/password", json={"password": "example-password"}
            ).status_code
            == 404
        )


def test_closed_machine_hidden_except_existing_owner(app, client):
    owner, target = person(app), machine(app)
    apply(app, owner, target)
    client.patch("/api/admin/machines/" + target["id"], json={"available": False})
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        assert len(user.get("/api/machines").json()) == 1
        person(app, "李四")
        login(user, "applicant", "李四", "")
        assert user.get("/api/machines").json() == []


def test_credentials_encrypted_and_not_returned(app, client):
    target = machine(app)
    public = client.get("/api/machines").json()[0]
    assert "credentials" not in public and "host_key" not in public
    assert "example-ssh-credential" not in target["credentials"]
    assert (
        app.state.service.secrets.decrypt(target["credentials"])["sudo_password"]
        == "example-sudo-credential"
    )


def test_api_ignores_client_owner_for_applicant(app):
    owner, other, target = person(app), person(app, "李四"), machine(app)
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        response = user.post(
            "/api/accounts",
            json={
                "machine_id": target["id"],
                "person_id": other["id"],
                "username": "researcher",
                "password": "example-password",
                "request_key": new_id(),
            },
        )
        assert response.status_code == 202
        assert user.get("/api/accounts").json()[0]["person_id"] == owner["id"]
        assert "example-password" not in response.text


@pytest.mark.parametrize("username", ["legacy_user", "bin", "root", "operator"])
def test_existing_username_rejected_before_quota_or_task(app, username):
    owner, target = person(app), machine(app)
    report = app.state.service.remote.execute(target, "inspect")
    report["existing_usernames"] = ["legacy_user", "bin", "root", "operator"]
    app.state.service.save_snapshot(target["id"], report, False)
    with pytest.raises(DomainError, match="用户名已存在"):
        apply(app, owner, target, username=username)
    assert app.state.service.person(owner["id"])["used"] == 0
    assert app.state.db.all("SELECT id FROM jobs") == []


def test_same_machine_duplicate_name_for_different_people_is_rejected(app):
    first, second, target = person(app), person(app, "李四"), machine(app)
    apply(app, first, target, username="researcher")
    with pytest.raises(DomainError, match="用户名已有登记账号"):
        apply(app, second, target, username="researcher")
    assert app.state.service.person(second["id"])["used"] == 0


@pytest.mark.parametrize("password", ["123", "abc", "你好呀"])
def test_ordinary_password_only_requires_three_characters(app, password):
    owner, target = person(app), machine(app)
    job = app.state.service.request_account(
        {"role": "applicant", "person_id": owner["id"]},
        target["id"],
        "researcher",
        password,
        new_id(),
    )
    assert job["state"] == "queued"


def test_two_character_password_rejected_without_task(app):
    owner, target = person(app), machine(app)
    with pytest.raises(DomainError, match="3–256"):
        app.state.service.request_account(
            {"role": "applicant", "person_id": owner["id"]},
            target["id"],
            "researcher",
            "12",
            new_id(),
        )
    assert app.state.db.all("SELECT id FROM jobs") == []


def test_concurrent_same_host_accounts_still_share_global_quota(app):
    owner, target = person(app), machine(app)

    def submit(index):
        try:
            return apply(app, owner, target, username=f"researcher_{index}")["id"]
        except DomainError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert sum(bool(value) for value in results) == 3
    assert app.state.service.person(owner["id"])["used"] == 3
