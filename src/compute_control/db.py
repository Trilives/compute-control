"""Local SQLite persistence and transactional boundaries."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin (
 id INTEGER PRIMARY KEY CHECK(id=1), username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
 contact TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS applicants (
 id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 quota INTEGER CHECK(quota IS NULL OR quota>=0), deleted_at REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO settings VALUES ('default_quota', 3);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, role TEXT NOT NULL,
 person_id TEXT, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS attempts_time ON login_attempts(ts);
CREATE TABLE IF NOT EXISTS machines (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL,
 username TEXT NOT NULL, credentials TEXT NOT NULL, host_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, archive_root TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 0,
 identity TEXT, capabilities TEXT NOT NULL, error TEXT, sampled REAL,
 snapshot TEXT, log_sampled REAL, refresh_requested REAL, last_probe REAL, UNIQUE(host, port));
CREATE UNIQUE INDEX IF NOT EXISTS machine_identity ON machines(identity) WHERE identity IS NOT NULL;
CREATE TABLE IF NOT EXISTS machine_password_changes (
 machine_id TEXT PRIMARY KEY REFERENCES machines(id), id TEXT UNIQUE NOT NULL,
 credentials TEXT, state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
 id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES applicants(id),
 machine_id TEXT NOT NULL REFERENCES machines(id), username TEXT NOT NULL,
 state TEXT NOT NULL, occupies INTEGER NOT NULL DEFAULT 1, uid INTEGER, home TEXT,
 created REAL NOT NULL, credential_cipher TEXT, source TEXT NOT NULL DEFAULT 'created',
 is_admin INTEGER, privilege_sampled REAL,
 UNIQUE(machine_id, username));
CREATE INDEX IF NOT EXISTS person_machine_lookup ON accounts(person_id,machine_id);
CREATE TABLE IF NOT EXISTS machine_notes (
 id TEXT PRIMARY KEY, machine_id TEXT NOT NULL REFERENCES machines(id),
 author_key TEXT NOT NULL, author_name TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS machine_notes_recent ON machine_notes(machine_id,created DESC,id DESC);
CREATE TABLE IF NOT EXISTS account_ledger_dismissals (
 account_id TEXT NOT NULL REFERENCES accounts(id), viewer_key TEXT NOT NULL,
 cleared REAL NOT NULL, PRIMARY KEY(account_id,viewer_key));
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(id), machine_id TEXT NOT NULL,
 kind TEXT NOT NULL, state TEXT NOT NULL, step TEXT NOT NULL DEFAULT 'queued',
 error TEXT, created REAL NOT NULL, updated REAL NOT NULL, request_key TEXT UNIQUE NOT NULL,
 params TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS archives (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL, machine_id TEXT NOT NULL,
 path TEXT NOT NULL, checksum TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL,
 deleted REAL, UNIQUE(account_id,path));
CREATE TABLE IF NOT EXISTS viewer_leases (
 token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
 expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_events (
 machine_id TEXT NOT NULL, username TEXT NOT NULL, ts REAL NOT NULL,
 PRIMARY KEY(machine_id, username, ts));
CREATE TABLE IF NOT EXISTS announcements (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
 pinned INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, deleted REAL);
CREATE TABLE IF NOT EXISTS admin_contacts (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, contact TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS permission_requests (
 id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES applicants(id),
 account_id TEXT NOT NULL REFERENCES accounts(id), reason TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', response TEXT NOT NULL DEFAULT '',
 job_id TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS pending_permission ON permission_requests(account_id) WHERE state='pending';
CREATE TABLE IF NOT EXISTS machine_samples (
 machine_id TEXT NOT NULL REFERENCES machines(id), sampled REAL NOT NULL,
 snapshot TEXT NOT NULL, error TEXT, PRIMARY KEY(machine_id,sampled));
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
 target TEXT NOT NULL, result TEXT NOT NULL, ts REAL NOT NULL);
"""


class Database:
    def __init__(self, runtime):
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(runtime, 0o700)
        self.path = runtime / "control.sqlite3"
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 4:
                raise ValueError("数据库版本高于当前程序，拒绝降级")
            if version in (1, 2, 3):
                backups = runtime / "backups"
                backups.mkdir(mode=0o700, exist_ok=True)
                backup_path = backups / f"before-v4-from-v{version}-{time.time_ns()}.sqlite3"
                with sqlite3.connect(backup_path) as target:
                    conn.backup(target)
                os.chmod(backup_path, 0o600)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
        with self.connect(write=True) as conn:
            additions = {
                "machines": {"last_probe": "REAL"},
                "admin": {"contact": "TEXT NOT NULL DEFAULT ''"},
                "applicants": {"deleted_at": "REAL"},
                "accounts": {
                    "credential_cipher": "TEXT",
                    "is_admin": "INTEGER",
                    "privilege_sampled": "REAL",
                    "source": "TEXT NOT NULL DEFAULT 'created'",
                },
            }
            for table, columns in additions.items():
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for column, definition in columns.items():
                    if column not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            if version in (1, 2):
                for row in conn.execute(
                    "SELECT id,sampled,snapshot FROM machines WHERE sampled>? AND snapshot IS NOT NULL",
                    (time.time() - 86400,),
                ).fetchall():
                    snapshot = json.loads(row["snapshot"])
                    compact = {key: snapshot.get(key) for key in ["cpu_percent", "memory", "gpus"]}
                    conn.execute(
                        "INSERT OR IGNORE INTO machine_samples VALUES (?,?,?,NULL)",
                        (row["id"], row["sampled"], encode(compact)),
                    )
            conn.execute("DROP INDEX IF EXISTS person_machine")
            conn.execute("PRAGMA user_version=4")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self, write=False):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def audit(conn, actor, action, target, result="ok"):
        conn.execute(
            "INSERT INTO audit_logs(actor,action,target,result,ts) VALUES (?,?,?,?,?)",
            (actor, action, str(target), result, time.time()),
        )

    def all(self, sql, params=()):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
