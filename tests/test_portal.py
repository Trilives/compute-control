import copy
import sqlite3
import threading
import time

from conftest import REPORT, apply, login, machine, person
from fastapi.testclient import TestClient
from test_worker import MemoryMailbox

from compute_control.db import SCHEMA, Database
from compute_control.security import DomainError
from compute_control.worker import Worker


def active(app, owner=None, target=None):
    owner, target = owner or person(app), target or machine(app)
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    task = apply(app, owner, target)
    runner.process_job(task)
    return owner, target, task["account_id"], runner


def test_announcements_pin_history_delete_and_permissions(app, client):
    ids = []
    for pinned in (False, True):
        response = client.post(
            "/api/admin/announcements",
            json={"title": "通知", "content": "<script>文本</script>", "pinned": pinned},
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        assert len(user.get("/api/announcements").json()) == 2
        assert [item["id"] for item in user.get("/api/announcements?pinned=true").json()] == [
            ids[1]
        ]
        assert (
            user.post("/api/admin/announcements", json={"title": "x", "content": "x"}).status_code
            == 403
        )
        assert user.delete("/api/admin/announcements/" + ids[0]).status_code == 403
    assert (
        client.patch("/api/admin/announcements/" + ids[0], json={"pinned": True}).status_code == 200
    )
    assert len(client.get("/api/announcements?pinned=true").json()) == 2
    assert client.delete("/api/admin/announcements/" + ids[1]).status_code == 200
    assert len(client.get("/api/announcements").json()) == 1
    assert app.state.db.one("SELECT deleted FROM announcements WHERE id=?", (ids[1],))["deleted"]


def test_contact_directory_is_separate_from_console_login(app, client):
    before = app.state.db.one("SELECT COUNT(*) AS n FROM admin")["n"]
    contact = client.post(
        "/api/admin/contacts", json={"name": "值班管理员", "contact": "分机 123\n内网 IM"}
    ).json()
    assert (
        client.put(
            "/api/admin/contacts/" + contact["id"], json={"name": "管理员乙", "contact": "分机 456"}
        ).status_code
        == 200
    )
    person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        assert user.get("/api/admin-contacts").json()[0]["name"] == "管理员乙"
        assert user.delete("/api/admin/contacts/" + contact["id"]).status_code == 403
    assert app.state.db.one("SELECT COUNT(*) AS n FROM admin")["n"] == before
    assert client.delete("/api/admin/contacts/" + contact["id"]).status_code == 200
    assert client.get("/api/admin-contacts").json() == []


def test_permission_request_owner_decision_and_duplicate_processing(app, client):
    owner, target, account_id, runner = active(app)
    second = person(app, "李四")
    with TestClient(app) as user:
        login(user, "applicant", second["name"], "")
        assert (
            user.post(
                "/api/permission-requests", json={"account_id": account_id, "reason": "安装依赖"}
            ).status_code
            == 404
        )
        login(user, "applicant", owner["name"], "")
        data = {"account_id": account_id, "reason": "需安装项目开发依赖"}
        request = user.post("/api/permission-requests", json=data)
        assert request.status_code == 201
        assert user.post("/api/permission-requests", json=data).status_code == 409
        assert (
            user.put(
                "/api/admin/accounts/" + account_id + "/privilege", json={"enabled": True}
            ).status_code
            == 403
        )
        request_id = request.json()["id"]
        url = "/api/admin/permission-requests/" + request_id + "/decision"
        assert user.post(url, json={"action": "grant"}).status_code == 403
        response = client.post(
            url, json={"action": "help_install", "response": "今天管理员帮助安装"}
        )
        assert response.status_code == 200 and response.json()["job_id"] is None
        assert user.get("/api/permission-requests").json()[0]["response"] == "今天管理员帮助安装"
        assert client.post(url, json={"action": "grant"}).status_code == 409
        again = user.post("/api/permission-requests", json=data).json()
    decision = client.post(
        "/api/admin/permission-requests/" + again["id"] + "/decision", json={"action": "grant"}
    )
    job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (decision.json()["job_id"],))
    assert job["kind"] == "set_privilege"
    runner.process_job(job)
    assert client.get("/api/accounts").json()[0]["is_admin"] == 1
    result = client.put("/api/admin/accounts/" + account_id + "/privilege", json={"enabled": False})
    assert result.status_code == 202
    runner.process_job(app.state.db.one("SELECT * FROM jobs WHERE id=?", (result.json()["id"],)))
    assert client.get("/api/accounts").json()[0]["is_admin"] == 0


