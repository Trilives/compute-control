"""Transport protocol tests cover sudo prompts and NOPASSWD without any real SSH host."""

import json
from types import SimpleNamespace

import pytest

from compute_control.security import DomainError
from compute_control.ssh import SSH


class Channel:
    def __init__(self, prompt):
        self.prompt = prompt
        self.stderr = b"CC_SUDO_PASSWORD:" if prompt else b""
        self.stdout = b"" if prompt else b"CC_READY\n"
        self.sent = []
        self.finished = False

    def settimeout(self, value):
        pass

    def exec_command(self, command):
        assert "private-sudo-password" not in command
        assert "private-user-password" not in command

    def recv_ready(self):
        return bool(self.stdout)

    def recv_stderr_ready(self):
        return bool(self.stderr)

    def recv(self, size):
        value, self.stdout = self.stdout, b""
        return value

    def recv_stderr(self, size):
        value, self.stderr = self.stderr, b""
        return value

    def exit_status_ready(self):
        return self.finished

    def sendall(self, value):
        self.sent.append(value)
        if value == b"private-sudo-password\n":
            self.stdout = b"CC_READY\n"
        else:
            json.loads(value)
            self.stdout = json.dumps({"ok": True, "result": {"sampled": True}}).encode()
            self.finished = True

    def shutdown_write(self):
        pass


@pytest.mark.parametrize("prompt", [True, False])
def test_sudo_auth_is_separate_from_operation_input(monkeypatch, prompt):
    channel = Channel(prompt)

    class Client:
        def set_missing_host_key_policy(self, value):
            pass

        def connect(self, *args, **kwargs):
            assert kwargs["look_for_keys"] is False and kwargs["allow_agent"] is False

        def get_transport(self):
            return self

        def open_session(self, **kwargs):
            return channel

        def close(self):
            pass

    class Vault:
        def decrypt(self, value):
            return {
                "ssh_password": "private-ssh-password",
                "sudo_password": "private-sudo-password",
            }

    monkeypatch.setattr("compute_control.ssh.paramiko.SSHClient", Client)
    adapter = SSH(SimpleNamespace(ssh_timeout=1), Vault())
    result = adapter.execute(
        {
            "host": "192.0.2.1",
            "port": 22,
            "username": "operator",
            "credentials": "encrypted",
            "fingerprint": "expected",
        },
        "create",
        {"password": "private-user-password"},
    )
    assert result == {"sampled": True}
    expected = 2 if prompt else 1
    assert len(channel.sent) == expected
    assert json.loads(channel.sent[-1])["password"] == "private-user-password"


def test_diagnostics_do_not_disclose_transport_secrets(monkeypatch):
    class Vault:
        def decrypt(self, value):
            return {"ssh_password": "sensitive", "sudo_password": "sensitive"}

    def fail(*args, **kwargs):
        raise RuntimeError("sensitive")

    monkeypatch.setattr("compute_control.ssh.paramiko.SSHClient.connect", fail)
    with pytest.raises(DomainError) as error:
        SSH(SimpleNamespace(ssh_timeout=1), Vault()).execute(
            {
                "host": "192.0.2.1",
                "port": 22,
                "username": "operator",
                "credentials": "encrypted",
                "fingerprint": "expected",
            },
            "inspect",
        )
    assert "sensitive" not in error.value.message
