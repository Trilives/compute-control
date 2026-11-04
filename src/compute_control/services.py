"""Identity-independent business operations; no HTTP or SSH client types."""

import ipaddress
import json
import sqlite3
import time
import uuid
from pathlib import PurePosixPath

from .account_admin import AccountAdministration
from .db import encode
from .management_passwords import ManagementPasswords
from .portal import PortalOperations
from .security import DomainError, validate_password


def new_id():
    return uuid.uuid4().hex


def actor(session):
    return "admin" if session["role"] == "admin" else session["person_id"]


def capability_reasons(capabilities):
    if capabilities.get("can_create"):
        return []
    reasons = list(capabilities.get("block_reasons") or [])
    if not reasons:
        if capabilities.get("missing_tools"):
            reasons.append("缺少系统工具：" + ", ".join(capabilities["missing_tools"]))
        if not capabilities.get("local_accounts_only", False):
            reasons.append("账号来源需核查，请刷新机器能力报告")
        if not capabilities.get("gnu_tar", False):
            reasons.append("未检测到支持归档校验的 GNU tar")
    return reasons or ["能力报告未通过，请重新检测"]


class Service(AccountAdministration, PortalOperations, ManagementPasswords):
    def __init__(self, db, secrets, remote, config):
        self.db, self.secrets, self.remote, self.config = db, secrets, remote, config

    def people(self, include_deleted=False):
        return self.db.all(
            "SELECT p.*, COALESCE(p.quota,s.value) AS effective_quota, "
            "(SELECT COUNT(*) FROM accounts a WHERE a.person_id=p.id AND occupies=1) AS used "
            "FROM applicants p CROSS JOIN settings s WHERE s.key='default_quota' "
            + ("" if include_deleted else "AND p.deleted_at IS NULL ")
            + "ORDER BY p.name"
        )

    def person(self, person_id):
        return next((item for item in self.people() if item["id"] == person_id), None)

    def save_person(self, data, person_id=None):
        name = data["name"].strip()
        if not name or len(name) > 80:
            raise DomainError("姓名须为 1–80 个字符")
        person_id = person_id or new_id()
        try:
            with self.db.connect(write=True) as conn:
                matches = conn.execute(
                    "SELECT id,deleted_at FROM applicants WHERE name=? COLLATE NOCASE AND id<>?",
                    (name, person_id),
                ).fetchall()
                if matches:
                    editing = conn.execute(
                        "SELECT id FROM applicants WHERE id=?", (person_id,)
                    ).fetchone()
                    if len(matches) != 1 or matches[0]["deleted_at"] is None or editing:
                        raise DomainError("姓名已存在（英文字母不区分大小写），请区分同名人员", 409)
                    person_id = matches[0]["id"]
                conn.execute(
                    "INSERT INTO applicants(id,name,enabled,quota) VALUES (?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,"
                    "quota=excluded.quota,deleted_at=NULL",
                    (person_id, name, data["enabled"], data["quota"]),
                )
                if not data["enabled"]:
                    conn.execute("DELETE FROM sessions WHERE person_id=?", (person_id,))
                self.db.audit(conn, "admin", "applicant.save", person_id)
        except sqlite3.IntegrityError as exc:
            raise DomainError("姓名已存在，请区分同名人员", 409) from exc
        return self.person(person_id)

    def delete_person(self, person_id):
        with self.db.connect(write=True) as conn:
            person = conn.execute("SELECT * FROM applicants WHERE id=?", (person_id,)).fetchone()
            if not person:
                raise DomainError("人员不存在", 404)
            conn.execute(
                "UPDATE applicants SET enabled=0,deleted_at=COALESCE(deleted_at,?) WHERE id=?",
                (time.time(), person_id),
            )
            conn.execute("DELETE FROM sessions WHERE person_id=?", (person_id,))
            if person["deleted_at"] is None:
                self.db.audit(conn, "admin", "applicant.delete", person_id)
        return {"deleted": True}

    def visible_machines(self, session):
        sql = "SELECT * FROM machines"
        params = ()
        if session["role"] != "admin":
            sql += (
                " WHERE available=1 OR id IN "
                "(SELECT machine_id FROM accounts WHERE person_id=? AND occupies=1)"
            )
            params = (session["person_id"],)
        rows = self.db.all(sql + " ORDER BY name", params)
        pending = {
            item["machine_id"]
            for item in self.db.all(
                "SELECT machine_id FROM machine_password_changes WHERE state='pending'"
            )
        }
        for row in rows:
            if session["role"] == "admin":
                row["management_password_pending"] = row["id"] in pending
            for secret in ["credentials", "host_key"]:
                row.pop(secret)
            row["capabilities"] = json.loads(row["capabilities"])
            row["creation_blockers"] = capability_reasons(row["capabilities"])
            row["snapshot"] = json.loads(row["snapshot"]) if row["snapshot"] else None
            if row["snapshot"]:
                for field in ("users", "processes", "active_user_processes"):
                    row["snapshot"][field] = [
                        item
                        for item in row["snapshot"].get(field, [])
                        if item.get("username") != "root"
                    ]
                if "existing_usernames" in row["snapshot"]:
                    row["snapshot"]["existing_usernames"] = [
                        name for name in row["snapshot"]["existing_usernames"] if name != "root"
                    ]
                mappings = {
                    item["username"]: item["name"]
                    for item in self.db.all(
                        "SELECT a.username,p.name FROM accounts a JOIN applicants p ON p.id=a.person_id "
                        "WHERE a.machine_id=? AND a.occupies=1",
                        (row["id"],),
                    )
                }
                for user in row["snapshot"]["users"]:
                    machine_admin = user["username"] == row["username"]
                    user["is_machine_admin"] = machine_admin
                    user["registered"] = machine_admin or user["username"] in mappings
                    user["registration_source"] = (
                        "machine_admin"
                        if machine_admin
                        else "account"
                        if user["registered"]
                        else None
                    )
                    user["person_name"] = (
                        "机器管理员" if machine_admin else mappings.get(user["username"])
                    )
            row["latest_note"] = self.db.one(
                "SELECT author_name,content,created,(SELECT COUNT(*) FROM machine_notes WHERE machine_id=?) AS total FROM machine_notes WHERE machine_id=? ORDER BY created DESC,id DESC LIMIT 1",
                (row["id"], row["id"]),
            )
            row["stale"] = (
                not row["sampled"]
                or time.time() - row["sampled"] > self.config.snapshot_seconds * 2
            )
            row["active_users_30d"] = self.db.one(
                "SELECT COUNT(DISTINCT username) AS n FROM login_events WHERE machine_id=? AND ts>? AND username NOT IN (?, 'root')",
                (row["id"], time.time() - 30 * 86400, row["username"]),
            )["n"]
            if session["role"] != "admin":
                row.pop("archive_root")
                row.pop("username")
        return rows

    def add_machine(self, data):
        try:
            ipaddress.ip_address(data["host"])
        except ValueError as exc:
            raise DomainError("机器地址须为有效 IPv4 或 IPv6") from exc
        path = PurePosixPath(data["archive_root"])
        if not path.is_absolute() or ".." in path.parts or str(path) in ["/", "/home"]:
            raise DomainError("归档根目录必须为独立的绝对路径")
        credentials = self.secrets.encrypt(
            {key: data[key] for key in ["ssh_password", "sudo_password"]}
        )
        machine = {key: data[key] for key in ["name", "host", "port", "username", "archive_root"]}
        host = self.remote.identify(data["host"], data["port"])
        machine.update(
            id=new_id(),
            credentials=credentials,
            host_key=host["host_key"],
            fingerprint=host["fingerprint"],
        )
        report = self.remote.execute(
            machine, "inspect", {"include_logins": True, "include_tools": True}
        )
        available = bool(data["available"] and report["capabilities"]["can_create"])
        try:
            with self.db.connect(write=True) as conn:
                conn.execute(
                    "INSERT INTO machines(id,name,host,port,username,credentials,host_key,fingerprint,"
                    "archive_root,available,identity,capabilities) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        machine[key]
                        for key in [
                            "id",
                            "name",
                            "host",
                            "port",
                            "username",
                            "credentials",
                            "host_key",
                            "fingerprint",
                            "archive_root",
                        ]
                    )
                    + (available, report["identity"], encode(report["capabilities"])),
                )
                self.db.audit(conn, "admin", "machine.add", machine["id"])
        except sqlite3.IntegrityError as exc:
            raise DomainError("地址或机器身份已登记，不能重复添加", 409) from exc
        self.save_snapshot(machine["id"], report, True)
        return machine["id"]

    def save_snapshot(self, machine_id, report, logins):
        report = dict(report)
        machine = self.db.one("SELECT identity,snapshot FROM machines WHERE id=?", (machine_id,))
        if machine["identity"] != report["identity"]:
            raise DomainError("机器身份变化，请管理员核验")
        previous = json.loads(machine["snapshot"]) if machine["snapshot"] else {}
        if "system_tools" not in report and "system_tools" in previous:
            report["system_tools"] = previous["system_tools"]
            report["tools_sampled"] = previous.get("tools_sampled")
        before, after = previous.get("cpu_times"), report.get("cpu_times")
        measured_cpu = "cpu_percent" in report
        if not measured_cpu:
            report["cpu_percent"] = None
        if not measured_cpu and before and after:
            total = after["total"] - before["total"]
            idle = after["idle"] - before["idle"]
            if total > 0 and 0 <= idle <= total:
                report["cpu_percent"] = round(100 * (total - idle) / total, 1)
        events = report.pop("login_events", [])
        sampled = time.time()
        with self.db.connect(write=True) as conn:
            conn.execute(
                "UPDATE machines SET snapshot=?,sampled=?,last_probe=?,error=NULL,capabilities=? WHERE id=?",
                (
                    encode(report),
                    sampled,
                    sampled,
                    encode(report["capabilities"]),
                    machine_id,
                ),
            )
            compact = {key: report.get(key) for key in ["cpu_percent", "memory", "gpus"]}
            conn.execute(
                "INSERT INTO machine_samples VALUES (?,?,?,NULL)",
                (machine_id, sampled, encode(compact)),
            )
            conn.execute("DELETE FROM machine_samples WHERE sampled<?", (sampled - 86400,))
            for user in report.get("users", []):
                if "is_admin" in user:
                    conn.execute(
                        "UPDATE accounts SET is_admin=?,privilege_sampled=? WHERE machine_id=? AND username=? AND uid=?",
                        (user["is_admin"], sampled, machine_id, user["username"], user["uid"]),
                    )
            if report.get("boot_id"):
                reboots = conn.execute(
                    "SELECT * FROM jobs WHERE machine_id=? AND kind='reboot' AND step NOT IN ('boot_verified','admin_checked')",
                    (machine_id,),
                ).fetchall()
                for reboot in reboots:
                    previous_boot = json.loads(reboot["params"]).get("boot_id")
                    if previous_boot and previous_boot != report["boot_id"]:
                        conn.execute(
                            "UPDATE jobs SET step='boot_verified',state='succeeded',error=NULL WHERE id=?",
                            (reboot["id"],),
                        )
                        self.db.audit(conn, "worker", "machine.reboot.verified", machine_id)
            if logins:
                conn.execute(
                    "UPDATE machines SET log_sampled=? WHERE id=?", (time.time(), machine_id)
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO login_events VALUES (?,?,?)",
                    [(machine_id, item["username"], item["ts"]) for item in events],
                )
                conn.executemany(
                    "UPDATE accounts SET last_active=CASE WHEN last_active IS NULL OR last_active<? "
                    "THEN ? ELSE last_active END WHERE machine_id=? AND username=?",
                    [(event["ts"], event["ts"], machine_id, event["username"]) for event in events],
                )
                conn.execute("DELETE FROM login_events WHERE ts<?", (time.time() - 90 * 86400,))

    def accounts(self, session, include_cleared=False):
        sql = (
            "SELECT a.*,p.name,p.enabled AS owner_enabled,p.deleted_at AS owner_deleted_at,m.name AS machine_name,m.host,m.port,d.cleared AS ledger_cleared_at, "
            "(SELECT MAX(ts) FROM login_events e WHERE e.machine_id=a.machine_id "
            "AND e.username=a.username) AS observed_login FROM accounts a "
            "JOIN applicants p ON p.id=a.person_id JOIN machines m ON m.id=a.machine_id "
            "LEFT JOIN account_ledger_dismissals d ON d.account_id=a.id AND d.viewer_key=? WHERE a.username<>'root'"
        )
        params = (self.ledger_viewer(session),)
        if not include_cleared:
            sql += " AND (d.cleared IS NULL OR a.state<>'retired' OR a.occupies=1)"
        if session["role"] != "admin":
            sql += " AND a.person_id=?"
            params += (session["person_id"],)
        rows = self.db.all(sql + " ORDER BY a.created DESC", params)
        pending = {
            item["account_id"]
            for item in self.db.all(
                "SELECT DISTINCT account_id FROM jobs WHERE state IN ('queued','running','waiting_password')"
            )
        }
        for row in rows:
            row["can_clear_ledger"] = (
                row["state"] == "retired" and not row["occupies"] and row["id"] not in pending
            )
            observed = row.pop("observed_login")
            row["last_active"] = max(
                (ts for ts in (row["last_active"], observed) if ts is not None), default=None
            )
            row["password_available"] = bool(row.pop("credential_cipher", None))
        return rows

    def request_account(self, session, machine_id, username, password, request_key, person_id=None):
        validate_password(password, minimum=3)
        import re

        if (
            not re.fullmatch("[a-z][a-z0-9_-]{0,31}", username)
            or ":" in password
            or "\r" in password
        ):
            raise DomainError("用户名或密码不符合 Linux 账号规则")
        owner = person_id if session["role"] == "admin" else session["person_id"]
        with self.db.connect(write=True) as conn:
            previous = conn.execute(
                "SELECT * FROM jobs WHERE request_key=?", (request_key,)
            ).fetchone()
            if previous:
                account = conn.execute(
                    "SELECT person_id,machine_id,username FROM accounts WHERE id=?",
                    (previous["account_id"],),
                ).fetchone()
                if (
                    not account
                    or account["person_id"] != owner
                    or account["machine_id"] != machine_id
                    or account["username"] != username
                ):
                    raise DomainError("申请标识冲突", 409)
                return dict(previous)
            person = conn.execute(
                "SELECT * FROM applicants WHERE id=? AND enabled=1", (owner,)
            ).fetchone()
            machine = conn.execute(
                "SELECT * FROM machines WHERE id=? AND available=1", (machine_id,)
            ).fetchone()
            if not person or not machine:
                raise DomainError("名单无效或机器未开放申请", 403)
            self.guard_reboot(conn, machine_id)
            snapshot = json.loads(machine["snapshot"]) if machine["snapshot"] else {}
            if (
                username in {"root", "admin", "nobody", machine["username"]}
                or username in snapshot.get("existing_usernames", [])
                or any(user["username"] == username for user in snapshot.get("users", []))
            ):
                raise DomainError("该机器上用户名已存在，请更换用户名；不会覆盖原账号", 409)
            if conn.execute(
                "SELECT id FROM accounts WHERE machine_id=? AND username=?", (machine_id, username)
            ).fetchone():
                raise DomainError("该机器上用户名已有登记账号，请更换用户名", 409)
            used = conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE person_id=? AND occupies=1", (owner,)
            ).fetchone()[0]
            quota = person["quota"]
            if quota is None:
                quota = conn.execute(
                    "SELECT value FROM settings WHERE key='default_quota'"
                ).fetchone()[0]
            if used >= quota:
                raise DomainError("已达到账号上限，请联系管理员", 409)
            account_id, job_id, now = new_id(), new_id(), time.time()
            try:
                conn.execute(
                    "INSERT INTO accounts(id,person_id,machine_id,username,state,created,credential_cipher) VALUES (?,?,?,?,?,?,?)",
                    (
                        account_id,
                        owner,
                        machine_id,
                        username,
                        "creating",
                        now,
                        self.secrets.encrypt({"password": password}),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainError("该机器上用户名已有登记账号", 409) from exc
            conn.execute(
                "INSERT INTO jobs(id,account_id,machine_id,kind,state,created,updated,request_key) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, account_id, machine_id, "create", "queued", now, now, request_key),
            )
            self.db.audit(conn, actor(session), "account.request", account_id)
        return self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def authorize_account(self, session, account_id):
        account = self.db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
        if not account or (
            session["role"] != "admin" and account["person_id"] != session["person_id"]
        ):
            raise DomainError("账号不存在或无权访问", 404)
        return account

    def retire_account(self, session, account_id):
        self.authorize_account(session, account_id)
        with self.db.connect(write=True) as conn:
            account = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            self.guard_reboot(conn, account["machine_id"])
            if account["state"] not in ["active", "archive_failed", "needs_reconcile"]:
                raise DomainError("账号当前状态不能提交回收", 409)
            if conn.execute(
                "SELECT id FROM jobs WHERE account_id=? AND state IN ('queued','running')",
                (account_id,),
            ).fetchone():
                raise DomainError("账号已有待执行任务", 409)
            job_id, now = new_id(), time.time()
            conn.execute(
                "INSERT INTO jobs(id,account_id,machine_id,kind,state,created,updated,request_key) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, account_id, account["machine_id"], "retire", "queued", now, now, new_id()),
            )
            conn.execute("UPDATE accounts SET state='recycle_requested' WHERE id=?", (account_id,))
            self.db.audit(conn, actor(session), "account.retire", account_id)
        return self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def update_machine(self, machine_id, data):
        machine = self.db.one("SELECT * FROM machines WHERE id=?", (machine_id,))
        if not machine:
            raise DomainError("机器不存在", 404)
        try:
            ipaddress.ip_address(data["host"])
        except ValueError as exc:
            raise DomainError("须输入有效 IP") from exc
        root = PurePosixPath(data["archive_root"])
        if not root.is_absolute() or ".." in root.parts or str(root) in ["/", "/home"]:
            raise DomainError("归档根目录必须独立")
        revised = dict(
            machine,
            **{key: data[key] for key in ["host", "port", "username", "archive_root", "name"]},
        )
        revised["credentials"] = self.secrets.encrypt(
            {key: data[key] for key in ["ssh_password", "sudo_password"]}
        )
        report = self.remote.execute(
            revised, "inspect", {"include_logins": True, "include_tools": True}
        )
        if report["identity"] != machine["identity"]:
            raise DomainError("更新后的机器身份不符，不能替换原机器")
        with self.db.connect(write=True) as conn:
            self.guard_reboot(conn, machine_id)
            conn.execute(
                "UPDATE machines SET name=?,host=?,port=?,username=?,fingerprint=?,archive_root=?,credentials=?,available=?,capabilities=? WHERE id=?",
                tuple(
                    revised[key]
                    for key in [
                        "name",
                        "host",
                        "port",
                        "username",
                        "fingerprint",
                        "archive_root",
                        "credentials",
                    ]
                )
                + (
                    bool(data["available"] and report["capabilities"]["can_create"]),
                    encode(report["capabilities"]),
                    machine_id,
                ),
            )
            self.db.audit(conn, "admin", "machine.update", machine_id)
        self.save_snapshot(machine_id, report, True)
        return machine_id

    def admin_job(self, kind, account_id, params=None):
        account = self.authorize_account({"role": "admin"}, account_id)
        allowed = {
            "cleanup": ["home_cleanup_pending"],
            "restore": ["archive_failed", "needs_reconcile"],
            "reconcile": ["needs_reconcile", "archive_failed"],
            "delete_archive": ["retired"],
        }
        if kind not in allowed or account["state"] not in allowed[kind]:
            raise DomainError("账号当前状态不允许此操作", 409)
        now, job_id = time.time(), new_id()
        with self.db.connect(write=True) as conn:
            self.guard_reboot(conn, account["machine_id"])
            if conn.execute(
                "SELECT id FROM jobs WHERE account_id=? AND state IN ('queued','running')",
                (account_id,),
            ).fetchone():
                raise DomainError("已有待执行操作", 409)
            conn.execute(
                "INSERT INTO jobs(id,account_id,machine_id,kind,state,created,updated,request_key,params) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    account_id,
                    account["machine_id"],
                    kind,
                    "queued",
                    now,
                    now,
                    new_id(),
                    encode(params or {}),
                ),
            )
            self.db.audit(conn, "admin", "account." + kind, account_id)
        return self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
