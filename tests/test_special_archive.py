"""Special filesystem objects are reviewed and restored without being opened as data files."""

import json
import os
import socket
import stat
from pathlib import Path

import pytest
from conftest import apply, machine, person
from playwright.sync_api import expect
from test_archive import sandbox as sandbox
from test_machine_management_ui import enter_admin
from test_worker import worker

from compute_control import remote
from compute_control.security import DomainError


def test_admin_archive_recreates_fifo_and_socket_with_hardlinks(sandbox):
    work = sandbox.home / "runtime"
    work.mkdir()
    fifo = work / "queue"
    os.mkfifo(fifo, 0o640)
    os.link(fifo, work / "second-queue")
    endpoint = sandbox.root / "socket"
    with socket.socket(socket.AF_UNIX) as service:
        service.bind(str(endpoint))
    endpoint.rename(work / "endpoint.sock")
    os.link(work / "endpoint.sock", work / "second-endpoint.sock")

    preview = remote.retirement_activity(sandbox.payload)
    assert preview["processes"] == [] and not preview["special_scan_incomplete"]
    assert {item["path"]: item["kind"] for item in preview["special_objects"]} == {
        "runtime/queue": "fifo",
        "runtime/second-queue": "fifo",
        "runtime/endpoint.sock": "socket",
        "runtime/second-endpoint.sock": "socket",
    }
    with pytest.raises(remote.Refuse, match="主目录存在特殊对象"):
        remote.retire(sandbox.payload)

    def relative_manifest():
        return remote.restored_manifest(sandbox.home)

    expected = relative_manifest()
    result = remote.retire(sandbox.payload | {"archive_special_objects": True, "job_id": "c" * 32})
    assert result["state"] == "retired"
    assert Path(result["archive"]).is_file()
    assert remote.cleanup(sandbox.payload)["state"] == "home_cleaned"
    proof = json.loads(sandbox.receipt.read_text())
    remote.restore_home(sandbox.receipt, proof, sandbox.payload)
    assert relative_manifest() == expected
    assert stat.S_ISFIFO(fifo.lstat().st_mode)
    assert (work / "second-queue").stat().st_ino == fifo.stat().st_ino
    assert (work / "endpoint.sock").is_socket()
    assert (work / "second-endpoint.sock").stat().st_ino == (work / "endpoint.sock").stat().st_ino


def test_tampered_special_manifest_cannot_authorize_home_cleanup(sandbox):
    fifo = sandbox.home / "queue"
    os.mkfifo(fifo)
    result = remote.retire(sandbox.payload | {"archive_special_objects": True})
    manifest = Path(result["archive"]).parent / "manifest.json"
    manifest.write_text(manifest.read_text() + " ")
    with pytest.raises(remote.Refuse, match="归档清单摘要不符"):
        remote.cleanup(sandbox.payload)
    assert stat.S_ISFIFO(fifo.lstat().st_mode)


@pytest.mark.parametrize(("kind", "mode"), [("char", stat.S_IFCHR), ("block", stat.S_IFBLK)])
def test_device_metadata_recreates_the_same_device_number(tmp_path, monkeypatch, kind, mode):
    stamp = 1_000_000_000
    rdev = os.makedev(7, 19)
    entry = {
        "mode": mode | 0o640,
        "special": kind,
        "mtime_ns": stamp,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "inode": 5,
        "rdev": rdev,
        "xattrs": {},
    }
    created = []

    def create_device(path, flags, number):
        created.append((path, stat.S_IFMT(flags), number))
        path.touch()

    monkeypatch.setattr(remote.os, "mknod", create_device)
    remote.restore_special_objects(tmp_path, {".": {"mtime_ns": stamp}, "node": entry}, {"node"})
    assert created == [(tmp_path / "node", mode, rdev)]


def test_admin_sees_special_file_and_can_recycle_without_active_processes(app, live_page, client):
    owner, target = person(app), machine(app)
    runner = worker(app)
    created = apply(app, owner, target)
    runner.process_job(created)
    account_id = created["account_id"]
    job = app.state.service.retire_account({"role": "admin"}, account_id)
    fake = app.state.service.remote
    fake.error = DomainError("主目录存在特殊对象，需管理员处理", 502)
    runner.process_job(job)
    fake.error = None
    original = fake.execute

    def report(machine, action, data=None):
        if action == "retirement_activity":
            return {
                "processes": [],
                "schedule_blocker": None,
                "special_objects": [{"path": ".run/old.sock", "kind": "socket"}],
            }
        return original(machine, action, data)

    fake.execute = report
    path = f"/api/accounts/{account_id}/retire"
    assert (
        client.post(path, json={"confirmed": True, "archive_special_objects": True}).status_code
        == 409
    )
    page = live_page
    enter_admin(page)
    page.get_by_role("button", name="审阅", exact=True).click()
    page.get_by_role("button", name="审阅回收", exact=True).click()
    dialog = page.get_by_role("dialog", name="审阅回收")
    expect(dialog).to_contain_text("当前没有占用进程")
    expect(dialog.get_by_role("row", name=".run/old.sock Unix 套接字")).to_be_visible()
    page.once("dialog", lambda prompt: prompt.accept())
    dialog.get_by_role("button", name="强制回收", exact=True).click()
    expect(page.locator("#page-title")).to_have_text("后台任务")
    retry = app.state.db.one("SELECT params FROM jobs WHERE kind='retire' ORDER BY created DESC")
    assert json.loads(retry["params"]) == {"archive_special_objects": True}
