"""Durable management credential rotation; SSH effects never run in a SQL transaction."""

import time
import uuid

from .security import DomainError, validate_password


def uses_separate_sudo(credentials):
    # Older records have no choice flag: preserve their working authentication setup.
    return credentials.get(
        "separate_sudo_password", credentials["sudo_password"] != credentials["ssh_password"]
    )


def machine_credentials(data):
    separate = data.get("separate_sudo_password", False)
    return {
        "ssh_password": data["ssh_password"],
        "sudo_password": data.get("sudo_password", "") if separate else data["ssh_password"],
        "separate_sudo_password": separate,
    }


class ManagementPasswords:
    def change_management_password(self, machine_id, password):
        validate_password(password, minimum=3)
        if any(char in password for char in "\r:"):
            raise DomainError("机器密码不能包含冒号或换行")
        machine = self.db.one("SELECT * FROM machines WHERE id=?", (machine_id,))
        if not machine:
            raise DomainError("机器不存在", 404)
        with self.db.connect() as conn:
            self.guard_reboot(conn, machine_id)
        try:
            report = self.remote.execute(machine, "inspect", {"include_logins": False})
        except Exception as exc:
            raise DomainError("当前 SSH / sudo 连接不可用，请先检查网络或更新凭据", 502) from exc
        if report["identity"] != machine["identity"]:
            raise DomainError("机器身份变化，禁止修改密码", 409)
        old = self.secrets.decrypt(machine["credentials"])
        if password == old["ssh_password"]:
            raise DomainError("新密码不能与当前机器密码相同")
        revised = dict(old, ssh_password=password, separate_sudo_password=uses_separate_sudo(old))
        if not revised["separate_sudo_password"]:
            revised["sudo_password"] = password
        encrypted = self.secrets.encrypt(revised)
        change_id, now = uuid.uuid4().hex, time.time()
        # Reserve before issuing SSH: a crash preserves both the old and candidate credentials.
        with self.db.connect(write=True) as conn:
            self.guard_reboot(conn, machine_id)
            if conn.execute(
                "SELECT id FROM jobs WHERE machine_id=? AND state IN ('queued','running','waiting_password')",
                (machine_id,),
            ).fetchone():
                raise DomainError("机器有未完成的平台任务，请完成后再修改管理密码", 409)
            current = conn.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone()
            if dict(current) != machine:
                raise DomainError("机器信息已更新，请刷新后重试", 409)
            conn.execute(
                "INSERT INTO machine_password_changes VALUES (?,?,?,'pending',?,?) "
                "ON CONFLICT(machine_id) DO UPDATE SET id=excluded.id,credentials=excluded.credentials,"
                "state='pending',created=excluded.created,updated=excluded.updated",
                (machine_id, change_id, encrypted, now, now),
            )
            self.db.audit(conn, "admin", "machine.password.request", machine_id)
        try:
            self.remote.execute(
                machine, "management_password", {"change_id": change_id, "password": password}
            )
        except Exception:
            # Neither raw diagnostics nor an exception prove whether chpasswd ran.
            pass
        return self.check_management_password(machine_id)

    def check_management_password(self, machine_id):
        machine = self.db.one("SELECT * FROM machines WHERE id=?", (machine_id,))
        if not machine:
            raise DomainError("机器不存在", 404)
        pending = self.db.one(
            "SELECT * FROM machine_password_changes WHERE machine_id=?", (machine_id,)
        )
        if not pending or pending["state"] != "pending":
            raise DomainError("该机器没有待核查的管理密码变更", 409)
        revised = dict(machine, credentials=pending["credentials"])
        state = "pending"
        try:
            report = self.remote.execute(revised, "inspect", {"include_logins": False})
            if report["identity"] == machine["identity"]:
                state = "verified"
        except Exception:
            pass
        if state == "pending":
            try:
                receipt = self.remote.execute(
                    machine,
                    "management_password_status",
                    {"change_id": pending["id"], "cancel_if_not_started": True},
                )
                if receipt["state"] in ["failed", "cancelled"]:
                    state = "failed"
            except Exception:
                pass
        if state != "pending":
            with self.db.connect(write=True) as conn:
                current = conn.execute(
                    "SELECT state FROM machine_password_changes WHERE machine_id=? AND id=?",
                    (machine_id, pending["id"]),
                ).fetchone()
                if not current or current["state"] != "pending":
                    raise DomainError("密码变更状态已更新，请刷新页面", 409)
                if state == "verified":
                    conn.execute(
                        "UPDATE machines SET credentials=?,refresh_requested=? WHERE id=?",
                        (pending["credentials"], time.time(), machine_id),
                    )
                conn.execute(
                    "UPDATE machine_password_changes SET state=?,credentials=NULL,updated=? WHERE machine_id=?",
                    (state, time.time(), machine_id),
                )
                self.db.audit(conn, "admin", "machine.password." + state, machine_id)
        messages = {
            "verified": "机器管理密码已修改，新的 SSH / sudo 连接已验证，系统凭据已同步。",
            "failed": "改密未完成，原连接凭据已保留；请检查连接和机器密码策略后重试。",
            "pending": "密码变更结果待核查，已加密保留新旧凭据。请使用核查入口，不要重复改密。",
        }
        return {"state": state, "message": messages[state]}
