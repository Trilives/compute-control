"""Announcements, contact directory and permission requests within console business rules."""

import json
import sqlite3
import time
import uuid

from .security import DomainError


class PortalOperations:
    def announcements(self, pinned=False):
        return self.db.all(
            "SELECT id,title,content,pinned,created FROM announcements WHERE deleted IS NULL"
            + (" AND pinned=1" if pinned else "")
            + " ORDER BY pinned DESC,created DESC"
        )

    def publish_announcement(self, data):
        if not data["title"].strip() or not data["content"].strip():
            raise DomainError("标题和正文不能为空")
        item_id = uuid.uuid4().hex
        with self.db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO announcements VALUES (?,?,?,?,?,NULL)",
                (item_id, data["title"].strip(), data["content"], data["pinned"], time.time()),
            )
            self.db.audit(conn, "admin", "announcement.publish", item_id)
        return {"id": item_id}

    def change_announcement(self, item_id, pinned=None, delete=False):
        with self.db.connect(write=True) as conn:
            if not conn.execute(
                "SELECT id FROM announcements WHERE id=? AND deleted IS NULL", (item_id,)
            ).fetchone():
                raise DomainError("公告不存在", 404)
            if delete:
                conn.execute(
                    "UPDATE announcements SET deleted=? WHERE id=?", (time.time(), item_id)
                )
            else:
                conn.execute("UPDATE announcements SET pinned=? WHERE id=?", (pinned, item_id))
            self.db.audit(
                conn, "admin", "announcement.delete" if delete else "announcement.pin", item_id
            )
        return {"updated": True}

    def admin_contacts(self):
        return self.db.all("SELECT * FROM admin_contacts ORDER BY created,id")

    def save_admin_contact(self, data, item_id=None):
        if not data["name"].strip() or not data["contact"].strip():
            raise DomainError("姓名和联系方式不能为空")
        with self.db.connect(write=True) as conn:
            if item_id:
                changed = conn.execute(
                    "UPDATE admin_contacts SET name=?,contact=? WHERE id=?",
                    (data["name"].strip(), data["contact"].strip(), item_id),
                ).rowcount
                if not changed:
                    raise DomainError("联系人不存在", 404)
            else:
                item_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO admin_contacts VALUES (?,?,?,?)",
                    (item_id, data["name"].strip(), data["contact"].strip(), time.time()),
                )
            self.db.audit(conn, "admin", "contact.save", item_id)
        return {"id": item_id}

    def delete_admin_contact(self, item_id):
        with self.db.connect(write=True) as conn:
            if not conn.execute("DELETE FROM admin_contacts WHERE id=?", (item_id,)).rowcount:
                raise DomainError("联系人不存在", 404)
            self.db.audit(conn, "admin", "contact.delete", item_id)
        return {"deleted": True}

    def permission_requests(self, session):
        return self.db.all(
            "SELECT r.*,p.name,a.username,m.name AS machine_name FROM permission_requests r "
            "JOIN applicants p ON p.id=r.person_id JOIN accounts a ON a.id=r.account_id "
            "JOIN machines m ON m.id=a.machine_id WHERE a.username<>'root'"
            + ("" if session["role"] == "admin" else " AND r.person_id=?")
            + " ORDER BY r.created DESC",
            () if session["role"] == "admin" else (session["person_id"],),
        )

    def request_permission(self, session, account_id, reason):
        account = self.authorize_account(session, account_id)
        if not reason.strip():
            raise DomainError("请简单说明申请事由")
        if account["state"] != "active" or not account["occupies"]:
            raise DomainError("请选择可使用的账号", 409)
        item_id, now = uuid.uuid4().hex, time.time()
        try:
            with self.db.connect(write=True) as conn:
                latest = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
                if (
                    latest["person_id"] != account["person_id"]
                    or latest["state"] != "active"
                    or not latest["occupies"]
                ):
                    raise DomainError("账号归属或状态已改变，请刷新", 409)
                conn.execute(
                    "INSERT INTO permission_requests(id,person_id,account_id,reason,created,updated) VALUES (?,?,?,?,?,?)",
                    (item_id, account["person_id"], account_id, reason.strip(), now, now),
                )
                self.db.audit(
                    conn,
                    (
                        "admin"
                        if session["role"] == "admin"
                        else "applicant:" + account["person_id"]
                    ),
                    "permission.request",
                    item_id,
                )
        except sqlite3.IntegrityError as exc:
            raise DomainError("该账号已有待处理的 sudo 权限申请", 409) from exc
        return {"id": item_id}

    def decide_permission(self, request_id, action, response):
        with self.db.connect(write=True) as conn:
            request = conn.execute(
                "SELECT * FROM permission_requests WHERE id=? AND state='pending'", (request_id,)
            ).fetchone()
            if not request:
                raise DomainError("申请不存在或已处理", 409)
            account = conn.execute(
                "SELECT * FROM accounts WHERE id=?", (request["account_id"],)
            ).fetchone()
            if action == "grant" and account["person_id"] != request["person_id"]:
                raise DomainError("账号已重新分配，请拒绝原申请后由新持有人提交", 409)
            job = self.queue_privilege(conn, account, True) if action == "grant" else None
            conn.execute(
                "UPDATE permission_requests SET state=?,response=?,job_id=?,updated=? WHERE id=?",
                (action, response, job["id"] if job else None, time.time(), request_id),
            )
            self.db.audit(conn, "admin", "permission." + action, request_id)
        return {"handled": True, "job_id": job["id"] if job else None}

    def queue_privilege(self, conn, account, enabled):
        if not account or account["state"] != "active" or not account["occupies"]:
            raise DomainError("只能调整可使用的普通账号权限", 409)
        self.guard_reboot(conn, account["machine_id"])
        if conn.execute(
            "SELECT id FROM jobs WHERE account_id=? AND state IN ('queued','running','waiting_password','needs_reconcile')",
            (account["id"],),
        ).fetchone():
            raise DomainError("账号有未完成或待核对任务", 409)
        job_id, now = uuid.uuid4().hex, time.time()
        conn.execute(
            "INSERT INTO jobs(id,account_id,machine_id,kind,state,created,updated,request_key,params) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                account["id"],
                account["machine_id"],
                "set_privilege",
                "queued",
                now,
                now,
                uuid.uuid4().hex,
                json.dumps({"enabled": enabled}),
            ),
        )
        self.db.audit(
            conn,
            "admin",
            "account.privilege.request",
            account["id"],
            json.dumps({"enabled": enabled}),
        )
        return {"id": job_id}

    def set_privilege(self, account_id, enabled):
        with self.db.connect(write=True) as conn:
            account = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            return self.queue_privilege(conn, account, enabled)

    def machine_history(self, session, machine_id):
        if not any(item["id"] == machine_id for item in self.visible_machines(session)):
            raise DomainError("机器不存在", 404)
        now = time.time()
        return {
            "window_seconds": 86400,
            "interval_seconds": self.config.snapshot_seconds,
            "from": now - 86400,
            "to": now,
            "samples": [
                dict(row, snapshot=json.loads(row["snapshot"]))
                for row in self.db.all(
                    "SELECT sampled,snapshot,error FROM machine_samples WHERE machine_id=? AND sampled>=? ORDER BY sampled",
                    (machine_id, now - 86400),
                )
            ],
        }

    def machine_notes(self, session, machine_id):
        if not any(item["id"] == machine_id for item in self.visible_machines(session)):
            raise DomainError("机器不存在", 404)
        notes = self.db.all(
            "SELECT id,author_name,content,created,(? OR author_key=?) AS can_delete "
            "FROM machine_notes WHERE machine_id=? ORDER BY created DESC,id DESC",
            (session["role"] == "admin", self.ledger_viewer(session), machine_id),
        )
        for note in notes:
            note["can_delete"] = bool(note["can_delete"])
        return notes

    def delete_machine_note(self, session, machine_id, note_id):
        viewer = self.ledger_viewer(session)
        with self.db.connect(write=True) as conn:
            sql = (
                "SELECT n.author_key FROM machine_notes n JOIN machines m ON m.id=n.machine_id "
                "WHERE n.id=? AND n.machine_id=?"
            )
            params = (note_id, machine_id)
            if session["role"] != "admin":
                sql += " AND (m.available=1 OR m.id IN (SELECT machine_id FROM accounts WHERE person_id=? AND occupies=1))"
                params += (session["person_id"],)
            note = conn.execute(sql, params).fetchone()
            if not note:
                raise DomainError("备注不存在", 404)
            if session["role"] != "admin" and note["author_key"] != viewer:
                raise DomainError("只能删除自己发布的备注", 403)
            conn.execute(
                "DELETE FROM machine_notes WHERE id=? AND machine_id=?", (note_id, machine_id)
            )
            self.db.audit(conn, viewer, "machine.note.delete", machine_id + ":" + note_id)
        return {"deleted": True}

    def add_machine_note(self, session, machine_id, content):
        if not content.strip():
            raise DomainError("请填写备注内容")
        note_id = uuid.uuid4().hex
        with self.db.connect(write=True) as conn:
            sql, params = "SELECT id FROM machines WHERE id=?", (machine_id,)
            if session["role"] != "admin":
                sql += " AND (available=1 OR id IN (SELECT machine_id FROM accounts WHERE person_id=? AND occupies=1))"
                params += (session["person_id"],)
            if not conn.execute(sql, params).fetchone():
                raise DomainError("机器不存在", 404)
            author = (
                "管理员"
                if session["role"] == "admin"
                else conn.execute(
                    "SELECT name FROM applicants WHERE id=?", (session["person_id"],)
                ).fetchone()["name"]
            )
            conn.execute(
                "INSERT INTO machine_notes VALUES (?,?,?,?,?,?)",
                (
                    note_id,
                    machine_id,
                    self.ledger_viewer(session),
                    author,
                    content.strip(),
                    time.time(),
                ),
            )
            self.db.audit(conn, self.ledger_viewer(session), "machine.note.add", machine_id)
        return {"id": note_id}
