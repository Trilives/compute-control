"""Explicit production HTTP supports login while HTTPS remains the default."""

import sys

import pytest
import uvicorn
from conftest import ADMIN_PASSWORD
from fastapi.testclient import TestClient

from compute_control import cli
from compute_control.app import create_app
from compute_control.config import Config


@pytest.fixture
def production_config(tmp_path):
    def write(insecure=None):
        path = tmp_path / "production.toml"
        body = 'mode="production"\nhost="0.0.0.0"\nport=80\nruntime_dir="state"\n'
        if insecure is not None:
            body += f"allow_insecure_http={insecure}\n"
        path.write_text(body)
        return path

    return write


@pytest.mark.parametrize("value", ['"false"', "1", "[]", "{}"])
def test_http_opt_in_requires_a_boolean(production_config, value):
    with pytest.raises(ValueError, match="allow_insecure_http 必须为布尔值"):
        Config.load(production_config(value))


def test_explicit_http_keeps_authenticated_session_and_csrf(production_config):
    config = Config.load(production_config("true"))
    assert config.mode == "production" and not config.secure_cookie
    application = create_app(config)
    application.state.auth.init_admin("admin", ADMIN_PASSWORD)
    with TestClient(application, base_url="http://testserver") as client:
        response = client.get("/api/auth/me")
        cookie = response.headers["set-cookie"].lower()
        assert "secure" not in cookie and "httponly" in cookie and "samesite=strict" in cookie
        login = {"role": "admin", "username": "admin", "password": ADMIN_PASSWORD}
        assert client.post("/api/auth/login", json=login).status_code == 403
        response = client.post(
            "/api/auth/login", headers={"X-CSRF-Token": response.json()["csrf"]}, json=login
        )
        assert response.status_code == 200 and response.json()["role"] == "admin"
        assert client.get("/api/admin/applicants").status_code == 200
        assert client.post("/api/auth/logout").status_code == 403
        response = client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": response.json()["csrf"]}
        )
        assert response.status_code == 200
        assert client.get("/api/admin/applicants").status_code == 401


def test_default_production_still_requires_tls(production_config, monkeypatch, capsys):
    path = production_config()
    assert Config.load(path).secure_cookie
    monkeypatch.setattr(sys, "argv", ["compute-control", "--config", str(path), "serve"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "生产模式必须提供" in capsys.readouterr().err


@pytest.mark.parametrize("insecure", ["true", "false"])
def test_cli_starts_selected_transport(production_config, monkeypatch, insecure):
    path = production_config(insecure)
    arguments = ["compute-control", "--config", str(path), "serve"]
    if insecure == "false":
        arguments += ["--ssl-certfile", "cert.pem", "--ssl-keyfile", "key.pem"]
    calls = []
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    cli.main()
    assert len(calls) == 1
    application, options = calls[0]
    assert options["port"] == 80 and options["host"] == "0.0.0.0"
    assert application.state.config.secure_cookie == (insecure == "false")
    assert options["ssl_certfile"] == ("cert.pem" if insecure == "false" else None)
    assert options["ssl_keyfile"] == ("key.pem" if insecure == "false" else None)


@pytest.mark.parametrize(
    ("insecure", "tls_args"),
    [
        ("true", ["--ssl-certfile", "cert.pem", "--ssl-keyfile", "key.pem"]),
        ("true", ["--ssl-certfile", "cert.pem"]),
        ("false", ["--ssl-keyfile", "key.pem"]),
    ],
)
def test_cli_rejects_conflicting_or_incomplete_tls(
    production_config, monkeypatch, capsys, insecure, tls_args
):
    path = production_config(insecure)
    monkeypatch.setattr(
        sys, "argv", ["compute-control", "--config", str(path), "serve", *tls_args]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "TLS" in capsys.readouterr().err
