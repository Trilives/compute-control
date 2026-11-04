"""Additional console routes; existing composition supplies role/CSRF dependencies."""

from fastapi import Depends
from fastapi.responses import FileResponse, HTMLResponse

from .models import (
    AdminContact,
    Announcement,
    Confirm,
    MachineNote,
    PermissionDecision,
    PermissionRequest,
    Pin,
    Privilege,
)


def register_portal(app, service, member, admin):
    def tutorial_path(current, suffix):
        from .security import DomainError

        name = "admin" if current["role"] == "admin" else "applicant"
        path = service.config.runtime_dir / "tutorials" / (name + suffix)
        if not path.is_file():
            raise DomainError("图文教程尚未安装", 404)
        return path

    @app.get("/api/operation-guide/document")
    def tutorial_document(current=Depends(member)):
        return HTMLResponse(tutorial_path(current, ".html").read_text())

    @app.get("/api/operation-guide/download")
    def tutorial_download(current=Depends(member)):
        filename = "管理员图文教程.pdf" if current["role"] == "admin" else "申请者图文教程.pdf"
        return FileResponse(
            tutorial_path(current, ".pdf"), media_type="application/pdf", filename=filename
        )

    @app.get("/api/announcements")
    def announcements(pinned: bool = False, current=Depends(member)):
        return service.announcements(pinned)

    @app.post("/api/admin/announcements", status_code=201)
    def publish(data: Announcement, current=Depends(admin)):
        return service.publish_announcement(data.model_dump())

    @app.patch("/api/admin/announcements/{item_id}")
    def pin(item_id: str, data: Pin, current=Depends(admin)):
        return service.change_announcement(item_id, pinned=data.pinned)

    @app.delete("/api/admin/announcements/{item_id}")
    def delete_announcement(item_id: str, current=Depends(admin)):
        return service.change_announcement(item_id, delete=True)

    @app.get("/api/admin-contacts")
    def contacts(current=Depends(member)):
        return service.admin_contacts()

    @app.post("/api/admin/contacts", status_code=201)
    def add_contact(data: AdminContact, current=Depends(admin)):
        return service.save_admin_contact(data.model_dump())

    @app.put("/api/admin/contacts/{item_id}")
    def update_contact(item_id: str, data: AdminContact, current=Depends(admin)):
        return service.save_admin_contact(data.model_dump(), item_id)

    @app.delete("/api/admin/contacts/{item_id}")
    def delete_contact(item_id: str, current=Depends(admin)):
        return service.delete_admin_contact(item_id)

    @app.get("/api/permission-requests")
    def requests(current=Depends(member)):
        return service.permission_requests(current)

    @app.post("/api/permission-requests", status_code=201)
    def request(data: PermissionRequest, current=Depends(member)):
        return service.request_permission(current, data.account_id, data.reason)

    @app.post("/api/admin/permission-requests/{item_id}/decision")
    def decide(item_id: str, data: PermissionDecision, current=Depends(admin)):
        return service.decide_permission(item_id, data.action, data.response)

    @app.put("/api/admin/accounts/{account_id}/privilege", status_code=202)
    def privilege(account_id: str, data: Privilege, current=Depends(admin)):
        return service.set_privilege(account_id, data.enabled)

    @app.get("/api/machines/{machine_id}/history")
    def history(machine_id: str, current=Depends(member)):
        return service.machine_history(current, machine_id)

    @app.post("/api/accounts/{account_id}/ledger/{operation}")
    def ledger(account_id: str, operation: str, data: Confirm, current=Depends(member)):
        from .security import DomainError

        if not data.confirmed or operation not in ("clear", "restore"):
            raise DomainError("需要明确确认有效的台账操作")
        return service.set_ledger_cleared(current, account_id, operation == "clear")

    @app.get("/api/machines/{machine_id}/notes")
    def notes(machine_id: str, current=Depends(member)):
        return service.machine_notes(current, machine_id)

    @app.post("/api/machines/{machine_id}/notes", status_code=201)
    def add_note(machine_id: str, data: MachineNote, current=Depends(member)):
        return service.add_machine_note(current, machine_id, data.content)

    @app.delete("/api/machines/{machine_id}/notes/{note_id}")
    def delete_note(machine_id: str, note_id: str, current=Depends(member)):
        return service.delete_machine_note(current, machine_id, note_id)
