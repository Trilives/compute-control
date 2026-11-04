"""Built-in Markdown guides use synthetic images and never load runtime documents."""

from conftest import ADMIN_PASSWORD, login, person
from fastapi.testclient import TestClient
from playwright.sync_api import expect


def install_private_documents(app):
    for name in ["tutorials", "internal-tutorials", "web-tutorials"]:
        root = app.state.config.runtime_dir / name
        root.mkdir(mode=0o700)
        for role in ["admin", "applicant"]:
            for suffix in [".html", ".pdf", ".md"]:
                (root / (role + suffix)).write_text("INTERNAL-DISTRIBUTION-ONLY")


def test_guides_always_return_role_specific_packaged_markdown(app, client):
    install_private_documents(app)
    person(app)
    for role in ["admin", "applicant"]:
        if role == "applicant":
            login(client, "applicant", "张三", "")
        response = client.get(
            "/api/operation-guide?role=admin&edition=internal&path=../internal-tutorials/admin.md"
        )
        data = response.json()
        assert data["format"] == "markdown"
        assert "document_url" not in data and "download_url" not in data
        assert data["markdown"].startswith(
            "# 管理员操作指南" if role == "admin" else "# 申请者使用指南"
        )
        assert "INTERNAL-DISTRIBUTION-ONLY" not in response.text
        assert "![ " not in data["markdown"]
        assert "../src/compute_control/static/guide-images/" in data["markdown"]
        assert response.headers["cache-control"] == "no-store"
        for route in ["document", "download"]:
            assert client.get("/api/operation-guide/" + route).status_code == 404
    with TestClient(app) as guest:
        assert guest.get("/api/operation-guide").status_code == 401


def test_browser_embeds_markdown_and_local_images_without_pdf_or_iframe(app, live_page):
    install_private_documents(app)
    person(app)
    page = live_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.locator("#operation-guide").click()
    guide = page.locator("#guide-content .operation-guide")
    expect(guide.get_by_role("heading", name="管理员操作指南", exact=True)).to_be_visible()
    expect(guide.locator('img[src$="admin-machine-menu.png"]')).to_have_count(1)
    expect(page.locator("#guide-content iframe")).to_have_count(0)
    expect(page.get_by_role("link", name="下载 PDF", exact=True)).to_have_count(0)
    guide.locator("img").evaluate_all("nodes=>nodes.forEach(node=>node.loading='eager')")
    page.wait_for_function(
        "[...document.querySelectorAll('#guide-content img')].every(img=>img.complete&&img.naturalWidth>0)"
    )
    for width in [1440, 390]:
        page.set_viewport_size({"width": width, "height": 1000})
        assert guide.evaluate("node=>node.scrollWidth<=node.clientWidth")
    page.locator("#guide-dialog .dialog-heading button").click()
    page.get_by_role("button", name="退出", exact=True).click()
    expect(page.locator("#guide-content")).to_be_empty()
    page.locator("#login-name").fill("张三")
    page.locator("#login-form button[type=submit]").click()
    page.locator("#operation-guide").click()
    expect(guide.get_by_role("heading", name="申请者使用指南", exact=True)).to_be_visible()
    expect(guide.locator('img[src$="applicant-login.png"]')).to_have_count(1)
    expect(guide.locator('img[src$="admin-machine-menu.png"]')).to_have_count(0)
    assert not errors


def test_markdown_images_cannot_fetch_external_or_private_urls(app, live_page):
    result = live_page.evaluate("""() => {
      const text=['![演示](../src/compute_control/static/guide-images/applicant-login.png)',
        '![外站](https://tracker.invalid/x.png)', '![私密](../internal-tutorials/admin.png)',
        '![脚本](javascript:alert(1))', '![编码](../src/compute_control/static/guide-images/%2e%2e/private.png)',
        '<img src=x onerror="window.guideXss=true">'].join('\\n');
      const node=Guides.markdown(text);
      return {images:[...node.querySelectorAll('img')].map(img=>img.getAttribute('src')),scripts:node.querySelectorAll('script').length,htmlHandlers:node.querySelectorAll('[onerror]').length,xss:!!window.guideXss};
    }""")
    assert result == {
        "images": ["/static/guide-images/applicant-login.png"],
        "scripts": 0,
        "htmlHandlers": 0,
        "xss": False,
    }
