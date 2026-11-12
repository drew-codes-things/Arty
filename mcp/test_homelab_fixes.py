import importlib.util
import inspect
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

import pytest

pytest.importorskip("mcp.server.fastmcp")
import homelab_server as homelab

RELEASE_BUILDER = Path(__file__).resolve().parents[1] / "downloads" / "build_release.py"

GUESTS = [
    {"vmid": 103, "kind": "lxc", "name": "puffer", "status": "stopped", "ip": "192.0.2.20", "mac": "aa:bb:cc:dd:ee:01"},
    {"vmid": 109, "kind": "lxc", "name": "voice", "status": "running", "ip": None, "mac": None},
]


@pytest.fixture
def guests(monkeypatch):
    monkeypatch.setattr(homelab, "_guests", lambda: GUESTS)
    monkeypatch.setattr(homelab, "_port_open", lambda address, port: False)
    monkeypatch.setattr(homelab, "_mac_answering", lambda address: None)


def test_a_monitor_by_host_name_is_matched_through_its_address(guests, monkeypatch):
    monkeypatch.setattr(homelab, "addresses_of", lambda name: ["192.0.2.20"])
    result = json.loads(homelab.diagnose("games.example.com", 443))
    assert result["fix"] == {"action": "start", "vmid": 103, "name": "CT 103 puffer"}


def test_a_monitor_by_host_name_is_matched_by_the_guest_name(guests, monkeypatch):
    monkeypatch.setattr(homelab, "addresses_of", lambda name: [])
    result = json.loads(homelab.diagnose("puffer.home.example", 8080))
    assert result["fix"]["vmid"] == 103


def test_a_guest_without_a_fixed_address_is_matched_by_name(guests, monkeypatch):
    monkeypatch.setattr(homelab, "addresses_of", lambda name: ["198.51.100.7"])
    result = json.loads(homelab.diagnose("voice.example.net", 8780))
    assert result["fix"] == {"action": "restart", "vmid": 109, "name": "CT 109 voice"}


def test_an_unknown_host_is_still_not_a_guest(guests, monkeypatch):
    monkeypatch.setattr(homelab, "addresses_of", lambda name: ["198.51.100.7"])
    result = json.loads(homelab.diagnose("shop.example.org", 443))
    assert result["fix"] is None and "isn't one of the Proxmox guests" in result["summary"]


def test_unreachable_proxmox_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(homelab, "PVE_URL", "https://pve.example:8006")
    monkeypatch.setattr(homelab, "PVE_TOKEN", "token")
    monkeypatch.setattr(homelab, "PVE_CA", __file__)
    monkeypatch.setattr(homelab.ssl, "create_default_context", lambda cafile=None: None)

    def refuse(*args, **kwargs):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(homelab.urllib.request, "urlopen", refuse)
    with pytest.raises(RuntimeError, match="couldn't reach Proxmox"):
        homelab._pve("GET", "/cluster/resources")
    assert "couldn't reach Proxmox" in homelab.proxmox_status()


def test_a_missing_proxmox_certificate_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(homelab, "PVE_URL", "https://pve.example:8006")
    monkeypatch.setattr(homelab, "PVE_TOKEN", "token")
    monkeypatch.setattr(homelab, "PVE_CA", "/nonexistent/pve-root-ca.pem")
    with pytest.raises(RuntimeError, match="certificate"):
        homelab._pve("GET", "/cluster/resources")


def test_proxmox_timeout_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(homelab, "PVE_URL", "https://pve.example:8006")
    monkeypatch.setattr(homelab, "PVE_TOKEN", "token")
    monkeypatch.setattr(homelab.ssl, "create_default_context", lambda cafile=None: None)

    def slow(*args, **kwargs):
        raise TimeoutError("timed out")
    monkeypatch.setattr(homelab.urllib.request, "urlopen", slow)
    with pytest.raises(RuntimeError, match="couldn't reach Proxmox"):
        homelab._pve("GET", "/version")


def test_unreachable_pufferpanel_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(homelab, "PUFFER_URL", "https://puffer.example")
    monkeypatch.setattr(homelab, "_puffer_access_token", lambda: "token")

    def refuse(*args, **kwargs):
        raise urllib.error.URLError("No route to host")
    monkeypatch.setattr(homelab.urllib.request, "urlopen", refuse)
    assert "couldn't reach PufferPanel" in homelab.pufferpanel_servers()


def test_unreachable_pufferpanel_login_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(homelab, "PUFFER_URL", "https://puffer.example")
    monkeypatch.setattr(homelab, "PUFFER_CLIENT_ID", "id")
    monkeypatch.setattr(homelab, "PUFFER_CLIENT_SECRET", "secret")
    monkeypatch.setitem(homelab._puffer_token, "value", "")

    def refuse(*args, **kwargs):
        raise urllib.error.URLError("No route to host")
    monkeypatch.setattr(homelab.urllib.request, "urlopen", refuse)
    with pytest.raises(RuntimeError, match="couldn't reach PufferPanel"):
        homelab._puffer_access_token()


