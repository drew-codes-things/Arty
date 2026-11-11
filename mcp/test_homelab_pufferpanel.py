import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

pytest.importorskip("mcp.server.fastmcp")
import homelab_server as homelab

SERVERS = [
    {"id": "b50272d1", "name": "A+A", "port": 25565},
    {"id": "142c5b72", "name": "A+A+K", "port": 25567},
    {"id": "38398577", "name": "Survival", "port": 25568},
    {"id": "a4e35290", "name": "creative-world", "port": 25566},
]


@pytest.mark.parametrize("wanted, expected", [
    ("Survival", "38398577"),
    ("a+a", "b50272d1"),
    ("142c", "142c5b72"),
    ("creative", "a4e35290"),
])
def test_find_puffer_server_picks_one(wanted, expected):
    assert homelab.find_puffer_server(SERVERS, wanted)["id"] == expected


def test_find_puffer_server_explains_ambiguity_and_misses():
    assert "more than one" in homelab.find_puffer_server(SERVERS, "+a")
    assert "no game server called 'skyblock'" in homelab.find_puffer_server(SERVERS, "skyblock")
    assert homelab.find_puffer_server(SERVERS, "  ") == "Say which server."


def test_power_posts_the_action_for_the_named_server(monkeypatch):
    calls = []
    monkeypatch.setattr(homelab, "_puffer_servers", lambda: SERVERS)
    monkeypatch.setattr(homelab, "_puffer", lambda method, path: calls.append((method, path)))
    result = homelab.pufferpanel_power("Survival", "Stop")
    assert calls == [("POST", "/servers/38398577/stop")]
    assert "stop Survival" in result


def test_power_rejects_other_actions_without_calling_the_panel(monkeypatch):
    monkeypatch.setattr(homelab, "_puffer_servers", lambda: pytest.fail("should not reach the panel"))
    assert "must be one of" in homelab.pufferpanel_power("Survival", "kill")


def test_power_reports_panel_errors(monkeypatch):
    monkeypatch.setattr(homelab, "_puffer_servers", lambda: SERVERS)

    def refuse(method, path):
        raise RuntimeError("PufferPanel said 403: forbidden")

    monkeypatch.setattr(homelab, "_puffer", refuse)
    assert homelab.pufferpanel_power("Survival", "start").startswith("Couldn't start Survival: PufferPanel said 403")


def test_missing_credentials_are_reported(monkeypatch):
    monkeypatch.setattr(homelab, "PUFFER_CLIENT_ID", "")
    monkeypatch.setitem(homelab._puffer_token, "value", "")
    assert "isn't set up" in homelab.pufferpanel_servers()


def test_list_shows_state_for_each_server(monkeypatch):
    monkeypatch.setattr(homelab, "_puffer_servers", lambda: SERVERS[:2])
    monkeypatch.setattr(homelab, "_puffer_state", lambda server_id: "running" if server_id == "b50272d1" else "stopped")
    assert homelab.pufferpanel_servers().splitlines() == [
        "A+A (id b50272d1, port 25565): running",
        "A+A+K (id 142c5b72, port 25567): stopped",
    ]
