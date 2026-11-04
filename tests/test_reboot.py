"""Reboot paths are simulated only; no test sends a power command to a real host."""

import copy
import json
import time

from conftest import REPORT, machine
from test_worker import MemoryMailbox

from compute_control.worker import Worker


def reboot_ready(app, monkeypatch, busy=False):
    target = machine(app)
    report = copy.deepcopy(REPORT)
    report.update(
        identity=target["identity"],
        boot_id="boot-old",
        active_user_processes=[{"pid": 123, "username": "researcher", "name": "python"}]
        if busy
        else [],
    )
    report["capabilities"]["can_reboot"] = True
    calls = []

    def execute(host, action, data=None):
        calls.append((action, data))
        if action == "inspect":
            return copy.deepcopy(report)
        assert action == "reboot"
        return {"state": "reboot_scheduled", "boot_id": "boot-old"}

    monkeypatch.setattr(app.state.service.remote, "execute", execute)
    return target, report, calls


def test_busy_reboot_requires_third_confirmation(app, client, monkeypatch):
    target, report, calls = reboot_ready(app, monkeypatch, busy=True)
    url = "/api/admin/machines/" + target["id"] + "/reboot"
    assert client.post(url, json={"confirmed": False}).status_code == 400
    assert client.post(url, json={"confirmed": True}).status_code == 409
    assert app.state.db.all("SELECT id FROM jobs WHERE kind='reboot'") == []
    result = client.post(url, json={"confirmed": True, "force": True, "force_confirmed": True})
    assert result.status_code == 202
    assert (
        app.state.db.one("SELECT available FROM machines WHERE id=?", (target["id"],))["available"]
        == 0
    )
    job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (result.json()["id"],))
    Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox()).process_job(job)
    assert calls[-1][0] == "reboot" and calls[-1][1]["force"] is True
    assert (
        app.state.db.one("SELECT step FROM jobs WHERE id=?", (job["id"],))["step"]
        == "reboot_scheduled"
    )
    report["boot_id"] = "boot-new"
    app.state.service.save_snapshot(target["id"], copy.deepcopy(report), False)
    assert (
        app.state.db.one("SELECT step FROM jobs WHERE id=?", (job["id"],))["step"]
        == "boot_verified"
    )


def test_force_does_not_wait_for_platform_task(app, client, monkeypatch):
    target, report, calls = reboot_ready(app, monkeypatch)
    now = time.time()
    with app.state.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO jobs(id,machine_id,kind,state,created,updated,request_key) VALUES (?,?,?,?,?,?,?)",
            ("busy", target["id"], "retire", "running", now, now, "busy-key"),
        )
    url = "/api/admin/machines/" + target["id"] + "/reboot"
    assert client.post(url, json={"confirmed": True}).status_code == 409
    result = client.post(url, json={"confirmed": True, "force": True, "force_confirmed": True})
    assert result.status_code == 202
    job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (result.json()["id"],))
    assert json.loads(job["params"])["force"] is True
    Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox()).process_job(job)
    assert calls[-1][0] == "reboot"
    # An interrupted account operation is never falsely marked successful by reboot.
    assert app.state.db.one("SELECT state FROM jobs WHERE id='busy'")["state"] == "running"


def test_idle_reboot_uses_normal_confirmation(app, client, monkeypatch):
    target, _, _ = reboot_ready(app, monkeypatch)
    result = client.post(
        "/api/admin/machines/" + target["id"] + "/reboot", json={"confirmed": True}
    )
    assert result.status_code == 202
    job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (result.json()["id"],))
    assert json.loads(job["params"])["force"] is False


def test_remote_reboot_is_fixed_command_and_same_job_is_not_repeated(tmp_path, monkeypatch):
    from pathlib import Path

    from compute_control import remote

    (tmp_path / "boot-id").write_text("boot-old")
    monkeypatch.setattr(remote, "safe_directory", lambda *args: tmp_path)
    monkeypatch.setattr(
        remote,
        "Path",
        lambda value: (
            tmp_path / "boot-id" if str(value) == "/proc/sys/kernel/random/boot_id" else Path(value)
        ),
    )
    monkeypatch.setattr(
        remote.shutil, "which", lambda name: "/usr/sbin/shutdown" if name == "shutdown" else None
    )
    calls = []
    monkeypatch.setattr(remote, "run", lambda args: calls.append(args))
    payload = {"job_id": "a" * 32}
    first = remote.reboot(payload)
    assert remote.reboot(payload) == first
    assert calls == [["/usr/sbin/shutdown", "-r", "now"]]


def test_force_bypasses_remote_machine_lock(tmp_path, monkeypatch, capsys):
    import fcntl
    import io

    from compute_control import remote

    monkeypatch.setattr(remote, "safe_directory", lambda *args: tmp_path)
    calls = []
    monkeypatch.setattr(
        remote, "reboot", lambda payload: calls.append(payload["force"]) or {"state": "sent"}
    )
    with (tmp_path / "machine.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for force in [False, True]:
            payload = {"action": "reboot", "job_id": "a" * 32, "force": force}
            monkeypatch.setattr(remote.sys, "stdin", io.StringIO(json.dumps(payload) + "\n"))
            remote.main()
            response = json.loads(capsys.readouterr().out.splitlines()[-1])
            assert response["ok"] is force
    assert calls == [True]


def test_forced_reboot_is_dispatched_while_normal_worker_is_busy(app, client, monkeypatch):
    import threading
    from dataclasses import replace

    from conftest import apply, person

    target, _, calls = reboot_ready(app, monkeypatch)
    normal = apply(app, person(app), target)
    busy, release, sent, stop = (threading.Event() for _ in range(4))
    runner = Worker(app.state.db, app.state.service, app.state.config, MemoryMailbox())
    original = runner.process_reboot

    def blocked_job(job):
        with app.state.db.connect(write=True) as conn:
            conn.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
        busy.set()
        release.wait(10)

    def power_job(job):
        original(job)
        sent.set()

    monkeypatch.setattr(runner, "process_job", blocked_job)
    monkeypatch.setattr(runner, "process_reboot", power_job)
    runner.config = replace(app.state.config, ssh_concurrency=1)
    thread = threading.Thread(target=runner.run, args=(stop,), daemon=True)
    thread.start()
    try:
        assert busy.wait(3)
        response = client.post(
            "/api/admin/machines/" + target["id"] + "/reboot",
            json={"confirmed": True, "force": True, "force_confirmed": True},
        )
        assert response.status_code == 202
        assert sent.wait(4), "force waited for the occupied normal worker slot"
        assert calls[-1][0] == "reboot" and not release.is_set()
        assert (
            app.state.db.one("SELECT state FROM jobs WHERE id=?", (normal["id"],))["state"]
            == "running"
        )
    finally:
        stop.set()
        release.set()
        thread.join(5)
    assert not thread.is_alive()
