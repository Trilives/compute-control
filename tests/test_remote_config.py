import json
import subprocess
import sys

import paramiko
import pytest

from compute_control import remote
from compute_control.config import Config
from compute_control.security import DomainError
from compute_control.ssh import PinnedPolicy, fingerprint


def test_config_is_read_and_relative_paths_resolved(tmp_path):
    path = tmp_path / "custom.toml"
    path.write_text('port = 9876\nruntime_dir = "state"\nsnapshot_seconds = 240\n')
    config = Config.load(path)
    assert config.port == 9876 and config.snapshot_seconds == 240
    assert config.runtime_dir == tmp_path / "state"


@pytest.mark.parametrize(
    "text",
    [
        'mode="unknown"',
        'host="0.0.0.0"',
        "port=65536",
        "port=true",
        "snapshot_seconds=0",
        'mode="production"\nmisspelled=1',
        "lease_seconds=10",
    ],
)
def test_invalid_configuration_fails(tmp_path, text):
    path = tmp_path / "custom.toml"
    path.write_text(text)
    with pytest.raises(ValueError):
        Config.load(path)


def test_unknown_remote_action_cannot_run_shell():
    result = subprocess.run(
        [sys.executable, remote.__file__],
        input=json.dumps({"action": "arbitrary-command"}) + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    ready, response = result.stdout.splitlines()
    assert ready == "CC_READY"
    assert json.loads(response) == {"ok": False, "error": "未知操作"}


def test_manifest_includes_hidden_files_and_preserves_external_symlink(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".hidden").write_text("working data")
    (home / "external").symlink_to("/definitely-not-a-real-test-directory")
    data = remote.manifest(home)
    assert set(data) == {".", ".hidden", "external"}
    assert data["external"]["link"] == "/definitely-not-a-real-test-directory"
    assert "sha256" in data[".hidden"]


def test_existing_linux_user_is_never_modified(monkeypatch, tmp_path):
    monkeypatch.setattr(remote, "capabilities", lambda: {"can_create": True})
    monkeypatch.setattr(remote, "receipt_path", lambda _: tmp_path / "missing.json")
    monkeypatch.setattr(remote, "user_exists", lambda _: object())
    calls = []
    monkeypatch.setattr(remote, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(remote.Refuse, match="禁止覆盖"):
        remote.create(
            {
                "account_id": "a" * 32,
                "username": "researcher",
                "admin_username": "operator",
                "password": "example-password",
            }
        )
    assert calls == []


def test_pinned_host_key_mismatch_refuses_before_auth():
    key = paramiko.RSAKey.generate(1024)
    client = paramiko.SSHClient()
    policy = PinnedPolicy("SHA256:" + "A" * 43)
    with pytest.raises(DomainError, match="指纹"):
        policy.missing_host_key(client, "example", key)
    policy = PinnedPolicy(fingerprint(key))
    policy.missing_host_key(client, "example", key)
    assert client.get_host_keys().lookup("example") is not None


def test_app_reads_selected_config_and_production_cookies(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from compute_control.app import create_app

    path = tmp_path / "production.toml"
    path.write_text('mode="production"\nhost="0.0.0.0"\nport=9876\nruntime_dir="state"\n')
    monkeypatch.setenv("COMPUTE_CONTROL_CONFIG", str(path))
    application = create_app()
    assert application.state.config.port == 9876
    assert application.state.db.path.parent == tmp_path / "state"
    with TestClient(application, base_url="https://testserver") as client:
        response = client.get("/api/auth/me")
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert response.json()["role"] == "anonymous"
