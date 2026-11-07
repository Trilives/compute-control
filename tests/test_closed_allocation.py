"""Closed machines remain usable for explicit administrator allocation only."""

import json

import pytest
from conftest import apply, machine, person
from test_worker import worker

from compute_control.security import DomainError
from compute_control.services import new_id


def test_admin_can_allocate_closed_machine_and_worker_executes(app):
    owner, target = person(app), machine(app, available=False)
    service = app.state.service
    with pytest.raises(DomainError, match="未开放"):
        apply(app, owner, target)
    job = service.request_account(
        {"role": "admin"}, target["id"], "dedicated", "example-password", new_id(), owner["id"]
    )
    assert json.loads(job["params"])["admin_allocation"] is True
    worker(app).process_job(job)
    account = service.db.one("SELECT * FROM accounts WHERE id=?", (job["account_id"],))
    assert account["state"] == "active" and account["person_id"] == owner["id"]
    assert service.db.one("SELECT available FROM machines WHERE id=?", (target["id"],))["available"] == 0


def test_closed_allocation_still_checks_capabilities_and_quota(app):
    owner, target = person(app), machine(app, available=False)
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE machines SET capabilities=? WHERE id=?", (json.dumps({"can_create": False}), target["id"]))
    with pytest.raises(DomainError, match="创建"):
        app.state.service.request_account(
            {"role": "admin"}, target["id"], "dedicated", "example-password", new_id(), owner["id"]
        )
    assert app.state.service.person(owner["id"])["used"] == 0
