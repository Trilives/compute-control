from pathlib import Path
from types import SimpleNamespace

from conftest import login, person
from fastapi.testclient import TestClient

from compute_control import remote


def test_guide_is_available_to_both_roles_and_keeps_user_policy(app, client):
    guide = client.get("/api/guides").json()
    assert len(guide["rows"]) == 24
    assert any(row["tool"] == "Pixi" and row["level"] == "用户级" for row in guide["rows"])
    assert {item["key"] for item in guide["checks"]} >= {
        "git",
        "gcc",
        "g++",
        "make",
        "cmake",
        "ninja",
        "gdb",
        "system-python",
    }
    person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        assert user.get("/api/guides").json() == guide
    with TestClient(app) as guest:
        assert guest.get("/api/guides").status_code == 401


def test_missing_shared_tool_is_reported_without_installing(monkeypatch):
    calls = []
    monkeypatch.setattr(remote.shutil, "which", lambda command: None)
    monkeypatch.setattr(remote.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    result = remote.system_tool_inventory(
        [{"key": "gcc", "label": "GCC", "candidates": ["gcc"], "args": ["--version"]}]
    )
    assert result[0]["installed"] is False
    assert calls == []


def test_shared_tool_version_probe_is_bounded_and_read_only(monkeypatch):
    python = "/usr/bin/python3"
    if not Path(python).is_file():
        return
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="Python system-test\n", stderr="", returncode=0)

    monkeypatch.setattr(remote.subprocess, "run", run)
    result = remote.system_tool_inventory(
        [{"key": "python", "label": "Python", "candidates": [python], "args": ["--version"]}]
    )
    assert result[0]["installed"] and result[0]["version"] == "Python system-test"
    assert calls[0][0][-1] == "--version" and calls[0][1]["timeout"] == 3


def test_operation_guides_are_role_selected_and_never_read_private_file(app, client):
    admin = client.get("/api/operation-guide").json()
    assert admin["title"] == "管理员指南" and "初始化与部署" in admin["markdown"]
    assert "| 管理员密码 |" not in admin["markdown"]
    assert "本机开发环境实际记录" not in admin["markdown"]
    person(app)
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        data = user.get("/api/operation-guide?role=admin&path=private/ADMIN_GUIDE.md").json()
        assert data["title"] == "用户指南"
        assert data["markdown"].startswith("# 申请者使用指南")
        assert "初始化与部署" not in data["markdown"]
