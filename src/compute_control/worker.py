"""Single-host durable task worker and hourly background sampler."""

import json
import os
import socket
import socketserver
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .security import DomainError


def deliver_password(runtime, job_id, password):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            client.connect(str(runtime / "worker.sock"))
            client.sendall((json.dumps({"job_id": job_id, "password": password}) + "\n").encode())
            return client.recv(32) == b"OK\n"
    except OSError:
        return False


class Mailbox:
    def __init__(self, runtime, db):
        self.passwords, self.lock, self.db = {}, threading.Lock(), db
        mailbox = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(3)
                try:
                    data = json.loads(self.rfile.readline(4096))
                    job = db.one("SELECT * FROM jobs WHERE id=?", (data["job_id"],))
                    password = data["password"]
                    if (
                        not job
                        or job["kind"] not in ["create", "set_password"]
                        or job["state"] not in ["queued", "waiting_password"]
                    ):
                        return
                    if not isinstance(password, str) or not 3 <= len(password) <= 256:
                        return
                    with mailbox.lock:
                        mailbox.passwords[job["id"]] = (password, time.time())
                    self.wfile.write(b"OK\n")
                except (ValueError, KeyError, OSError):
                    return

        path = runtime / "worker.sock"
        if path.exists():
            path.unlink()
        self.server = socketserver.UnixStreamServer(str(path), Handler)
        os.chmod(path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def has(self, job_id):
        with self.lock:
            value = self.passwords.get(job_id)
            return value is not None and time.time() - value[1] < 180

    def take(self, job_id):
        with self.lock:
            now = time.time()
            self.passwords = {
                key: value for key, value in self.passwords.items() if now - value[1] < 180
            }
            result = self.passwords.pop(job_id, None)
        return result[0] if result else None

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Worker:
    def __init__(self, db, service, config, mailbox):
        self.db, self.service, self.config, self.mailbox = db, service, config, mailbox

    def recover(self):
        # A lost connection may leave a remote command alive. Do not rerun mutations blindly.
        with self.db.connect(write=True) as conn:
            conn.execute("DELETE FROM viewer_leases")
            accounts = conn.execute("SELECT account_id FROM jobs WHERE state='running'").fetchall()
            conn.execute(
                "UPDATE jobs SET state='needs_reconcile',error='worker 中断，需核查远端证据' WHERE state='running'"
            )
            for account in accounts:
                conn.execute(
                    "UPDATE accounts SET state='needs_reconcile' WHERE id=?",
                    (account["account_id"],),
                )

    def targets(self):
        return self.db.all("SELECT * FROM machines")

    def sample(self, machine):
        logins = (
            not machine["log_sampled"]
            or time.time() - machine["log_sampled"] >= self.config.login_seconds
        )
        try:
            previous = json.loads(machine["snapshot"]) if machine["snapshot"] else {}
            tools = (
                "system_tools" not in previous
                or time.time() - (previous.get("tools_sampled") or 0) >= 600
                or (machine.get("refresh_requested") or 0) > (machine.get("sampled") or 0)
            )
            report = self.service.remote.execute(
                machine, "inspect", {"include_logins": logins, "include_tools": tools}
            )
            self.service.save_snapshot(machine["id"], report, logins)
        except DomainError as exc:
            with self.db.connect(write=True) as conn:
                now = time.time()
                conn.execute(
                    "UPDATE machines SET error=?,last_probe=? WHERE id=?",
                    (exc.message, now, machine["id"]),
                )
                conn.execute(
                    "INSERT INTO machine_samples VALUES (?,?,?,?)",
                    (machine["id"], now, "{}", exc.message),
                )
                conn.execute("DELETE FROM machine_samples WHERE sampled<?", (now - 86400,))

    def process_reboot(self, job):
        machine = self.db.one("SELECT * FROM machines WHERE id=?", (job["machine_id"],))
        with self.db.connect(write=True) as conn:
            if not conn.execute(
                "UPDATE jobs SET state='running',step='remote' WHERE id=? AND state='queued'",
                (job["id"],),
            ).rowcount:
                return
        try:
            result = self.service.remote.execute(
                machine,
                "reboot",
                {"job_id": job["id"], "force": json.loads(job["params"]).get("force", False)},
            )
            if result.get("uncertain"):
                raise DomainError("重启安排结果不明，请核查原任务")
            with self.db.connect(write=True) as conn:
                conn.execute(
                    "UPDATE jobs SET state='succeeded',step='reboot_scheduled',params=?,error=?,updated=? WHERE id=?",
                    (
                        json.dumps({"boot_id": result["boot_id"]}),
                        "已发送立即重启指令；待机器恢复后核实",
                        time.time(),
                        job["id"],
                    ),
                )
                self.db.audit(conn, "worker", "machine.reboot.scheduled", machine["id"])
        except Exception:
            with self.db.connect(write=True) as conn:
                conn.execute(
                    "UPDATE jobs SET state='needs_reconcile',error=?,updated=? WHERE id=?",
                    ("重启安排结果未确认，禁止自动重复重启；请管理员核查", time.time(), job["id"]),
                )

    def process_job(self, job):
        if job["kind"] == "reboot":
            return self.process_reboot(job)
        account = self.db.one("SELECT * FROM accounts WHERE id=?", (job["account_id"],))
        machine = self.db.one("SELECT * FROM machines WHERE id=?", (job["machine_id"],))
        if self.db.one(
            "SELECT id FROM jobs WHERE machine_id=? AND kind='reboot' AND step NOT IN ('boot_verified','admin_checked')",
            (machine["id"],),
        ):
            return
        password = (
            self.mailbox.take(job["id"]) if job["kind"] in ["create", "set_password"] else None
        )
        if job["kind"] == "set_password" and not password:
            password = self.service.secrets.decrypt(json.loads(job["params"])["credential_cipher"])[
                "password"
            ]
        with self.db.connect(write=True) as conn:
            if job["kind"] == "create" and not machine["available"] and job["step"] == "queued":
                conn.execute(
                    "UPDATE jobs SET state='cancelled',updated=? WHERE id=?",
                    (time.time(), job["id"]),
                )
                conn.execute(
                    "UPDATE accounts SET occupies=0,state='cancelled' WHERE id=?", (account["id"],)
                )
                return
            if job["kind"] in ["create", "set_password"] and not password:
                conn.execute(
                    "UPDATE jobs SET state='waiting_password',updated=? WHERE id=?",
                    (time.time(), job["id"]),
                )
                conn.execute(
                    "UPDATE accounts SET state='waiting_password' WHERE id=?", (account["id"],)
                )
                return
            if conn.execute(
                "SELECT id FROM jobs WHERE machine_id=? AND kind='reboot' AND step NOT IN ('boot_verified','admin_checked')",
                (machine["id"],),
            ).fetchone():
                return
            claimed = conn.execute(
                "UPDATE jobs SET state='running',step='remote',updated=? WHERE id=? AND state IN ('queued','waiting_password')",
                (time.time(), job["id"]),
            ).rowcount
            if not claimed:
                return
            if password and job["kind"] == "create":
                conn.execute(
                    "UPDATE accounts SET credential_cipher=? WHERE id=?",
                    (self.service.secrets.encrypt({"password": password}), account["id"]),
                )
            if password and job["kind"] == "set_password":
                params = json.loads(job["params"])
                params["credential_cipher"] = self.service.secrets.encrypt({"password": password})
                job = dict(job, params=json.dumps(params))
                conn.execute("UPDATE jobs SET params=? WHERE id=?", (job["params"], job["id"]))
        try:
            result = self.service.remote.execute(
                machine,
                job["kind"],
                {
                    "account_id": account["id"],
                    "job_id": job["id"],
                    "username": account["username"],
                    "archive_root": machine["archive_root"],
                    "password": password,
                    **json.loads(job.get("params", "{}")),
                },
            )
            with self.db.connect(write=True) as conn:
                if result["state"] == "active":
                    cipher = (
                        self.service.secrets.encrypt({"password": password}) if password else None
                    )
                    if "is_admin" in result:
                        conn.execute(
                            "UPDATE accounts SET is_admin=?,privilege_sampled=? WHERE id=?",
                            (result["is_admin"], time.time(), account["id"]),
                        )
                    if result.get("last_privilege_job"):
                        original_permission = conn.execute(
                            "SELECT * FROM jobs WHERE id=? AND account_id=? AND kind='set_privilege'",
                            (result["last_privilege_job"], account["id"]),
                        ).fetchone()
                        if original_permission and result.get("is_admin") is not None:
                            matches = (
                                bool(result["is_admin"])
                                == json.loads(original_permission["params"])["enabled"]
                            )
                            conn.execute(
                                "UPDATE jobs SET state=?,step='verified',error=? WHERE id=?",
                                (
                                    "succeeded" if matches else "failed",
                                    None
                                    if matches
                                    else "已核对：当前权限与目标不同，可能有其他 sudoers 授权，请管理员处理",
                                    original_permission["id"],
                                ),
                            )
                    if result.get("last_password_job"):
                        original = conn.execute(
                            "SELECT * FROM jobs WHERE id=? AND account_id=? AND kind='set_password'",
                            (result["last_password_job"], account["id"]),
                        ).fetchone()
                        if original:
                            cipher = json.loads(original["params"]).get("credential_cipher")
                            conn.execute(
                                "UPDATE jobs SET state='succeeded',step='verified',error=NULL WHERE id=?",
                                (original["id"],),
                            )
                    conn.execute(
                        "UPDATE accounts SET state='active',uid=?,home=?,occupies=1,credential_cipher=COALESCE(?,credential_cipher) WHERE id=?",
                        (result["uid"], result["home"], cipher, account["id"]),
                    )
                elif result["state"] in ["retired", "cleaned"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO archives VALUES (?,?,?,?,?,?,?,NULL)",
                        (
                            job["id"],
                            account["id"],
                            machine["id"],
                            result["archive"],
                            result["checksum"],
                            result["size"],
                            time.time(),
                        ),
                    )
                    state = "retired" if result["state"] == "cleaned" else "home_cleanup_pending"
                    conn.execute(
                        "UPDATE accounts SET state=?,occupies=0 WHERE id=?", (state, account["id"])
                    )
                elif result["state"] == "not_created":
                    conn.execute(
                        "UPDATE accounts SET state='not_created',occupies=0 WHERE id=?",
                        (account["id"],),
                    )
                elif result["state"] == "home_cleaned":
                    conn.execute(
                        "UPDATE accounts SET state='retired',occupies=0 WHERE id=?",
                        (account["id"],),
                    )
                elif result["state"] == "archive_deleted":
                    params = json.loads(job["params"])
                    conn.execute(
                        "UPDATE archives SET deleted=? WHERE id=?",
                        (time.time(), params["archive_id"]),
                    )
                else:
                    raise DomainError("远端结果状态无法识别，请核查原任务")
                if (
                    job["kind"] == "set_privilege"
                    and result.get("is_admin") != json.loads(job["params"])["enabled"]
                ):
                    raise DomainError("权限变更尚未达到目标，请核对其他 sudoers 授权")
                conn.execute(
                    "UPDATE jobs SET state='succeeded',step='verified',error=NULL,updated=? WHERE id=?",
                    (time.time(), job["id"]),
                )
                self.db.audit(conn, "worker", job["kind"] + ".complete", account["id"])
        except Exception as exc:
            message = (
                exc.message if isinstance(exc, DomainError) else "后台执行异常，请核查远端证据"
            )
            with self.db.connect(write=True) as conn:
                state = "archive_failed" if job["kind"] == "retire" else "needs_reconcile"
                if job["kind"] not in [
                    "create",
                    "retire",
                    "adopt",
                    "set_password",
                    "set_privilege",
                ]:
                    state = account["state"]
                conn.execute("UPDATE accounts SET state=? WHERE id=?", (state, account["id"]))
                conn.execute(
                    "UPDATE jobs SET state='needs_reconcile',error=?,updated=? WHERE id=?",
                    (message, time.time(), job["id"]),
                )
                self.db.audit(conn, "worker", job["kind"] + ".failed", account["id"], state)
        finally:
            password = None

    def run(self, stop):
        self.recover()
        futures = {}
        forced = {}
        with (
            ThreadPoolExecutor(max_workers=self.config.ssh_concurrency) as pool,
            ThreadPoolExecutor(max_workers=1) as urgent,
        ):
            while not stop.is_set():
                for task_id, future in list(forced.items()):
                    if future.done():
                        try:
                            future.result()
                        except Exception:
                            with self.db.connect(write=True) as conn:
                                conn.execute(
                                    "UPDATE jobs SET state='needs_reconcile',error=? WHERE id=?",
                                    ("重启执行异常，请管理员核查，禁止自动重试", task_id),
                                )
                        finally:
                            del forced[task_id]
                for task in self.db.all(
                    "SELECT * FROM jobs WHERE kind='reboot' AND state='queued'"
                ):
                    if json.loads(task["params"]).get("force") and task["id"] not in forced:
                        forced[task["id"]] = urgent.submit(self.process_reboot, task)
                for machine_id, future in list(futures.items()):
                    if future.done():
                        # Unexpected failures remain visible and never silently release quota.
                        try:
                            future.result()
                        except Exception:
                            with self.db.connect(write=True) as conn:
                                conn.execute(
                                    "UPDATE machines SET error=? WHERE id=?",
                                    ("后台执行异常，请管理员检查任务状态", machine_id),
                                )
                        del futures[machine_id]
                for job in self.db.all(
                    "SELECT * FROM jobs WHERE state IN ('queued','waiting_password') ORDER BY created"
                ):
                    if len(futures) >= self.config.ssh_concurrency:
                        break
                    if job["kind"] == "reboot" and json.loads(job["params"]).get("force"):
                        continue
                    if job["state"] == "waiting_password" and not self.mailbox.has(job["id"]):
                        continue
                    if job["machine_id"] not in futures:
                        futures[job["machine_id"]] = pool.submit(self.process_job, job)
                for machine in self.targets():
                    if len(futures) >= self.config.ssh_concurrency:
                        break
                    previous = machine.get("last_probe") or machine["sampled"] or 0
                    due = not previous or time.time() - previous >= self.config.snapshot_seconds
                    refresh = (machine["refresh_requested"] or 0) > previous
                    if (due or refresh) and machine["id"] not in futures:
                        futures[machine["id"]] = pool.submit(self.sample, machine)
                stop.wait(1)
