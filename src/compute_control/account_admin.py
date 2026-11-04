"""Administrative operations within the existing account lifecycle boundary."""

import json
import sqlite3
import time
import uuid

from .db import encode
from .security import DomainError, validate_password


class AccountAdministration:
    def adopt_account(self, data):
        machine = self.db.one("SELECT * FROM machines WHERE id=?", (data["machine_id"],))
        if not machine:
            raise DomainError("机器不存在", 404)
        snapshot = json.loads(machine["snapshot"]) if machine["snapshot"] else {}
        user = next(
            (item for item in snapshot.get("users", []) if item["username"] == data["username"]),
            None,
        )
        if (
            not user
            or user["uid"] < 1000
            or user["username"] in ["root", "admin", "nobody", machine["username"]]
        ):
            raise DomainError("只能显式接管快照中的普通本地用户，不能接管管理/系统账号")
        now, account_id, job_id = time.time(), uuid.uuid4().hex, uuid.uuid4().hex
        cipher = (
            self.secrets.encrypt({"password": data["password"]}) if data.get("password") else None
        )
        try:
            with self.db.connect(write=True) as conn:
                if not conn.execute(
                    "SELECT id FROM applicants WHERE id=?", (data["person_id"],)
                ).fetchone():
                    raise DomainError("请选择登记或历史姓名", 404)
                self.guard_reboot(conn, machine["id"])
                conn.execute(
                    "INSERT INTO accounts(id,person_id,machine_id,username,state,uid,home,created,credential_cipher,source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        account_id,
                        data["person_id"],
                        machine["id"],
                        user["username"],
                        "adopting",
                        user["uid"],
                        user["home"],
                        now,
                        cipher,
                        "imported",
                    ),
                )
                conn.execute(
                    "INSERT INTO jobs(id,account_id,machine_id,kind,state,created,updated,request_key,params) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        job_id,
                        account_id,
                        machine["id"],
                        "adopt",
                        "queued",
                        now,
                        now,
                        uuid.uuid4().hex,
                        encode({"uid": user["uid"], "home": user["home"]}),
                    ),
                )
                self.db.audit(conn, "admin", "account.adopt.request", account_id)
        except sqlite3.IntegrityError as exc:
            raise DomainError("该机器上用户名已有台账", 409) from exc
        return self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def assign_account(self, account_id, person_id):
        with self.db.connect(write=True) as conn:
            account = conn.execute(
                "SELECT * FROM accounts WHERE id=? AND occupies=1", (account_id,)
            ).fetchone()
            if not account:
                raise DomainError("有效账号不存在", 404)
            self.guard_reboot(conn, account["machine_id"])
            target = conn.execute(
                "SELECT * FROM applicants WHERE id=? AND enabled=1 AND deleted_at IS NULL",
                (person_id,),
            ).fetchone()
            if not target:
                raise DomainError("接收人须为已启用的登记人员", 400)
            if account["person_id"] == person_id:
                raise DomainError("请选择不同的接收人")
            if conn.execute(
                "SELECT id FROM jobs WHERE account_id=? AND state IN ('queued','running','waiting_password')",
                (account_id,),
            ).fetchone():
                raise DomainError("账号有未完成任务，请先处理", 409)
            used = conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE person_id=? AND occupies=1", (person_id,)
            ).fetchone()[0]
            quota = target["quota"]
            if quota is None:
                quota = conn.execute(
                    "SELECT value FROM settings WHERE key='default_quota'"
                ).fetchone()[0]
            if used >= quota:
                raise DomainError("接收人的额度不足，请先调整额度", 409)
            conn.execute("UPDATE accounts SET person_id=? WHERE id=?", (person_id, account_id))
            self.db.audit(
                conn,
                "admin",
                "account.assign",
                account_id,
                encode({"from": account["person_id"], "to": person_id}),
            )
        return {"assigned": True}

    def reveal_password(self, account_id):
        with self.db.connect(write=True) as conn:
            row = conn.execute(
                "SELECT a.*,p.enabled,p.deleted_at FROM accounts a JOIN applicants p ON p.id=a.person_id WHERE a.id=?",
                (account_id,),
            ).fetchone()
            if not row:
                raise DomainError("账号不存在", 404)
            if row["enabled"] and row["deleted_at"] is None:
                raise DomainError("仅可查看已停用或已移出名单人员的留存密码", 403)
            if conn.execute(
                "SELECT id FROM jobs WHERE account_id=? AND kind='set_password' AND state<>'succeeded'",
                (account_id,),
            ).fetchone():
                raise DomainError("密码变更结果尚未确认，请先核查任务", 409)
            if not row["credential_cipher"]:
                raise DomainError(
                    "原密码从未留存，无法从 Linux 哈希还原；请录入已知密码或重设", 409
                )
            value = self.secrets.decrypt(row["credential_cipher"])["password"]
            self.db.audit(conn, "admin", "account.password.reveal", account_id)
        return {"password": value}

    def remember_password(self, account_id, password):
        self.authorize_account({"role": "admin"}, account_id)
        if not password or len(password) > 256 or "\x00" in password:
            raise DomainError("请输入已知密码，最长 256 字符")
        with self.db.connect(write=True) as conn:
            conn.execute(
                "UPDATE accounts SET credential_cipher=? WHERE id=?",
                (self.secrets.encrypt({"password": password}), account_id),
            )
            self.db.audit(conn, "admin", "account.password.record", account_id)
        return {"recorded": True, "verified": False}

    def request_password_change(self, account_id, password):
        account = self.authorize_account({"role": "admin"}, account_id)
        validate_password(password, minimum=3)
        if ":" in password or "\r" in password:
            raise DomainError("机器密码不能包含冒号或换行")
        if not account["occupies"]:
            raise DomainError("账号已回收，不能重设密码", 409)
        job_id, now = uuid.uuid4().hex, time.time()
        cipher = self.secrets.encrypt({"password": password})
        with self.db.connect(write=True) as conn:
            self.guard_reboot(conn, account["machine_id"])
            if conn.execute(
                "SELECT id FROM jobs WHERE account_id=? AND state IN ('queued','running','waiting_password')",
                (account_id,),
            ).fetchone():
                raise DomainError("账号有未完成任务", 409)
            conn.execute(
                "INSERT INTO jobs(id,account_id,machine_id,kind,state,created,updated,request_key,params) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    account_id,
                    account["machine_id"],
                    "set_password",
                    "queued",
                    now,
                    now,
                    uuid.uuid4().hex,
                    encode({"credential_cipher": cipher}),
                ),
            )
            self.db.audit(conn, "admin", "account.password.reset.request", account_id)
        return self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def guard_reboot(self, conn, machine_id):
        if conn.execute(
            "SELECT id FROM jobs WHERE machine_id=? AND kind='reboot' AND step NOT IN ('boot_verified','admin_checked')",
            (machine_id,),
        ).fetchone():
            raise DomainError("机器重启结果尚未核实，暂不执行账号操作", 409)

    def reboot_precheck(self, machine_id):
        machine = self.db.one("SELECT * FROM machines WHERE id=?", (machine_id,))
        if not machine:
            raise DomainError("机器不存在", 404)
        report = self.remote.execute(machine, "inspect", {"include_logins": False})
        self.save_snapshot(machine_id, report, False)
        jobs = self.db.all(
            "SELECT id,kind,state FROM jobs WHERE machine_id=? AND kind<>'reboot' AND state IN ('queued','running')",
            (machine_id,),
        )
        processes = report.get("active_user_processes", [])
        gpu = report.get("processes", [])
        return {
            "busy": bool(jobs or processes or gpu or report.get("activity_unknown")),
            "platform_jobs": jobs,
            "user_processes": processes,
            "gpu_process_count": len(gpu),
            "activity_unknown": report.get("activity_unknown", False),
        }

    def request_reboot(self, machine_id, force=False, force_confirmed=False):
        check = self.reboot_precheck(machine_id)
        if (check["busy"] or force) and not (force and force_confirmed):
            raise DomainError("检测到活动任务，需要管理员第三次确认后强制重启", 409)
        now, job_id = time.time(), uuid.uuid4().hex
        with self.db.connect(write=True) as conn:
            machine = conn.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone()
            if not machine:
                raise DomainError("机器不存在", 404)
            self.guard_reboot(conn, machine_id)
            capabilities = json.loads(machine["capabilities"])
            if not capabilities.get("can_reboot"):
                raise DomainError("未检测到系统 shutdown 命令，请刷新机器状态或由管理员处理")
            if (
                not force
                and conn.execute(
                    "SELECT id FROM jobs WHERE machine_id=? AND state IN ('queued','running')",
                    (machine_id,),
                ).fetchone()
            ):
                raise DomainError("出现新的平台任务，需要第三次确认强制重启", 409)
            snapshot = json.loads(machine["snapshot"]) if machine["snapshot"] else {}
            conn.execute(
                "INSERT INTO jobs(id,machine_id,kind,state,created,updated,request_key,params) VALUES (?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    machine_id,
                    "reboot",
                    "queued",
                    now,
                    now,
                    uuid.uuid4().hex,
                    encode({"boot_id": snapshot.get("boot_id"), "force": force}),
                ),
            )
            conn.execute("UPDATE machines SET available=0 WHERE id=?", (machine_id,))
            self.db.audit(
                conn,
                "admin",
                "machine.reboot.request",
                machine_id,
                encode({"force": force, "third_confirmation": force_confirmed}),
            )
        return self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def check_reboot(self, machine_id):
        machine = self.db.one("SELECT * FROM machines WHERE id=?", (machine_id,))
        if not machine:
            raise DomainError("机器不存在", 404)
        job = self.db.one(
            "SELECT * FROM jobs WHERE machine_id=? AND kind='reboot' AND step NOT IN ('boot_verified','admin_checked') ORDER BY created DESC LIMIT 1",
            (machine_id,),
        )
        if not job:
            return {"checked": True}
        if time.time() - job["created"] < 75:
            raise DomainError("重启仍在安排或执行中，请稍后核查", 409)
        report = self.remote.execute(machine, "inspect", {"include_logins": False})
        self.save_snapshot(machine_id, report, False)
        with self.db.connect(write=True) as conn:
            conn.execute(
                "UPDATE jobs SET step='admin_checked',state='succeeded',error=NULL WHERE id=?",
                (job["id"],),
            )
            self.db.audit(conn, "admin", "machine.reboot.checked", machine_id)
        return {"checked": True}

    @staticmethod
    def ledger_viewer(session):
        return "admin" if session["role"] == "admin" else "applicant:" + session["person_id"]

    def set_ledger_cleared(self, session, account_id, cleared):
        viewer = self.ledger_viewer(session)
        with self.db.connect(write=True) as conn:
            account = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not account or (
                session["role"] != "admin" and account["person_id"] != session["person_id"]
            ):
                raise DomainError("账号不存在", 404)
            if cleared:
                if account["state"] != "retired" or account["occupies"]:
                    raise DomainError("仅可清理已完成回收和原主目录清理的账号", 409)
                if conn.execute(
                    "SELECT id FROM jobs WHERE account_id=? AND state IN ('queued','running','waiting_password')",
                    (account_id,),
                ).fetchone():
                    raise DomainError("账号仍有未完成任务，请处理完成后再清理台账", 409)
                changed = conn.execute(
                    "INSERT OR IGNORE INTO account_ledger_dismissals VALUES (?,?,?)",
                    (account_id, viewer, time.time()),
                ).rowcount
            else:
                changed = conn.execute(
                    "DELETE FROM account_ledger_dismissals WHERE account_id=? AND viewer_key=?",
                    (account_id, viewer),
                ).rowcount
            if changed:
                self.db.audit(
                    conn,
                    viewer,
                    "account.ledger.clear" if cleared else "account.ledger.restore",
                    account_id,
                )
        return {"cleared": cleared}
