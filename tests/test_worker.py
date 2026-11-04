import secrets
import time

from conftest import apply, login, machine, person
from fastapi.testclient import TestClient

from compute_control.security import DomainError
from compute_control.worker import Mailbox, Worker, deliver_password

TEST_MACHINE_CREDENTIAL = secrets.token_urlsafe(24)


class MemoryMailbox:
    def __init__(self, password=TEST_MACHINE_CREDENTIAL):
        self.password = password

    def take(self, job_id):
        return self.password


def worker(app, password=TEST_MACHINE_CREDENTIAL):
    return Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox(password))


def test_background_monitoring_does_not_depend_on_page_lease(app, client):
    machine(app)
    sampler = worker(app)
    assert len(sampler.targets()) == 1
    assert client.post("/api/viewer-lease").status_code == 200
    assert len(sampler.targets()) == 1
    client.delete("/api/viewer-lease")
    assert len(sampler.targets()) == 1


def test_leases_merge_and_expire(app, client):
    owner = person(app)
    machine(app)
    sampler = worker(app)
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        client.post("/api/viewer-lease")
        user.post("/api/viewer-lease")
        assert len(sampler.targets()) == 1
        with app.state.db.connect(write=True) as conn:
            conn.execute("UPDATE viewer_leases SET expires=?", (time.time() - 1,))
        assert len(sampler.targets()) == 1


def test_creation_continues_without_lease(app):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    worker(app).process_job(job)
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (job["account_id"],))
    assert account["state"] == "active" and account["uid"] == 1001 and account["occupies"] == 1


def test_lost_password_never_persisted_and_can_be_redelivered(app):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    worker(app, None).process_job(job)
    assert (
        app.state.db.one("SELECT state FROM jobs WHERE id=?", (job["id"],))["state"]
        == "waiting_password"
    )
    mailbox = Mailbox(app.state.config.runtime_dir, app.state.db)
    try:
        assert deliver_password(app.state.config.runtime_dir, job["id"], "new-example-password")
        assert mailbox.take(job["id"]) == "new-example-password"
        assert mailbox.take(job["id"]) is None
    finally:
        mailbox.close()
    for path in app.state.config.runtime_dir.glob("*.sqlite3*"):
        assert b"new-example-password" not in path.read_bytes()


def test_closed_before_creation_cancels_only_unstarted_job(app, client):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    client.patch("/api/admin/machines/" + target["id"], json={"available": False})
    worker(app).process_job(job)
    assert app.state.service.person(owner["id"])["used"] == 0
    assert app.state.service.remote.calls[-1][1] == "inspect"


def test_disconnect_preserves_quota_and_requires_reconciliation(app):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    app.state.service.remote.error = DomainError("连接中断", 502)
    worker(app).process_job(job)
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (job["account_id"],))
    assert account["state"] == "needs_reconcile" and account["occupies"] == 1
    assert (
        app.state.db.one("SELECT state FROM jobs WHERE id=?", (job["id"],))["state"]
        == "needs_reconcile"
    )


def test_archive_failure_preserves_account_and_quota(app):
    owner, target = person(app), machine(app)
    created = apply(app, owner, target)
    worker(app).process_job(created)
    job = app.state.service.retire_account({"role": "admin"}, created["account_id"])
    app.state.service.remote.error = DomainError("归档空间不足", 502)
    worker(app).process_job(job)
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (created["account_id"],))
    assert account["state"] == "archive_failed" and account["occupies"] == 1
    assert app.state.db.all("SELECT * FROM archives") == []


def test_archive_success_releases_quota_but_preserves_home_cleanup_record(app):
    owner, target = person(app), machine(app)
    created = apply(app, owner, target)
    worker(app).process_job(created)
    job = app.state.service.retire_account({"role": "admin"}, created["account_id"])
    worker(app).process_job(job)
    account = app.state.db.one("SELECT * FROM accounts WHERE id=?", (created["account_id"],))
    assert account["state"] == "home_cleanup_pending" and account["occupies"] == 0
    assert len(app.state.db.all("SELECT * FROM archives")) == 1


def test_restart_never_blindly_repeats_remote_operation(app):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
    sampler = worker(app)
    sampler.recover()
    assert (
        app.state.db.one("SELECT state FROM jobs WHERE id=?", (job["id"],))["state"]
        == "needs_reconcile"
    )
    assert app.state.service.person(owner["id"])["used"] == 1
    assert app.state.service.remote.calls[-1][1] == "inspect"


