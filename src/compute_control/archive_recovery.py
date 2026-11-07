"""Archive-specific recovery keeps plaintext passwords out of persisted jobs and responses."""

from .security import DomainError, validate_password


class ArchiveRecovery:
    def archive_records(self):
        rows = self.db.all(
            "SELECT r.*,a.username,a.state AS account_state,p.name,m.name AS machine_name,"
            "a.credential_cipher IS NOT NULL AS password_available "
            "FROM archives r JOIN accounts a ON a.id=r.account_id "
            "JOIN applicants p ON p.id=a.person_id JOIN machines m ON m.id=r.machine_id "
            "WHERE a.username<>'root' ORDER BY r.created DESC"
        )
        pending = {
            row["account_id"]
            for row in self.db.all(
                "SELECT account_id FROM jobs WHERE state IN ('queued','running','waiting_password')"
            )
        }
        for row in rows:
            row["password_available"] = bool(row["password_available"])
            row["can_restore"] = (
                not row["deleted"]
                and row["account_state"] in ["retired", "home_cleanup_pending"]
                and row["account_id"] not in pending
            )
        return rows

    def request_archive_restore(self, archive_id, password=None):
        archive = self.db.one(
            "SELECT * FROM archives WHERE id=? AND deleted IS NULL", (archive_id,)
        )
        if not archive:
            raise DomainError("归档不存在或已删除", 404)
        return self.admin_job(
            "restore", archive["account_id"], {"archive_id": archive_id, "password": password}
        )

    def prepare_archive_restore(self, conn, account, params):
        if not params or "archive_id" not in params:
            return params or {}
        archive = conn.execute(
            "SELECT * FROM archives WHERE id=? AND account_id=? AND machine_id=? AND deleted IS NULL",
            (params["archive_id"], account["id"], account["machine_id"]),
        ).fetchone()
        if not archive:
            raise DomainError("归档不存在或已删除", 404)
        password = params.get("password")
        cipher = account["credential_cipher"]
        if password is not None:
            validate_password(password, minimum=3)
            if any(char in password for char in ":\r"):
                raise DomainError("机器密码不能包含冒号或换行")
            cipher = self.secrets.encrypt({"password": password})
        if not cipher:
            raise DomainError("该旧记录没有留存密码，请输入恢复后的登录密码")
        return {
            "archive_id": archive["id"],
            "archive_path": archive["path"],
            "archive_checksum": archive["checksum"],
            "archive_size": archive["size"],
            "restore_credential_cipher": cipher,
        }
