import copy

import pytest
from fastapi.testclient import TestClient

from compute_control.app import create_app
from compute_control.config import Config
from compute_control.services import new_id

ADMIN_PASSWORD = "a-long-test-only-password"
REPORT = {
    "identity": "test-machine-identity",
    "capabilities": {
        "can_create": True,
        "missing_tools": [],
        "local_accounts_only": True,
        "gnu_tar": True,
    },
    "users": [],
    "gpus": [],
    "processes": [],
    "memory": {},
    "load": [0, 0, 0],
    "login_events": [{"username": "legacy", "ts": 1.0}],
    "login_status": "记录可能不完整",
    "gpu_error": None,
}


class FakeRemote:
    def __init__(self):
        self.calls = []
        self.error = None

    def identify(self, host, port):
        return {"fingerprint": "SHA256:" + "A" * 43, "host_key": "ssh-ed25519 test-key"}

    def execute(self, machine, action, data=None):
        self.calls.append((machine["id"], action))
        if self.error:
            raise self.error
        if action == "inspect":
            return copy.deepcopy(REPORT) | {"identity": machine.get("identity") or machine["host"]}
        if action == "cleanup":
            return {"state": "home_cleaned"}
        if action == "delete_archive":
            return {"state": "archive_deleted"}
        if action == "set_privilege":
            return {
                "state": "active",
                "uid": 1001,
                "home": "/home/" + data["username"],
                "is_admin": data["enabled"],
                "last_privilege_job": data["job_id"],
            }
        if action in ["restore", "reconcile", "adopt", "set_password"]:
            return {"state": "active", "uid": 1001, "home": "/home/" + data["username"]}
        if action == "create":
            return {"state": "active", "uid": 1001, "home": "/home/" + data["username"]}
        return {
            "state": "retired",
            "archive": "/safe/home.tar",
            "checksum": "test-digest",
            "size": 10240,
        }


@pytest.fixture
def app(tmp_path):
    config = Config(runtime_dir=tmp_path / "runtime")
    app = create_app(config, remote=FakeRemote())
    app.state.auth.init_admin("admin", ADMIN_PASSWORD)
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        login(client)
        yield client


def login(client, role="admin", username="admin", password=ADMIN_PASSWORD):
    data = client.get("/api/auth/me").json()
    client.headers["X-CSRF-Token"] = data["csrf"]
    response = client.post(
        "/api/auth/login",
        json={
            "role": role,
            "username": username,
            "password": password,
        },
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def person(app, name="张三", quota=None):
    return app.state.service.save_person({"name": name, "enabled": True, "quota": quota})


def machine(app, host="192.0.2.1", available=True):
    service = app.state.service
    machine_id = service.add_machine(
        {
            "name": host,
            "host": host,
            "port": 22,
            "username": "operator",
            "ssh_password": "example-ssh-credential",
            "sudo_password": "example-sudo-credential",
            "separate_sudo_password": True,
            "fingerprint": "SHA256:" + "A" * 43,
            "archive_root": "/var/lib/compute-control/archives",
            "available": available,
        }
    )
    return service.db.one("SELECT * FROM machines WHERE id=?", (machine_id,))


def apply(app, owner, target, username="researcher", key=None):
    return app.state.service.request_account(
        {"role": "applicant", "person_id": owner["id"]},
        target["id"],
        username,
        "example-user-password",
        key or new_id(),
    )


@pytest.fixture
def live_page(app):
    import socket
    import threading
    import time

    import httpx
    import uvicorn
    from playwright.sync_api import sync_playwright

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if httpx.get(address + "/healthz").status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.05)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(address)
            yield page
            browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
