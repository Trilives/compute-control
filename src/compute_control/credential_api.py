"""Authenticated, revalidated credential-management endpoints."""

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from .models import ManagementPassword
from .security import DomainError, validate_password


def register_credentials(app, service, auth, admin, attach_cookie):
    def validate(data, machine=False):
        password = data.new_password.get_secret_value()
        if password != data.confirm_password.get_secret_value():
            raise DomainError("两次输入的新密码不一致")
        validate_password(password, minimum=3 if machine else 12)
        return password

    @app.put("/api/admin/password")
    def web_password(data: ManagementPassword, request: Request, current=Depends(admin)):
        password = validate(data)
        auth.change_password(
            current, data.current_password.get_secret_value(), password, request.client.host
        )
        token, value = auth.issue()
        return attach_cookie(JSONResponse({"role": "anonymous", "csrf": value["csrf"]}), token)

    @app.post("/api/admin/machines/{machine_id}/password")
    def machine_password(
        machine_id: str, data: ManagementPassword, request: Request, current=Depends(admin)
    ):
        password = validate(data, machine=True)
        auth.confirm_password(data.current_password.get_secret_value(), request.client.host)
        result = service.change_management_password(machine_id, password)
        return JSONResponse(result, status_code=202 if result["state"] == "pending" else 200)

    @app.post("/api/admin/machines/{machine_id}/password/check")
    def check_password(machine_id: str, current=Depends(admin)):
        result = service.check_management_password(machine_id)
        return JSONResponse(result, status_code=202 if result["state"] == "pending" else 200)
