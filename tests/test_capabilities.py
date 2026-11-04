"""NSS entry presence must not be confused with an active domain provider."""

from pathlib import Path

import pytest

from compute_control import remote


@pytest.mark.parametrize(
    ("nss", "passwd", "active", "local", "blocking"),
    [
        ("passwd: files systemd sss", "local:x:1000:1000::/home/local:/bin/bash", False, True, []),
        ("passwd: files systemd sss", "", True, False, ["sss"]),
        ("passwd: files ldap", "", False, False, ["ldap"]),
        ("passwd: compat", "local:x:1000:1000::/home/local:/bin/bash", False, True, []),
        ("passwd: compat", "+::::::", False, False, ["compat"]),
        ("passwd: files [SUCCESS=return] systemd # sss", "", True, True, []),
        ("hosts: files dns", "", False, False, []),
    ],
)
def test_nss_sources(tmp_path, monkeypatch, nss, passwd, active, local, blocking):
    (tmp_path / "nsswitch.conf").write_text(nss)
    (tmp_path / "passwd").write_text(passwd)
    monkeypatch.setattr(remote, "Path", lambda value: tmp_path / Path(value).name)
    monkeypatch.setattr(remote, "sssd_in_use", lambda: active)
    sources, inactive, blocked, allowed = remote.account_sources()
    assert allowed == local
    assert blocked == blocking
    if "sss" in sources and not active:
        assert inactive == ["sss"]


@pytest.mark.parametrize("evidence", ["config", "snippet", "process", "pipe", "none"])
def test_sssd_activity_evidence(tmp_path, monkeypatch, evidence):
    targets = {
        "config": "etc/sssd/sssd.conf",
        "snippet": "etc/sssd/conf.d/site.conf",
        "process": "proc/123/comm",
        "pipe": "var/lib/sss/pipes/nss",
    }
    if evidence in targets:
        path = tmp_path / targets[evidence]
        path.parent.mkdir(parents=True)
        path.write_text("sssd_nss" if evidence == "process" else "")
    monkeypatch.setattr(remote, "Path", lambda value: tmp_path / str(value).lstrip("/"))
    assert remote.sssd_in_use() == (evidence != "none")
