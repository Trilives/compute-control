"""Applicant names ignore English letter case without changing identity or display names."""

import pytest
from conftest import ADMIN_PASSWORD, login, person
from fastapi.testclient import TestClient

from compute_control.services import new_id


@pytest.mark.parametrize("entered", ["alice", "ALICE", "aLiCe", " Alice "])
def test_english_name_login_keeps_original_identity_and_spelling(app, entered):
    owner = person(app, "Alice")
    with TestClient(app) as user:
        login(user, "applicant", entered, "")
        me = user.get("/api/auth/me").json()
        assert me["person_id"] == owner["id"]
        assert me["name"] == "Alice"


def test_mixed_name_duplicate_rename_and_deleted_restore_use_same_matching(app, client):
    owner = person(app, "张三-Alice")
    with TestClient(app) as user:
        login(user, "applicant", "张三-ALICE", "")
    assert client.post("/api/admin/applicants", json={"name": "张三-aLiCe"}).status_code == 409
    other = person(app, "Bob")
    assert (
        client.put("/api/admin/applicants/" + other["id"], json={"name": "张三-alice"}).status_code
        == 409
    )
    renamed = client.put("/api/admin/applicants/" + owner["id"], json={"name": "张三-ALICE"}).json()
    assert renamed["id"] == owner["id"] and renamed["name"] == "张三-ALICE"
    assert client.delete("/api/admin/applicants/" + owner["id"]).status_code == 200
    restored = client.post("/api/admin/applicants", json={"name": "张三-alice"}).json()
    assert restored["id"] == owner["id"] and restored["enabled"]


def test_legacy_case_conflict_never_chooses_an_arbitrary_identity(app, client):
    owner = person(app, "Alice")
    with app.state.db.connect(write=True) as conn:
        conn.execute("INSERT INTO applicants(id,name,enabled) VALUES (?, 'ALICE', 1)", (new_id(),))
    with TestClient(app) as user:
        user.headers["X-CSRF-Token"] = user.get("/api/auth/me").json()["csrf"]
        result = user.post("/api/auth/login", json={"role": "applicant", "username": "alice"})
        assert result.status_code == 401 and "联系管理员" in result.json()["detail"]
    assert client.post("/api/admin/applicants", json={"name": "aLiCe"}).status_code == 409
    assert (
        client.put("/api/admin/applicants/" + owner["id"], json={"name": "Alice-研发"}).status_code
        == 200
    )
    with TestClient(app) as user:
        login(user, "applicant", "alice", "")
        assert user.get("/api/auth/me").json()["person_id"] != owner["id"]


def test_admin_username_remains_case_sensitive_and_disabled_names_cannot_login(app):
    owner = person(app, "Alice")
    app.state.service.save_person({"name": "Alice", "enabled": False, "quota": None}, owner["id"])
    with TestClient(app) as user:
        user.headers["X-CSRF-Token"] = user.get("/api/auth/me").json()["csrf"]
        for data in [
            {"role": "applicant", "username": "ALICE"},
            {"role": "admin", "username": "ADMIN", "password": ADMIN_PASSWORD},
        ]:
            assert user.post("/api/auth/login", json=data).status_code == 401