def test_permission_request_does_not_follow_reassigned_account(app, client):
    owner, _, account_id, _ = active(app)
    request = app.state.service.request_permission(
        {"role": "applicant", "person_id": owner["id"]}, account_id, "安装工具"
    )
    receiver = person(app, "李四")
    app.state.service.assign_account(account_id, receiver["id"])
    url = "/api/admin/permission-requests/" + request["id"] + "/decision"
    assert client.post(url, json={"action": "grant"}).status_code == 409
    assert client.post(url, json={"action": "reject"}).status_code == 200


def test_history_persists_filters_24h_and_checks_visibility(app, client):
    target = machine(app, available=False)
    now = time.time()
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO machine_samples VALUES (?,?,?,NULL)", (target["id"], now - 90000, "{}")
        )
    history = client.get("/api/machines/" + target["id"] + "/history").json()
    assert len(history["samples"]) == 1 and history["window_seconds"] == 86400
    assert "users" not in history["samples"][0]["snapshot"]
    person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        assert user.get("/api/machines/" + target["id"] + "/history").status_code == 404
    app.state.service.save_snapshot(
        target["id"], copy.deepcopy(REPORT) | {"identity": target["identity"]}, False
    )
    assert len(app.state.db.all("SELECT * FROM machine_samples")) == 2


def test_hourly_sampler_runs_without_viewers_and_manual_refresh_bypasses_interval(app, monkeypatch):
    target = machine(app)
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    for manual in (False, True):
        now = time.time()
        with app.state.db.connect(write=True) as conn:
            conn.execute(
                "UPDATE machines SET last_probe=?,refresh_requested=? WHERE id=?",
                (now if manual else now - 3601, now + 0.01 if manual else None, target["id"]),
            )
        stop = threading.Event()
        observed = []

        def sample(machine, observed=observed, stop=stop):
            observed.append(machine["id"])
            stop.set()

        monkeypatch.setattr(runner, "sample", sample)
        thread = threading.Thread(target=runner.run, args=(stop,), daemon=True)
        thread.start()
        try:
            assert stop.wait(3)
        finally:
            stop.set()
            thread.join(3)
        assert observed == [target["id"]]
    assert app.state.db.all("SELECT * FROM viewer_leases") == []


def test_failed_probe_is_recorded_without_destroying_last_success(app):
    target = machine(app)
    original = target["snapshot"]
    app.state.service.remote.error = DomainError("连接失败", 502)
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    runner.sample(target)
    current = app.state.db.one("SELECT * FROM machines WHERE id=?", (target["id"],))
    assert current["last_probe"] > target["last_probe"] and current["snapshot"] == original
    assert (
        app.state.db.one("SELECT * FROM machine_samples WHERE error IS NOT NULL")["error"]
        == "连接失败"
    )


def test_v2_migration_creates_history_and_keeps_old_records(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    schema = (
        SCHEMA[: SCHEMA.index("CREATE TABLE IF NOT EXISTS announcements")]
        + SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS audit_logs") :]
    )
    schema = schema.replace(", last_probe REAL", "").replace(
        "\n is_admin INTEGER, privilege_sampled REAL,", ""
    )
    with sqlite3.connect(runtime / "control.sqlite3") as conn:
        conn.executescript(schema)
        conn.execute("INSERT INTO admin VALUES (1,'admin','hash','联系甲')")
        conn.execute("PRAGMA user_version=2")
    db = Database(runtime)
    assert db.one("SELECT contact FROM admin")["contact"] == "联系甲"
    assert db.all("SELECT * FROM announcements") == []
    backup = list((runtime / "backups").glob("before-v4-from-v2-*.sqlite3"))
    assert len(backup) == 1
    with sqlite3.connect(backup[0]) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    Database(runtime)
    assert len(list((runtime / "backups").glob("before-v4-from-v2-*.sqlite3"))) == 1


def test_database_restart_does_not_add_synthetic_or_duplicate_history(app):
    machine(app)
    original = app.state.db.all("SELECT * FROM machine_samples")
    Database(app.state.config.runtime_dir)
    Database(app.state.config.runtime_dir)
    assert app.state.db.all("SELECT * FROM machine_samples") == original
