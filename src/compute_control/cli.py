"""Local administrator bootstrap, web server, and dedicated task worker."""

import argparse
import fcntl
import getpass
import os
import signal
import threading
from dataclasses import replace

from .config import Config
from .db import Database
from .security import HASHER, Auth, DomainError, Secrets, validate_password
from .services import Service
from .ssh import SSH
from .worker import Mailbox, Worker


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="局域网算力控制服务")
    parser.add_argument("--config", help="TOML 配置路径")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-admin", help="初始化唯一管理员")
    init.add_argument("--username", default="admin")
    commands.add_parser("reset-admin", help="本机交互重设管理员密码并注销现有会话")
    commands.add_parser("worker", help="启动后台任务执行器")
    serve = commands.add_parser("serve", help="启动 Web 服务")
    serve.add_argument("--ssl-certfile")
    serve.add_argument("--ssl-keyfile")
    args = parser.parse_args()
    try:
        config = Config.load(args.config)
        db = Database(config.runtime_dir)
        auth = Auth(db, config)
        if args.command in ["init-admin", "reset-admin"]:
            password = getpass.getpass("管理员密码（至少 12 字符）: ")
            if password != getpass.getpass("再次输入: "):
                raise DomainError("两次输入不一致")
            if args.command == "init-admin":
                auth.init_admin(args.username, password)
            else:
                validate_password(password)
                with db.connect(write=True) as conn:
                    if not conn.execute("SELECT id FROM admin").fetchone():
                        raise DomainError("请先 init-admin")
                    conn.execute("UPDATE admin SET password_hash=?", (HASHER.hash(password),))
                    conn.execute("DELETE FROM sessions WHERE role='admin'")
                    db.audit(conn, "local-cli", "admin.password.reset", "admin")
            print("管理员设置完成。密码未写入日志或命令参数。")
        elif args.command == "serve":
            if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
                raise DomainError("TLS 证书和私钥必须同时提供")
            if config.https_enabled is False and args.ssl_certfile:
                raise DomainError("HTTPS 未启用；使用证书时请设置 https_enabled=true")
            if config.allow_insecure_http and (args.ssl_certfile or args.ssl_keyfile):
                raise DomainError("allow_insecure_http=true 时不能同时指定 TLS 证书")
            if config.secure_cookie and not (args.ssl_certfile and args.ssl_keyfile):
                raise DomainError(
                    "生产模式必须提供 TLS 证书，或明确设置 https_enabled=false 使用 HTTP"
                )
            if args.ssl_certfile:
                config = replace(config, https_enabled=True)
            import uvicorn

            from .app import create_app

            uvicorn.run(
                create_app(config),
                host=config.host,
                port=config.port,
                ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile,
                proxy_headers=False,
                access_log=False,
            )
        else:
            with (config.runtime_dir / "worker.lock").open("a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise DomainError("本机已有 worker，禁止双重执行") from exc
                vault = Secrets(config.runtime_dir)
                service = Service(db, vault, SSH(config, vault), config)
                stop = threading.Event()
                for sig in [signal.SIGINT, signal.SIGTERM]:
                    signal.signal(sig, lambda *_: stop.set())
                mailbox = Mailbox(config.runtime_dir, db)
                try:
                    Worker(db, service, config, mailbox).run(stop)
                finally:
                    mailbox.close()
    except (DomainError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
