"""Password hashing, opaque sessions, and authenticated machine credential encryption."""

import hashlib
import os
import secrets
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet

from .db import encode

HASHER = PasswordHasher()
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
LOGIN_WINDOW_SECONDS = 60
LOGIN_ATTEMPT_LIMIT = 100


class DomainError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def check_password(password_hash, password):
    try:
        return HASHER.verify(password_hash, password)
    except (VerificationError, ValueError):
        return False


def validate_password(password, minimum=12):
    if not minimum <= len(password) <= 256 or "\x00" in password or "\n" in password:
        raise DomainError(f"密码长度须为 {minimum}–256 个字符，不能包含换行或 NUL")


class Secrets:
    def __init__(self, runtime: Path):
        path = runtime / "master.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(Fernet.generate_key())
        if path.stat().st_mode & 0o077:
            raise ValueError("master.key 权限必须为 600")
        self.cipher = Fernet(path.read_bytes())

    def encrypt(self, data):
        return self.cipher.encrypt(encode(data).encode()).decode()

    def decrypt(self, token):
        import json

        return json.loads(self.cipher.decrypt(token.encode()))


class Auth:
    def __init__(self, db, config):
        self.db, self.config = db, config

    def init_admin(self, username, password):
        validate_password(password)
        if not username.strip() or len(username) > 64:
            raise DomainError("管理员用户名须为 1–64 个字符")
        with self.db.connect(write=True) as conn:
            if conn.execute("SELECT id FROM admin").fetchone():
                raise DomainError("管理员已初始化；使用 reset-admin 重设")
            conn.execute(
                "INSERT INTO admin(id,username,password_hash) VALUES (1,?,?)",
                (username.strip(), HASHER.hash(password)),
            )
            self.db.audit(conn, "local-cli", "admin.initialize", username.strip())

    def issue(self, role="anonymous", person_id=None, previous=None):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db.connect(write=True) as conn:
            conn.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            if previous:
                conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest(previous),))
            conn.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?)",
                (digest(token), csrf, role, person_id, time.time() + self.config.session_seconds),
            )
        return token, self.session(token)

    def session(self, token):
        if not token:
            return None
        session = self.db.one(
            "SELECT * FROM sessions WHERE token_hash=? AND expires>?", (digest(token), time.time())
        )
        if session and session["role"] == "applicant":
            person = self.db.one(
                "SELECT * FROM applicants WHERE id=? AND enabled=1", (session["person_id"],)
            )
            if not person:
                return None
            session["name"] = person["name"]
        return session

    def login(self, role, username, password, ip):
        now = time.time()
        ip = role + ":" + ip
        with self.db.connect(write=True) as conn:
            conn.execute("DELETE FROM login_attempts WHERE ts<=?", (now - LOGIN_WINDOW_SECONDS,))
            count = conn.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE ip=?", (ip,)
            ).fetchone()[0]
            if count >= LOGIN_ATTEMPT_LIMIT:
                raise DomainError("登录失败次数较多，请在一分钟后重试", 429)
            conn.execute("INSERT INTO login_attempts VALUES (?,?)", (ip, now))
        if role == "admin":
            admin = self.db.one("SELECT * FROM admin")
            valid = check_password(admin["password_hash"] if admin else DUMMY_HASH, password)
            if not valid or not admin or admin["username"] != username:
                raise DomainError("用户名或密码错误", 401)
            self.successful_attempt(ip, now)
            return None
        matches = self.db.all(
            "SELECT id,enabled FROM applicants WHERE name=? COLLATE NOCASE", (username.strip(),)
        )
        if len(matches) != 1 or not matches[0]["enabled"]:
            raise DomainError("姓名未登记或已停用，请联系管理员", 401)
        self.successful_attempt(ip, now)
        return matches[0]["id"]

    def successful_attempt(self, ip, timestamp):
        with self.db.connect(write=True) as conn:
            conn.execute("DELETE FROM login_attempts WHERE ip=? AND ts=?", (ip, timestamp))
