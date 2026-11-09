import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

pytest.importorskip("mcp.server.fastmcp")
import homelab_server as homelab
import remote

ADDRESS_SETTINGS = ("PVE_URL", "UPTIME_KUMA_URL", "NEXTCLOUD_URL", "PUFFERPANEL_URL", "WORKSPACE_HOST")


def test_no_address_is_built_in(monkeypatch):
    for name in ADDRESS_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOMELAB_ENV", "/nonexistent/homelab.env")
    try:
        reloaded = importlib.reload(homelab)
        assert (reloaded.PVE_URL, reloaded.KUMA_URL, reloaded.NC_URL, reloaded.PUFFER_URL) == ("", "", "", "")
    finally:
        monkeypatch.undo()
        importlib.reload(homelab)


def test_proxmox_without_an_address_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(homelab, "PVE_URL", "")
    monkeypatch.setattr(homelab, "PVE_TOKEN", "token")
    with pytest.raises(RuntimeError, match="PVE_URL"):
        homelab._pve("GET", "/version")


def test_pufferpanel_without_an_address_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(homelab, "PUFFER_URL", "")
    monkeypatch.setattr(homelab, "PUFFER_CLIENT_ID", "id")
    monkeypatch.setattr(homelab, "PUFFER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(homelab, "_puffer_token", {"value": "", "expires": 0.0})
    with pytest.raises(RuntimeError, match="PUFFERPANEL_URL"):
        homelab._puffer_access_token()


def test_nextcloud_without_an_address_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(homelab, "NC_URL", "")
    monkeypatch.setattr(homelab, "NC_USER", "user")
    monkeypatch.setattr(homelab, "NC_PASSWORD", "password")
    with pytest.raises(RuntimeError, match="NEXTCLOUD_URL"):
        homelab._dav("PROPFIND", "/")


def test_uptime_kuma_without_an_address_falls_back_to_proxmox_status(monkeypatch):
    monkeypatch.setattr(homelab, "KUMA_URL", "")
    monkeypatch.setattr(homelab, "KUMA_KEY", "key")
    monkeypatch.setattr(homelab, "proxmox_status", lambda: "proxmox is fine")
    answer = homelab.service_status()
    assert "address and an API key" in answer
    assert answer.endswith("proxmox is fine")


def test_the_desktop_vm_without_an_address_is_reported_not_attempted(monkeypatch):
    monkeypatch.delenv("WORKSPACE_HOST", raising=False)
    monkeypatch.setattr(remote, "SETTINGS_FILE", "/nonexistent/workspace.env")
    monkeypatch.setattr(remote.subprocess, "run", lambda *args, **kwargs: pytest.fail("ssh must not be started without a host"))
    code, output = remote.ssh("true")
    assert code == 1
    assert b"WORKSPACE_HOST" in output


def test_the_desktop_vm_address_comes_from_its_own_settings_file(monkeypatch, tmp_path):
    settings = tmp_path / "workspace.env"
    settings.write_text("WORKSPACE_HOST=192.0.2.14\n")
    monkeypatch.delenv("WORKSPACE_HOST", raising=False)
    monkeypatch.setattr(remote, "SETTINGS_FILE", str(settings))
    try:
        assert remote.desktop_host() == "192.0.2.14"
    finally:
        os.environ.pop("WORKSPACE_HOST", None)
