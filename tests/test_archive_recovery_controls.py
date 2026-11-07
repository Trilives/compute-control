"""Archive rows select the exact archive and require a password for legacy records."""

import json

from test_account_recovery import recycled


def test_archive_restore_requests_password_when_not_saved(app, client):
    _, account_id, _ = recycled(app)
    archive = app.state.db.one("SELECT * FROM archives")
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE accounts SET credential_cipher=NULL WHERE id=?", (account_id,))
    rows = client.get("/api/admin/archives").json()
    assert rows[0]["password_available"] is False
    response = client.post(f"/api/admin/archives/{archive['id']}/restore", json={"confirmed": True})
    assert response.status_code == 400
    assert (
        app.state.db.one("SELECT occupies FROM accounts WHERE id=?", (account_id,))["occupies"] == 0
    )
    response = client.post(
        f"/api/admin/archives/{archive['id']}/restore",
        json={"confirmed": True, "password": "new-legacy-password"},
    )
    assert response.status_code == 202
    assert "new-legacy-password" not in response.text and "params" not in response.json()
    job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (response.json()["id"],))
    assert "new-legacy-password" not in job["params"]
    params = json.loads(job["params"])
    assert params["archive_id"] == archive["id"] and params["archive_path"] == archive["path"]
    assert (
        app.state.service.secrets.decrypt(params["restore_credential_cipher"])["password"]
        == "new-legacy-password"
    )


def test_archive_restore_uses_saved_password_and_persists_after_success(app, client):
    _, account_id, runner = recycled(app)
    archive = app.state.db.one("SELECT * FROM archives")
    response = client.post(f"/api/admin/archives/{archive['id']}/restore", json={"confirmed": True})
    assert response.status_code == 202
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE id=?", (response.json()["id"],)))
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
    assert account["state"] == "active"
    assert app.state.service.secrets.decrypt(account["credential_cipher"])["password"]


def test_deleted_archive_cannot_restore_and_member_cannot_request(app, client):
    _, account_id, _ = recycled(app)
    archive = app.state.db.one("SELECT * FROM archives")
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE archives SET deleted=1 WHERE id=?", (archive["id"],))
    assert (
        client.post(
            f"/api/admin/archives/{archive['id']}/restore", json={"confirmed": True}
        ).status_code
        == 404
    )
    assert (
        app.state.db.one("SELECT occupies FROM accounts WHERE id=?", (account_id,))["occupies"] == 0
    )
