"""HTTP composition and authorization; business operations live in services."""

import ipaddress
import secrets as random
import sqlite3
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Config
from .db import Database
from .models import (
    AccountRequest,
    Adoption,
    Assignment,
    Available,
    Confirm,
    Contact,
    Login,
    Machine,
    Password,
    Person,
    Quota,
    RebootRequest,
    Target,
)
from .portal_api import register_portal
from .security import Auth, DomainError, Secrets, validate_password
from .services import Service, actor, capability_reasons
from .ssh import SSH, probe
from .worker import deliver_password

PACKAGE = Path(__file__).parent


def create_app(config=None, remote=None):
    config = config or Config.load()
    cookie_name = (
        "cc_session_http"
        if config.mode == "production" and config.allow_insecure_http
        else "cc_session"
    )
    db = Database(config.runtime_dir)
    vault = Secrets(config.runtime_dir)
    auth = Auth(db, config)
    service = Service(db, vault, remote or SSH(config, vault), config)
    app = FastAPI(title="算力控制台", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.auth, app.state.service = db, auth, service
    app.state.config = config
    templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors include input values, including raw passwords. Return only field names.
        fields = ", ".join(".".join(str(value) for value in item["loc"]) for item in exc.errors())
        return JSONResponse({"detail": "输入格式不正确: " + fields}, status_code=422)

    @app.middleware("http")
    async def security_headers(request, call_next):
        if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({"detail": "请求过大"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        return response

    def session(request: Request):
        value = auth.session(request.cookies.get(cookie_name))
        if not value:
            raise DomainError("会话已过期，请重新进入", 401)
        if request.method != "GET":
            token = request.headers.get("X-CSRF-Token", "")
            if not random.compare_digest(token, value["csrf"]):
                raise DomainError("请求校验失败，请刷新页面", 403)
        return value

    def member(value=Depends(session)):
        if value["role"] == "anonymous":
            raise DomainError("请先进入具体模式", 401)
        return value

    def admin(value=Depends(member)):
        if value["role"] != "admin":
            raise DomainError("需要管理员权限", 403)
        return value

    def attach_cookie(response, token):
        response.set_cookie(
            cookie_name,
            token,
            max_age=config.session_seconds,
            httponly=True,
            secure=config.secure_cookie,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={})

    @app.get("/healthz")
    def health():
        return {"status": "ok", "initialized": db.one("SELECT id FROM admin") is not None}

    @app.get("/api/auth/me")
    def me(request: Request):
        value = auth.session(request.cookies.get(cookie_name))
        token = None
        if not value:
            token, value = auth.issue(previous=request.cookies.get(cookie_name))
        data = {key: value.get(key) for key in ["role", "person_id", "csrf", "name"]}
        data["initialized"] = db.one("SELECT id FROM admin") is not None
        if value["role"] == "applicant":
            data["person"] = service.person(value["person_id"])
            data["admin_contact"] = db.one("SELECT contact FROM admin")["contact"]
        response = JSONResponse(data)
        return attach_cookie(response, token) if token else response

    @app.post("/api/auth/login")
    def login(data: Login, request: Request, current=Depends(session)):
        try:
            person_id = auth.login(
                data.role, data.username, data.password.get_secret_value(), request.client.host
            )
        except DomainError as exc:
            if data.role != "applicant" or exc.status != 401:
                raise
            record = db.one("SELECT contact FROM admin")
            return JSONResponse(
                {
                    "detail": exc.message,
                    "admin_contact": record["contact"] if record else "",
                    "admin_contacts": [
                        {"name": item["name"], "contact": item["contact"]}
                        for item in service.admin_contacts()
                    ],
                },
                status_code=401,
            )
        token, value = auth.issue(data.role, person_id, request.cookies.get(cookie_name))
        with db.connect(write=True) as conn:
            db.audit(conn, actor(value), "session.login", data.role)
        return attach_cookie(JSONResponse({"role": data.role, "csrf": value["csrf"]}), token)

    @app.post("/api/auth/logout")
    def logout(request: Request, current=Depends(session)):
        token, value = auth.issue(previous=request.cookies.get(cookie_name))
        return attach_cookie(JSONResponse({"role": "anonymous", "csrf": value["csrf"]}), token)

    @app.get("/api/admin/applicants")
    def applicants(include_deleted: bool = False, current=Depends(admin)):
        return service.people(include_deleted)

    @app.post("/api/admin/applicants", status_code=201)
    def add_person(data: Person, current=Depends(admin)):
        return service.save_person(data.model_dump())

    @app.put("/api/admin/applicants/{person_id}")
    def edit_person(person_id: str, data: Person, current=Depends(admin)):
        if not service.person(person_id):
            raise DomainError("人员不存在", 404)
        return service.save_person(data.model_dump(), person_id)

    @app.delete("/api/admin/applicants/{person_id}")
    def delete_person(person_id: str, current=Depends(admin)):
        return service.delete_person(person_id)

    @app.get("/api/admin/settings")
    def settings(current=Depends(admin)):
        return {
            "default_quota": db.one("SELECT value FROM settings WHERE key='default_quota'")[
                "value"
            ],
            "admin_contact": db.one("SELECT contact FROM admin")["contact"],
        }

    @app.put("/api/admin/settings/quota")
    def set_quota(data: Quota, current=Depends(admin)):
        with db.connect(write=True) as conn:
            conn.execute("UPDATE settings SET value=? WHERE key='default_quota'", (data.value,))
            db.audit(conn, "admin", "quota.default", data.value)
        return {"default_quota": data.value}

    @app.put("/api/admin/settings/contact")
    def contact(data: Contact, current=Depends(admin)):
        with db.connect(write=True) as conn:
            conn.execute("UPDATE admin SET contact=? WHERE id=1", (data.text,))
            db.audit(conn, "admin", "admin.contact.update", "admin")
        return {"admin_contact": data.text}

    @app.post("/api/admin/accounts/adopt", status_code=202)
    def adopt(data: Adoption, current=Depends(admin)):
        values = data.model_dump()
        values["password"] = data.password.get_secret_value() if data.password else None
        job = service.adopt_account(values)
        return {key: value for key, value in job.items() if key != "params"}

    @app.put("/api/admin/accounts/{account_id}/assignment")
    def assign(account_id: str, data: Assignment, current=Depends(admin)):
        return service.assign_account(account_id, data.person_id)

    @app.post("/api/admin/accounts/{account_id}/password/reveal")
    def reveal(account_id: str, data: Confirm, current=Depends(admin)):
        if not data.confirmed:
            raise DomainError("需要明确确认查看")
        return service.reveal_password(account_id)

    @app.put("/api/admin/accounts/{account_id}/password/record")
    def record_password(account_id: str, data: Password, current=Depends(admin)):
        return service.remember_password(account_id, data.password.get_secret_value())

    @app.post("/api/admin/accounts/{account_id}/password/reset", status_code=202)
    def reset_password(account_id: str, data: Password, current=Depends(admin)):
        value = data.password.get_secret_value()
        job = service.request_password_change(account_id, value)
        deliver_password(config.runtime_dir, job["id"], value)
        return {key: item for key, item in job.items() if key != "params"}

    @app.get("/api/guides")
    def guides(current=Depends(member)):
        import json

        return json.loads((PACKAGE / "tool_policy.json").read_text())

    @app.get("/api/operation-guide")
    def operation_guide(current=Depends(member)):
        filename = "ADMIN_GUIDE.md" if current["role"] == "admin" else "USER_GUIDE.md"
        source = PACKAGE / "guides" / filename
        if not source.exists():
            source = PACKAGE.parent.parent / "docs" / filename
        return {
            "title": "管理员指南" if current["role"] == "admin" else "用户指南",
            "markdown": source.read_text(),
        }

    @app.get("/api/machines")
    def machines(current=Depends(member)):
        return service.visible_machines(current)

    @app.post("/api/admin/machines/probe")
    def host_probe(data: Target, current=Depends(admin)):
        try:
            ipaddress.ip_address(data.host)
            return probe(data.host, data.port, config.ssh_timeout)
        except ValueError as exc:
            raise DomainError("须输入有效 IP") from exc
        except Exception as exc:
            raise DomainError("无法获取 SSH 主机指纹，请检查 IP、端口和网络", 502) from exc

    @app.post("/api/admin/machines", status_code=201)
    def add_machine(data: Machine, current=Depends(admin)):
        values = data.model_dump()
        for key in ["ssh_password", "sudo_password"]:
            values[key] = getattr(data, key).get_secret_value()
        return {"id": service.add_machine(values)}

    @app.put("/api/admin/machines/{machine_id}")
    def update_machine(machine_id: str, data: Machine, current=Depends(admin)):
        values = data.model_dump()
        for key in ["ssh_password", "sudo_password"]:
            values[key] = getattr(data, key).get_secret_value()
        return {"id": service.update_machine(machine_id, values)}

    @app.patch("/api/admin/machines/{machine_id}")
    def available(machine_id: str, data: Available, current=Depends(admin)):
        machine = db.one("SELECT * FROM machines WHERE id=?", (machine_id,))
        if not machine:
            raise DomainError("机器不存在", 404)
        import json

        if data.available:
            with db.connect() as conn:
                service.guard_reboot(conn, machine_id)
        if data.available and not json.loads(machine["capabilities"])["can_create"]:
            reasons = capability_reasons(json.loads(machine["capabilities"]))
            raise DomainError("暂不能开放申请：" + "；".join(reasons))
        with db.connect(write=True) as conn:
            conn.execute("UPDATE machines SET available=? WHERE id=?", (data.available, machine_id))
            db.audit(conn, "admin", "machine.available", machine_id)
        return {"available": data.available}

    @app.post("/api/admin/machines/{machine_id}/reboot/precheck")
    def reboot_precheck(machine_id: str, current=Depends(admin)):
        return service.reboot_precheck(machine_id)

    @app.post("/api/admin/machines/{machine_id}/reboot", status_code=202)
    def reboot_machine(machine_id: str, data: RebootRequest, current=Depends(admin)):
        if not data.confirmed:
            raise DomainError("需要明确确认重启")
        job = service.request_reboot(machine_id, data.force, data.force_confirmed)
        return {key: value for key, value in job.items() if key != "params"}

    @app.post("/api/admin/machines/{machine_id}/reboot/check")
    def check_reboot(machine_id: str, data: Confirm, current=Depends(admin)):
        if not data.confirmed:
            raise DomainError("需要明确确认已检查机器状态")
        return service.check_reboot(machine_id)

    @app.post("/api/viewer-lease")
    def renew_lease(current=Depends(member)):
        return {"scheduled": False, "sampling_seconds": config.snapshot_seconds}

    @app.delete("/api/viewer-lease")
    def release_lease(current=Depends(member)):
        with db.connect(write=True) as conn:
            conn.execute("DELETE FROM viewer_leases WHERE token_hash=?", (current["token_hash"],))
        return {"released": True}

    @app.post("/api/machines/{machine_id}/refresh")
    def refresh(machine_id: str, current=Depends(member)):
        if not any(machine["id"] == machine_id for machine in service.visible_machines(current)):
            raise DomainError("机器不存在", 404)
        with db.connect(write=True) as conn:
            machine = conn.execute(
                "SELECT refresh_requested,last_probe,sampled FROM machines WHERE id=?",
                (machine_id,),
            ).fetchone()
            previous = machine["refresh_requested"]
            if previous and (
                time.time() - previous < 5
                or previous > (machine["last_probe"] or machine["sampled"] or 0)
            ):
                return {"queued": True, "coalesced": True}
            conn.execute(
                "UPDATE machines SET refresh_requested=? WHERE id=?", (time.time(), machine_id)
            )
        return {"queued": True}

    @app.get("/api/accounts")
    def accounts(include_cleared: bool = False, current=Depends(member)):
        return service.accounts(current, include_cleared)

    @app.post("/api/accounts", status_code=202)
    def apply(data: AccountRequest, current=Depends(member)):
        password = data.password.get_secret_value()
        job = service.request_account(
            current, data.machine_id, data.username, password, data.request_key, data.person_id
        )
        if job["state"] in ["queued", "waiting_password"]:
            deliver_password(config.runtime_dir, job["id"], password)
        return job

    @app.post("/api/accounts/{account_id}/retire", status_code=202)
    def retire(account_id: str, data: Confirm, current=Depends(member)):
        if not data.confirmed:
            raise DomainError("须明确确认回收")
        return service.retire_account(current, account_id)

    @app.post("/api/accounts/{account_id}/{operation}", status_code=202)
    def account_admin_action(
        account_id: str, operation: str, data: Confirm, current=Depends(admin)
    ):
        if not data.confirmed or operation not in ["cleanup", "restore", "reconcile"]:
            raise DomainError("操作无效或未明确确认")
        return service.admin_job(operation, account_id)

    @app.get("/api/jobs")
    def jobs(current=Depends(member)):
        if current["role"] == "admin":
            return [
                {key: value for key, value in job.items() if key != "params"}
                for job in db.all("SELECT * FROM jobs ORDER BY created DESC LIMIT 200")
            ]
        return [
            {key: value for key, value in job.items() if key != "params"}
            for job in db.all(
                "SELECT j.* FROM jobs j JOIN accounts a ON a.id=j.account_id WHERE a.person_id=? ORDER BY j.created DESC LIMIT 200",
                (current["person_id"],),
            )
        ]

    @app.post("/api/jobs/{job_id}/password")
    def reenter_password(job_id: str, data: Password, current=Depends(member)):
        job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            raise DomainError("任务不存在", 404)
        service.authorize_account(current, job["account_id"])
        if job["kind"] == "set_password" and current["role"] != "admin":
            raise DomainError("密码重设任务仅管理员可操作", 403)
        if job["state"] not in ["queued", "waiting_password"] or job["kind"] not in [
            "create",
            "set_password",
        ]:
            raise DomainError("任务不是等待密码状态", 409)
        validate_password(data.password.get_secret_value(), minimum=3)
        if not deliver_password(config.runtime_dir, job_id, data.password.get_secret_value()):
            raise DomainError("worker 不在线，请管理员启动后在原任务重试", 503)
        return {"accepted": True}

    @app.get("/api/admin/archives")
    def archives(current=Depends(admin)):
        return db.all(
            "SELECT r.*,a.username,p.name,m.name AS machine_name FROM archives r JOIN accounts a ON a.id=r.account_id JOIN applicants p ON p.id=a.person_id JOIN machines m ON m.id=r.machine_id WHERE a.username<>'root' ORDER BY r.created DESC"
        )

    @app.post("/api/admin/archives/{archive_id}/delete", status_code=202)
    def delete_archive(archive_id: str, data: Confirm, current=Depends(admin)):
        archive = db.one("SELECT * FROM archives WHERE id=? AND deleted IS NULL", (archive_id,))
        if not archive or not data.confirmed:
            raise DomainError("归档不存在或未确认", 404)
        return service.admin_job(
            "delete_archive",
            archive["account_id"],
            {
                "archive_id": archive_id,
                "archive_path": archive["path"],
                "checksum": archive["checksum"],
            },
        )

    @app.get("/api/admin/audit")
    def audit(current=Depends(admin)):
        return db.all("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 200")

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, exc):
        return JSONResponse({"detail": "记录发生冲突，请刷新后重试"}, status_code=409)

    register_portal(app, service, member, admin)
    return app
