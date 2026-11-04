"""Pinned-host SSH transport. Sudo authentication and operation input are separated."""

import base64
import hashlib
import json
import shlex
import socket
import time
from pathlib import Path

import paramiko

from .security import DomainError


class RemoteError(DomainError):
    def __init__(self, message):
        super().__init__(message, 502)


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def probe(host, port, timeout):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with paramiko.Transport(sock) as transport:
            transport.banner_timeout = timeout
            transport.start_client(timeout=timeout)
            key = transport.get_remote_server_key()
            return {
                "fingerprint": fingerprint(key),
                "host_key": key.get_name() + " " + key.get_base64(),
            }


class PinnedPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        if fingerprint(key) != self.expected:
            raise RemoteError("SSH 主机指纹变化或不匹配，连接已停止，请管理员核验")
        client.get_host_keys().add(hostname, key.get_name(), key)


class SSH:
    def __init__(self, config, secrets):
        self.config, self.secrets = config, secrets
        self.source = Path(__file__).with_name("remote.py").read_text()
        self.tool_checks = json.loads(Path(__file__).with_name("tool_policy.json").read_text())[
            "checks"
        ]

    def identify(self, host, port):
        try:
            return probe(host, port, self.config.ssh_timeout)
        except Exception as exc:
            raise RemoteError("无法读取 SSH 主机信息，请检查 IP、端口和网络") from exc

    def execute(self, machine, action, data=None):
        credentials = self.secrets.decrypt(machine["credentials"])
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedPolicy(machine["fingerprint"]))
        try:
            client.connect(
                machine["host"],
                port=machine["port"],
                username=machine["username"],
                password=credentials["ssh_password"],
                timeout=self.config.ssh_timeout,
                auth_timeout=self.config.ssh_timeout,
                banner_timeout=self.config.ssh_timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            channel = client.get_transport().open_session(timeout=self.config.ssh_timeout)
            channel.settimeout(self.config.ssh_timeout)
            marker = "CC_SUDO_PASSWORD:"
            command = (
                "sudo -S -p "
                + shlex.quote(marker)
                + " -- python3 -u -c "
                + shlex.quote(self.source)
            )
            channel.exec_command(command)
            output, errors, authenticated = b"", b"", False
            deadline = time.monotonic() + self.config.ssh_timeout
            while b"CC_READY\n" not in output:
                if time.monotonic() > deadline or channel.exit_status_ready():
                    raise RemoteError("sudo/Python 启动失败或超时，请检查管理权限")
                if channel.recv_stderr_ready():
                    errors += channel.recv_stderr(4096)
                    if marker.encode() in errors:
                        if authenticated:
                            raise RemoteError("sudo 密码验证失败")
                        channel.sendall(credentials["sudo_password"].encode() + b"\n")
                        authenticated = True
                        errors = b""
                if channel.recv_ready():
                    output += channel.recv(4096)
                time.sleep(0.01)
            payload = dict(
                data or {},
                action=action,
                admin_username=machine["username"],
                expected_identity=machine.get("identity"),
            )
            if action == "inspect" and payload.get("include_tools"):
                payload["tool_checks"] = self.tool_checks
            channel.sendall((json.dumps(payload) + "\n").encode())
            channel.shutdown_write()
            output = output.split(b"CC_READY\n", 1)[1]
            deadline = time.monotonic() + (
                7500
                if action in ["retire", "cleanup", "delete_archive"]
                else self.config.ssh_timeout * 4
            )
            while True:
                if time.monotonic() > deadline:
                    raise RemoteError("操作超时，远端可能仍在执行，请核对原任务")
                if channel.recv_ready():
                    output += channel.recv(65536)
                if channel.recv_stderr_ready():
                    channel.recv_stderr(
                        65536
                    )  # Raw diagnostics can contain credentials: never log.
                if len(output) > 32 * 1024 * 1024:
                    raise RemoteError("远端结果过大，请管理员核查")
                if channel.exit_status_ready() and not channel.recv_ready():
                    break
                time.sleep(0.02)
            result = json.loads(output)
            if not result.get("ok"):
                raise RemoteError(result.get("error", "远端操作失败"))
            return result["result"]
        except DomainError:
            raise
        except Exception as exc:
            raise RemoteError("SSH 连接或结果异常，账号操作请核查原任务，勿重复执行") from exc
        finally:
            client.close()
