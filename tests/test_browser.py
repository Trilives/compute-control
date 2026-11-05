"""Real Chromium flows against an isolated local application and test database."""

import socket
import threading
import time

import httpx
import uvicorn
from conftest import ADMIN_PASSWORD
from playwright.sync_api import expect, sync_playwright


def test_admin_and_applicant_browser_flow(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if httpx.get(address + "/healthz").status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.05)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(address)
            expect(page.locator("html")).to_have_attribute("data-theme", "light")
            page.get_by_role("button", name="切换至夜间模式").click()
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            page.reload()
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")
            page.get_by_role("button", name="切换至白天模式").click()
            expect(page.locator("#login-title")).to_have_text("申请者模式")
            expect(page.locator("#login-password")).not_to_be_visible()
            page.get_by_role("button", name="管理员入口").click()
            page.locator("#login-name").fill("admin")
            page.locator("#login-password").fill(ADMIN_PASSWORD)
            page.locator("#login-form button[type=submit]").click()
            expect(page.get_by_text("还没有可见的机器")).to_be_visible()
            page.get_by_role("button", name="+ 添加机器").click()
            expect(page.locator("#machine-form input[name=fingerprint]")).to_have_count(0)
            expect(page.locator("#machine-form input[name=verified]")).to_have_count(0)
            form = page.locator("#machine-form")
            for field, value in {
                "name": "浏览器测试节点",
                "host": "192.0.2.100",
                "username": "operator",
                "ssh_password": "example-password",
            }.items():
                form.locator(f"input[name={field}]").fill(value)
            form.locator("button[type=submit]").click()
            expect(page.get_by_role("heading", name="浏览器测试节点")).to_be_visible()
            page.get_by_role("button", name="管理机器", exact=True).click()
            page.get_by_role("button", name="更新凭据").click()
            form.locator("input[name=ssh_password]").fill("updated-example-password")
            form.locator("button[type=submit]").click()
            expect(page.locator("#machine-dialog")).not_to_be_visible()
            target = app.state.db.one("SELECT * FROM machines")
            report = app.state.service.remote.execute(target, "inspect")
            report.update(
                memory={"MemTotal": 4096, "MemAvailable": 1024},
                cpu_times={"total": 1000, "idle": 600},
            )
            report["gpus"] = [
                {
                    "index": "0",
                    "uuid": "GPU-test",
                    "name": "Test GPU",
                    "memory_total_mib": "4096",
                    "memory_used_mib": "2048",
                    "memory_free_mib": "2048",
                    "gpu_percent": "42",
                    "temperature": "50",
                    "power_w": "80",
                }
            ]
            app.state.service.save_snapshot(target["id"], report, False)
            report["cpu_times"] = {"total": 2000, "idle": 900}
            app.state.service.save_snapshot(target["id"], report, False)
            page.reload()
            expect(page.locator(".line-chart")).to_have_count(0)
            page.locator(".history-details > summary").click()
            expect(page.locator(".line-chart")).to_have_count(4)
            expect(page.locator(".chart-header")).to_contain_text(
                ["CPU 使用率70.0%", "内存使用率75.0%", "GPU 利用率42.0%", "显存占用2048.0 MiB"]
            )
            assert page.evaluate("""() => {
                const machine = structuredClone(state.machines[0]);
                machine.sampled += 5; machine.snapshot.cpu_percent = 60;
                ComputeCharts.observe(machine);
                const chart = ComputeCharts.host(machine);
                const count = chart.querySelector('svg').querySelectorAll('circle').length;
                ComputeCharts.observe(machine);
                const repeated = ComputeCharts.host(machine).querySelector('svg').querySelectorAll('circle').length;
                return chart.querySelector('path').getAttribute('d').includes('L') && count === 2 && repeated === 2;
            }""")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.set_viewport_size({"width": 1440, "height": 1000})
            original_execute = app.state.service.remote.execute

            def busy_report(machine, action, data=None):
                result = original_execute(machine, action, data)
                if action == "inspect":
                    result["active_user_processes"] = [
                        {"pid": 123, "username": "worker", "name": "python"}
                    ]
                    result["capabilities"]["can_reboot"] = True
                return result

            app.state.service.remote.execute = busy_report
            confirmations = []

            def reboot_confirmation(dialog):
                confirmations.append(dialog.message)
                dialog.accept() if len(confirmations) == 1 else dialog.dismiss()

            page.on("dialog", reboot_confirmation)
            page.get_by_role("button", name="管理机器", exact=True).click()
            page.get_by_role("button", name="重启机器", exact=True).click()
            page.wait_for_function(
                '() => !document.querySelector("#notice")?.textContent.includes("重启指令已提交")'
            )
            expect(page.get_by_role("button", name="开发工具说明", exact=True)).to_be_visible()
            # Wait for the read-only precheck to produce the third-stage dialog.
            deadline = time.monotonic() + 5
            while len(confirmations) < 2 and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert len(confirmations) == 2 and "第三次确认" in confirmations[1]
            assert app.state.db.all("SELECT id FROM jobs WHERE kind='reboot'") == []
            page.remove_listener("dialog", reboot_confirmation)
            page.get_by_role("button", name="管理员指南", exact=True).click()
            expect(page.locator("#guide-content")).to_contain_text("初始化与部署")
            page.locator("#guide-dialog").get_by_role("button", name="关闭", exact=True).click()
            page.get_by_role("button", name="开发工具说明", exact=True).click()
            expect(page.locator("#guide-content")).to_contain_text("CUDA Toolkit")
            expect(page.locator("#guide-content")).to_contain_text("NVIDIA 驱动")
            page.locator("#guide-dialog").get_by_role("button", name="关闭", exact=True).click()
            page.get_by_role("button", name="管理员名单", exact=True).click()
            contact = page.get_by_role("textbox", name="补充联系说明")
            contact.fill("系统依赖问题联系管理员\n分机 123")
            page.get_by_role("button", name="保存联系方式", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("已保存")
            contact.fill("暂未保存的草稿")
            contact.evaluate("node => node.dataset.stable = 'yes'")
            requests = []
            page.on(
                "request",
                lambda request: requests.append(request.url) if "/api/" in request.url else None,
            )
            page.wait_for_timeout(150)
            requests.clear()
            page.wait_for_timeout(5500)
            expect(contact).to_have_value("暂未保存的草稿")
            expect(contact).to_have_attribute("data-stable", "yes")
            assert requests == []
            page.get_by_role("button", name="申请者与额度", exact=True).click()
            page.get_by_role("button", name="+ 添加姓名").click()
            page.locator("#person-form input[name=name]").fill("浏览器测试用户")
            page.locator("#person-form input[name=quota]").fill("4")
            page.locator("#person-form button[type=submit]").click()
            expect(page.locator("#content tbody")).to_contain_text("浏览器测试用户")
            page.get_by_role("button", name="退出", exact=True).click()
            expect(page.locator("#login-title")).to_have_text("申请者模式")
            page.locator("#login-name").fill("浏览器测试用户")
            page.locator("#login-form button[type=submit]").click()
            expect(page.locator("#identity")).to_contain_text("浏览器测试用户")
            expect(page.locator("#summary")).to_contain_text("0 / 4")
            page.get_by_role("button", name="联系管理员", exact=True).click()
            expect(page.locator("#contact-text")).to_contain_text("分机 123")
            page.locator("#contact-admin-dialog").get_by_role(
                "button", name="关闭", exact=True
            ).click()
            page.get_by_role("button", name="用户指南", exact=True).click()
            expect(page.locator("#guide-content")).to_contain_text("申请账号")
            page.locator("#guide-dialog").get_by_role("button", name="关闭", exact=True).click()
            expect(page.get_by_role("button", name="申请 sudo 权限", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="申请者与额度", exact=True)).to_have_count(0)
            expect(page.get_by_text("还没有可见的机器")).to_be_visible()
            expect(page.get_by_role("button", name="+ 申请账号", exact=True)).to_be_visible()
            page.get_by_role("button", name="+ 申请账号", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("没有开放申请的机器")
            with app.state.db.connect(write=True) as conn:
                conn.execute("UPDATE machines SET available=1 WHERE id=?", (target["id"],))
            page.reload()
            expect(page.get_by_role("button", name="+ 申请账号", exact=True)).to_be_visible()
            page.get_by_role("button", name="申请账号", exact=True).click()
            expect(page.locator("#account-dialog")).to_be_visible()
            expect(page.locator("#account-form select[name=machine_id]")).to_have_value(
                target["id"]
            )
            expect(page.locator("#owner-wrap")).not_to_be_visible()
            page.locator("#account-form input[name=username]").fill("root")
            page.locator("#account-form input[name=password]").fill("123")
            page.locator("#account-form button[type=submit]").click()
            expect(page.locator("#alert-dialog")).to_be_visible()
            expect(page.locator("#account-dialog")).to_be_visible()
            assert page.locator("#alert-dialog").evaluate(
                "node => { const r=node.getBoundingClientRect(); return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2).closest('dialog') === node; }"
            )
            page.locator("#alert-dialog").get_by_role("button", name="确定", exact=True).click()
            expect(page.locator("#account-form input[name=username]")).to_have_value("root")
            expect(page.locator("#account-form input[name=password]")).to_have_value("123")
            page.locator("#account-form input[name=username]").fill("browser_user")
            page.locator("#account-form input[name=password]").fill("123")
            page.locator("#account-form button[type=submit]").click()
            expect(page.locator("#account-dialog")).not_to_be_visible()
            expect(page.locator("#page-title")).to_have_text("后台任务")
            created = app.state.db.one("SELECT * FROM accounts WHERE username=?", ("browser_user",))
            assert created is not None and created["machine_id"] == target["id"]
            assert created["person_id"] == app.state.db.one("SELECT id FROM applicants")["id"]
            page.get_by_role("button", name="当前状态", exact=True).click()
            expect(page.locator(".machine-account-count")).to_contain_text("本机已有 1 个账号")
            expect(page.get_by_role("button", name="申请账号", exact=True)).to_be_enabled()
            page.get_by_role("button", name="申请账号", exact=True).click()
            expect(page.locator("#account-form select[name=machine_id] option")).to_contain_text(
                "已有 1 个账号"
            )
            page.locator("#account-dialog").get_by_role("button", name="取消", exact=True).click()
            with app.state.db.connect(write=True) as conn:
                conn.execute("UPDATE applicants SET quota=1")
            page.reload()
            page.get_by_role("button", name="+ 申请账号", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("已达到账号上限")
            page.locator("#alert-dialog").get_by_role("button", name="确定", exact=True).click()
            page.get_by_role("button", name="账号台账", exact=True).click()
            expect(page.locator("#content tbody")).to_contain_text("browser_user")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == []
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
