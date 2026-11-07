"""Reviewed person removal atomically revokes access and schedules account retirement."""

import hashlib
import time
import uuid

from .db import encode
from .security import DomainError


class PeopleRetirement:
    def _person_removal_plan(self, conn, person_id):
        person = conn.execute("SELECT * FROM applicants WHERE id=?", (person_id,)).fetchone()
        if not person:
            raise DomainError("人员不存在", 404)
        accounts = [
            dict(row)
            for row in conn.execute(
                "SELECT a.id,a.machine_id,m.name AS machine_name,a.username,a.state,a.occupies,a.home "
                "FROM accounts a JOIN machines m ON m.id=a.machine_id WHERE a.person_id=? ORDER BY a.id",
                (person_id,),
            )
        ]
        jobs = [
            dict(row)
            for row in conn.execute(
                "SELECT j.id,j.account_id,j.kind,j.state,j.step FROM jobs j JOIN accounts a ON a.id=j.account_id "
                "WHERE a.person_id=? AND j.state IN ('queued','running','waiting_password') ORDER BY j.id",
                (person_id,),
            )
        ]
        token = hashlib.sha256(
            encode({"person": dict(person), "accounts": accounts, "jobs": jobs}).encode()
        ).hexdigest()
        blocked = []
        for account in accounts:
            pending = [job for job in jobs if job["account_id"] == account["id"]]
            if pending and all(job["kind"] == "retire" for job in pending):
                action = "continue"
            elif (
                pending
                and all(
                    job["kind"] == "create"
                    and job["step"] == "queued"
                    and job["state"] in ["queued", "waiting_password"]
                    for job in pending
                )
                and account["state"] in ["creating", "waiting_password"]
            ):
                action = "cancel_create"
            elif pending:
                action = "blocked"
            elif account["state"] in ["active", "suspended"]:
                action = "retire"
            elif account["state"] == "home_cleanup_pending":
                action = "cleanup"
            elif account["state"] in ["retired", "cancelled", "not_created"]:
                action = "keep_history"
            else:
                action = "blocked"
            if action in ["retire", "cleanup"]:
                try:
                    self.guard_reboot(conn, account["machine_id"])
                except DomainError:
                    action = "blocked"
            account["action"] = action
            if action == "blocked":
                blocked.append(
                    f"{account['machine_name']} / {account['username']}：请先核查账号或未完成任务"
                )
        return {
            "name": person["name"],
            "accounts": accounts,
            "review_token": token,
            "blocked": blocked,
        }, jobs

    def person_removal_plan(self, person_id):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            return self._person_removal_plan(conn, person_id)[0]

    def delete_person(self, person_id, review_token=None):
        now, scheduled = time.time(), 0
        with self.db.connect(write=True) as conn:
            plan, jobs = self._person_removal_plan(conn, person_id)
            if review_token is None and plan["accounts"]:
                raise DomainError("请先核对该人员全部账号并确认归档回收")
            if review_token is not None and review_token != plan["review_token"]:
                raise DomainError("人员或账号状态已变化，请重新打开核对弹窗", 409)
            if plan["blocked"]:
                raise DomainError("；".join(plan["blocked"]), 409)
            for account in plan["accounts"]:
                if account["action"] == "cancel_create":
                    for job in jobs:
                        if job["account_id"] == account["id"]:
                            conn.execute(
                                "UPDATE jobs SET state='cancelled',updated=? WHERE id=?",
                                (now, job["id"]),
                            )
                    conn.execute(
                        "UPDATE accounts SET state='cancelled',occupies=0 WHERE id=?",
                        (account["id"],),
                    )
                    self.db.audit(conn, "admin", "account.create.cancel", account["id"])
                elif account["action"] in ["retire", "cleanup"]:
                    kind, job_id = account["action"], uuid.uuid4().hex
                    conn.execute(
                        "INSERT INTO jobs(id,account_id,machine_id,kind,state,created,updated,request_key) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            job_id,
                            account["id"],
                            account["machine_id"],
                            kind,
                            "queued",
                            now,
                            now,
                            uuid.uuid4().hex,
                        ),
                    )
                    if kind == "retire":
                        conn.execute(
                            "UPDATE accounts SET state='recycle_requested' WHERE id=?",
                            (account["id"],),
                        )
                    scheduled += 1
                    self.db.audit(conn, "admin", "account." + kind, account["id"])
            changed = conn.execute(
                "UPDATE applicants SET enabled=0,deleted_at=? WHERE id=? AND deleted_at IS NULL",
                (now, person_id),
            ).rowcount
            conn.execute("DELETE FROM sessions WHERE person_id=?", (person_id,))
            if changed:
                self.db.audit(conn, "admin", "applicant.delete", person_id)
        return {"deleted": True, "retirement_jobs": scheduled}
