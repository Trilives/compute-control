"""Illustrated documents and downloads are authenticated and selected by actual role."""

import pytest
from conftest import ADMIN_PASSWORD, login, person
from fastapi.testclient import TestClient
from playwright.sync_api import expect


def install_tutorials(app):
    root = app.state.config.runtime_dir / "web-tutorials"
    root.mkdir(mode=0o700)
    for role, title in [("admin", "管理员图文教程"), ("applicant", "申请者图文教程")]:
        (root / f"{role}.html").write_text(
            f"<!doctype html><html><head><style>h1{{color:navy}}</style></head>"
            f"<body><h1>{title}</h1><p>{role}-only-content</p></body></html>"
        )
        (root / f"{role}.pdf").write_bytes(b"%PDF-1.4\n" + role.encode())


def test_illustrated_guides_and_downloads_follow_authenticated_role(app, client):
    install_tutorials(app)
    person(app)
    metadata = client.get("/api/operation-guide").json()
    assert metadata["format"] == "html"
    assert metadata["document_url"] == "/api/operation-guide/document"
    assert metadata["download_url"] == "/api/operation-guide/download"
    response = client.get(metadata["document_url"])
    assert "admin-only-content" in response.text
    policy = response.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in policy and "default-src 'none'" in policy
    assert response.headers["cache-control"] == "no-store"
    response = client.get(metadata["download_url"])
    assert response.content.endswith(b"admin")
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    with TestClient(app) as user:
        login(user, "applicant", "张三", "")
        meta = user.get("/api/operation-guide?role=admin").json()
        assert meta["title"] == "用户指南"
        response = user.get(meta["document_url"] + "?role=admin&path=admin.html")
        assert "applicant-only-content" in response.text
        assert "admin-only-content" not in response.text
        assert user.get(meta["download_url"] + "?role=admin").content.endswith(b"applicant")
    with TestClient(app) as guest:
        for url in ["/api/operation-guide", metadata["document_url"], metadata["download_url"]]:
            assert guest.get(url).status_code == 401


def test_browser_embeds_correct_manual_and_clears_it_on_logout(app, live_page):
    install_tutorials(app)
    person(app)
    page = live_page
    page.locator("#admin-entry").click()
    page.locator("#login-name").fill("admin")
    page.locator("#login-password").fill(ADMIN_PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    page.locator("#operation-guide").click()
    frame = page.frame_locator("#operation-guide-frame")
    expect(frame.get_by_role("heading", name="管理员图文教程")).to_be_visible()
    expect(page.get_by_role("link", name="下载 PDF", exact=True)).to_have_attribute(
        "href", "/api/operation-guide/download"
    )
    sandbox = page.locator("#operation-guide-frame").get_attribute("sandbox")
    assert "allow-scripts" not in sandbox
    page.locator("#guide-dialog .dialog-heading button").click()
    page.get_by_role("button", name="退出", exact=True).click()
    expect(page.locator("#operation-guide-frame")).to_have_count(0)
    page.locator("#login-name").fill("张三")
    page.locator("#login-form button[type=submit]").click()
    page.locator("#operation-guide").click()
    expect(frame.get_by_role("heading", name="申请者图文教程")).to_be_visible()
    expect(frame.get_by_text("admin-only-content", exact=True)).to_have_count(0)


def test_missing_document_cannot_fall_through_to_other_role(app, client):
    for route in ["document", "download"]:
        assert client.get("/api/operation-guide/" + route).status_code == 404


def test_internal_distribution_is_never_a_web_tutorial_fallback(app, client):
    private = app.state.config.runtime_dir / "tutorials"
    private.mkdir(mode=0o700)
    separate = app.state.config.runtime_dir / "internal-tutorials"
    separate.mkdir(mode=0o700)
    for root in [private, separate]:
        for role in ["admin", "applicant"]:
            (root / (role + ".html")).write_text("INTERNAL-DISTRIBUTION-ONLY")
            (root / (role + ".pdf")).write_bytes(b"%PDF-INTERNAL-DISTRIBUTION-ONLY")
    metadata = client.get("/api/operation-guide").json()
    assert metadata.get("format") != "html"
    for route in ["document", "download"]:
        result = client.get(
            "/api/operation-guide/" + route + "?edition=internal&path=../tutorials/admin.html"
        )
        assert result.status_code == 404 and "INTERNAL-DISTRIBUTION-ONLY" not in result.text
    install_tutorials(app)
    assert client.get("/api/operation-guide").json()["format"] == "html"
    assert "admin-only-content" in client.get("/api/operation-guide/document").text
    assert "INTERNAL-DISTRIBUTION-ONLY" not in client.get("/api/operation-guide/document").text


@pytest.mark.parametrize("link_directory", [False, True])
def test_web_guide_cannot_symlink_into_internal_distribution(app, client, link_directory):
    private = app.state.config.runtime_dir / "internal-tutorials"
    private.mkdir()
    (private / "admin.html").write_text("INTERNAL-DISTRIBUTION-ONLY")
    (private / "admin.pdf").write_bytes(b"%PDF-INTERNAL-DISTRIBUTION-ONLY")
    web = app.state.config.runtime_dir / "web-tutorials"
    if link_directory:
        web.symlink_to(private, target_is_directory=True)
    else:
        web.mkdir()
        for suffix in [".html", ".pdf"]:
            (web / ("admin" + suffix)).symlink_to(private / ("admin" + suffix))
    assert client.get("/api/operation-guide").json().get("format") != "html"
    for route in ["document", "download"]:
        response = client.get("/api/operation-guide/" + route)
        assert response.status_code == 404 and "INTERNAL-DISTRIBUTION-ONLY" not in response.text
