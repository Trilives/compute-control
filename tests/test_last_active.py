"""Last observable successful login is machine-scoped and survives log retention."""

import copy
import time

from conftest import ADMIN_PASSWORD, REPORT, apply, login, machine, person
from fastapi.testclient import TestClient
from playwright.sync_api import expect
from test_worker import worker

from compute_control.db import Database


def sample(app, target, username, timestamp):
    report = copy.deepcopy(REPORT)
    report["identity"] = target["identity"]
    report["login_events"] = [{"username": username, "ts": timestamp}] if timestamp else []
    app.state.service.save_snapshot(target["id"], report, True)


def test_last_active_is_per_machine_and_owner_with_missing_data_left_unknown(app, client):
    owner, other = person(app), person(app, "李四")
    first, second = machine(app), machine(app, "192.0.2.2")
    runner = worker(app)
    for target, user, name in [
        (first, owner, "same_user"),
        (second, owner, "same_user"),
        (first, other, "other_user"),
    ]:
        runner.process_job(apply(app, user, target, username=name))
    now = time.time()
    sample(app, first, "same_user", now - 60)
    sample(app, second, "same_user", now - 3600)
    sample(app, first, "same_user", now - 600)
    accounts = client.get("/api/accounts").json()
    by_key = {(a["machine_id"], a["username"]): a for a in accounts}
    assert by_key[first["id"], "same_user"].get("last_active") == now - 60
    assert by_key[second["id"], "same_user"].get("last_active") == now - 3600
    assert by_key[first["id"], "other_user"].get("last_active") is None
    with TestClient(app) as user:
        login(user, "applicant", owner["name"], "")
        own = user.get("/api/accounts").json()
        assert len(own) == 2 and all(a["username"] == "same_user" for a in own)
        assert sorted(a["last_active"] for a in own) == [now - 3600, now - 60]
    assert client.get("/api/accounts", headers={"X-CSRF-Token": "unused"}).status_code == 200


def test_last_active_persists_after_old_event_pruning_and_restart(app):
    target = machine(app)
    task = apply(app, person(app), target)
    worker(app).process_job(task)
    past = time.time() - 120 * 86400
    sample(app, target, "researcher", past)
    assert not app.state.db.all("SELECT * FROM login_events WHERE username='researcher'")
    assert app.state.service.accounts({"role": "admin"})[0].get("last_active") == past
    sample(app, target, "researcher", None)
    app.state.db = app.state.service.db = Database(app.state.config.runtime_dir)
    assert app.state.service.accounts({"role": "admin"})[0].get("last_active") == past


def test_ledger_displays_last_login_basis_and_unknown_without_assuming_inactivity(app, live_page):
    owner = person(app)
    target = machine(app)
    runner = worker(app)
    for name in ["recent_user", "unknown_user"]:
        runner.process_job(apply(app, owner, target, username=name))
    timestamp = time.time() - 1800
    sample(app, target, "recent_user", timestamp)
    page = live_page
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.locator("#tabs").get_by_role("button", name="账号台账", exact=True).click()
    expect(page.get_by_role("columnheader", name="最后活跃时间", exact=True)).to_have_count(1)
    recent = page.locator("tbody tr").filter(has_text="recent_user")
    expect(recent.locator("time")).to_have_attribute(
        "title", "最近一次可观测的成功登录；不代表进程结束时间"
    )
    expect(recent.locator("time")).to_have_attribute(
        "datetime", page.evaluate("ts=>new Date(ts*1000).toISOString()", timestamp)
    )
    unknown = page.locator("tbody tr").filter(has_text="unknown_user")
    expect(unknown).to_contain_text("暂无记录")
    assert "从未" not in unknown.inner_text()


def test_existing_login_history_is_backfilled_when_database_is_upgraded(app):
    target = machine(app)
    task = apply(app, person(app), target)
    worker(app).process_job(task)
    timestamp = time.time() - 300
    with app.state.db.connect(write=True) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
        if "last_active" in columns:
            conn.execute("ALTER TABLE accounts DROP COLUMN last_active")
        conn.execute(
            "INSERT INTO login_events VALUES (?,?,?)", (target["id"], "researcher", timestamp)
        )
    restored = Database(app.state.config.runtime_dir)
    assert restored.one("SELECT * FROM accounts")["last_active"] == timestamp