def test_refresh_coalesces_clicks_and_stale_error_retains_snapshot(app, client):
    target = machine(app)
    path = "/api/machines/" + target["id"] + "/refresh"
    assert client.post(path).json() == {"queued": True}
    requested = app.state.db.one("SELECT refresh_requested FROM machines")["refresh_requested"]
    for _ in range(25):
        assert client.post(path).json() == {"queued": True, "coalesced": True}
    assert (
        app.state.db.one("SELECT refresh_requested FROM machines")["refresh_requested"] == requested
    )
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "UPDATE machines SET refresh_requested=?,last_probe=?",
            (time.time() - 10, time.time() - 20),
        )
    assert client.post(path).json()["coalesced"]  # Still pending, even after five seconds.
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE machines SET last_probe=?", (time.time(),))
    assert client.post(path).json() == {"queued": True}
    before = app.state.db.one("SELECT snapshot FROM machines WHERE id=?", (target["id"],))
    app.state.service.remote.error = DomainError("主机离线", 502)
    worker(app).sample(target)
    after = app.state.db.one("SELECT * FROM machines WHERE id=?", (target["id"],))
    assert after["snapshot"] == before["snapshot"] and after["error"] == "主机离线"


def test_admin_cleanup_and_archive_deletion_leave_audit_and_tombstone(app, client):
    owner, target = person(app), machine(app)
    created = apply(app, owner, target)
    runner = worker(app)
    runner.process_job(created)
    runner.process_job(app.state.service.retire_account({"role": "admin"}, created["account_id"]))
    archive = app.state.db.one("SELECT * FROM archives")
    assert (
        client.post(
            "/api/admin/archives/" + archive["id"] + "/delete", json={"confirmed": True}
        ).status_code
        == 409
    )
    response = client.post(
        "/api/accounts/" + created["account_id"] + "/cleanup", json={"confirmed": True}
    )
    assert response.status_code == 202
    runner.process_job(response.json())
    response = client.post(
        "/api/admin/archives/" + archive["id"] + "/delete", json={"confirmed": True}
    )
    assert response.status_code == 202
    runner.process_job(response.json())
    archived = app.state.db.one("SELECT * FROM archives WHERE id=?", (archive["id"],))
    assert archived["deleted"] is not None
    assert (
        app.state.db.one("SELECT state FROM accounts WHERE id=?", (created["account_id"],))["state"]
        == "retired"
    )


def test_unexpected_worker_failure_does_not_leave_running_or_release_quota(app, monkeypatch):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)

    def crash(*args, **kwargs):
        raise RuntimeError("unexpected raw secret in error")

    monkeypatch.setattr(app.state.service.remote, "execute", crash)
    worker(app).process_job(job)
    result = app.state.db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    assert result["state"] == "needs_reconcile"
    assert "raw secret" not in result["error"]
    assert app.state.service.person(owner["id"])["used"] == 1


def test_cpu_percent_uses_elapsed_counters_and_reboot_is_unknown(app):
    target = machine(app)
    first = app.state.service.remote.execute(target, "inspect")
    first["cpu_times"] = {"total": 1000, "idle": 500}
    app.state.service.save_snapshot(target["id"], first, False)
    second = app.state.service.remote.execute(target, "inspect")
    second["cpu_times"] = {"total": 2000, "idle": 800}
    app.state.service.save_snapshot(target["id"], second, False)
    data = app.state.service.visible_machines({"role": "admin"})[0]
    assert data["snapshot"]["cpu_percent"] == 70.0
    second["cpu_times"] = {"total": 10, "idle": 5}
    app.state.service.save_snapshot(target["id"], second, False)
    data = app.state.service.visible_machines({"role": "admin"})[0]
    assert data["snapshot"]["cpu_percent"] is None


def test_hourly_defaults_and_legacy_lease_no_longer_controls_probe(app, client):
    machine(app)
    assert app.state.config.snapshot_seconds == 3600
    response = client.post("/api/viewer-lease")
    assert response.json()["scheduled"] is False
    assert app.state.db.one("SELECT * FROM viewer_leases") is None
    assert len(worker(app).targets()) == 1


def test_three_character_password_passes_memory_handoff(app):
    owner, target = person(app), machine(app)
    job = apply(app, owner, target)
    mailbox = Mailbox(app.state.config.runtime_dir, app.state.db)
    try:
        assert deliver_password(app.state.config.runtime_dir, job["id"], "123")
        assert mailbox.take(job["id"]) == "123"
    finally:
        mailbox.close()