def test_unreachable_nextcloud_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(homelab, "NC_URL", "https://cloud.example")
    monkeypatch.setattr(homelab, "NC_USER", "arty")
    monkeypatch.setattr(homelab, "NC_PASSWORD", "pw")

    def refuse(*args, **kwargs):
        raise TimeoutError("timed out")
    monkeypatch.setattr(homelab.urllib.request, "urlopen", refuse)
    assert "couldn't reach Nextcloud" in homelab.nextcloud_list("")


def test_power_when_proxmox_is_down_says_so(monkeypatch):
    def down():
        raise RuntimeError("couldn't reach Proxmox: refused")
    monkeypatch.setattr(homelab, "_guests", down)
    assert "couldn't reach Proxmox" in homelab.start_guest(103)


def test_ping_never_gets_an_option_for_an_address(monkeypatch):
    monkeypatch.setattr(homelab.subprocess, "run", lambda *a, **k: pytest.fail("ping was run"))
    assert homelab._mac_answering("-f") is None
    assert homelab._mac_answering("host.example; rm -rf /") is None


def test_a_slow_ping_is_not_a_crash(monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)
    monkeypatch.setattr(homelab.subprocess, "run", slow)
    assert homelab._mac_answering("192.0.2.20") is None


class FakeSnapshots:
    def __init__(self, names):
        self.snapshots = [{"name": name, "snaptime": number} for number, name in enumerate(names)]
        self.deleted = []

    def __call__(self, method, path, params=None):
        if method == "POST":
            self.snapshots.append({"name": params["snapname"], "snaptime": 100})
            return None
        if method == "DELETE":
            name = path.rsplit("/", 1)[-1]
            self.deleted.append(name)
            self.snapshots = [s for s in self.snapshots if s["name"] != name]
            return None
        return list(self.snapshots)


@pytest.mark.parametrize("kept, expected_left", [(0, 1), (1, 1), (3, 3)])
def test_snapshots_kept_is_honoured(monkeypatch, kept, expected_left):
    fake = FakeSnapshots(["arty_1", "arty_2", "arty_3", "arty_4", "manual"])
    monkeypatch.setattr(homelab, "_pve", fake)
    monkeypatch.setattr(homelab, "SNAPSHOTS_KEPT", kept)
    monkeypatch.setattr(homelab, "_node", lambda: "node1")
    result = homelab.snapshot_desktop("before an upgrade")
    assert result.startswith("snapshot arty_")
    left = [s["name"] for s in fake.snapshots if s["name"].startswith("arty_")]
    assert len(left) == expected_left
    assert "manual" not in fake.deleted
    assert left[-1].startswith("arty_2")


def test_node_comes_from_proxmox_when_not_set(monkeypatch):
    monkeypatch.setattr(homelab, "PVE_NODE", "")
    monkeypatch.setattr(homelab, "_pve", lambda method, path, params=None: [{"type": "storage"}, {"type": "node", "node": "pve-1"}])
    homelab._node_cache.clear()
    assert homelab._node() == "pve-1"


def test_set_node_wins(monkeypatch):
    monkeypatch.setattr(homelab, "PVE_NODE", "custom")
    monkeypatch.setattr(homelab, "_pve", lambda *a, **k: pytest.fail("asked Proxmox"))
    assert homelab._node() == "custom"


def owner_details() -> tuple:
    if not RELEASE_BUILDER.is_file():
        pytest.skip("the release builder, which lists the owner's details, is not in this copy")
    spec = importlib.util.spec_from_file_location("build_release", RELEASE_BUILDER)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder.OWNER_DETAILS


def test_no_owner_names_in_tool_text():
    source = inspect.getsource(homelab)
    assert [name for name, pattern in owner_details() if pattern.search(source)] == []


def test_nextcloud_upload_does_not_mix_error_output_into_the_file(monkeypatch):
    sent = []
    calls = []
    monkeypatch.setattr(homelab, "ssh", lambda command, **k: calls.append(k) or (0, b"file bytes"))
    monkeypatch.setattr(homelab, "_dav", lambda method, path, body=None, headers=None: sent.append((method, body)) or b"")
    homelab.nextcloud_upload("notes.txt")
    assert calls[0].get("merge_errors") is False
    assert ("PUT", b"file bytes") in sent


class TwoNodeCluster:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, params=None, *rest):
        self.calls.append((method, path))
        if path == "/cluster/resources":
            return [{"type": "lxc", "vmid": 201, "name": "web", "status": "running", "node": "pve-b"},
                    {"type": "qemu", "vmid": 301, "name": "desk", "status": "stopped", "node": "pve-c"}]
        return {"net0": "name=eth0,ip=192.0.2.5/24"}


def test_each_guest_is_reached_on_its_own_node(monkeypatch):
    cluster = TwoNodeCluster()
    monkeypatch.setattr(homelab, "_pve", cluster)
    monkeypatch.setattr(homelab, "_node", lambda: "pve-a")
    guests = homelab._guests()
    assert [guest["node"] for guest in guests] == ["pve-b", "pve-c"]
    assert ("GET", "/nodes/pve-b/lxc/201/config") in cluster.calls
    assert ("GET", "/nodes/pve-c/qemu/301/config") in cluster.calls
    homelab._power(301, "start")
    assert ("POST", "/nodes/pve-c/qemu/301/status/start") in cluster.calls
