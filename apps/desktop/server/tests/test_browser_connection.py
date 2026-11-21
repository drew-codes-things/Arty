import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from process import browser, browser_connection, browsers_found

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
WINDOW = {"X-Arty-Token": arty_server.TOKEN}


@pytest.fixture
def settings(monkeypatch):
    values = {}
    monkeypatch.setattr(browser_connection.cfg, "setting", lambda section, key, default=None: values.get((section, key), default))
    monkeypatch.setattr(browser_connection.cfg, "set_value", lambda section, key, value: values.__setitem__((section, key), value))
    return values


@pytest.fixture
def home(tmp_path):
    version = tmp_path / "home" / ".config" / "google-chrome" / "Default" / "Extensions" / browsers_found.EXTENSION_ID / "1.7_0"
    version.mkdir(parents=True)
    (version / "manifest.json").write_text("{}")
    snap = tmp_path / "home" / "snap" / "chromium" / "common" / "chromium" / "Default" / "Extensions" / browsers_found.EXTENSION_ID / "1"
    snap.mkdir(parents=True)
    (snap / "manifest.json").write_text("{}")
    return tmp_path / "home"


def dependencies(home):
    return {"find": lambda: browsers_found.browsers_with_extension(home=home, platform="linux"),
            "places": lambda: browsers_found.places_here(home=home, platform="linux"),
            "python": lambda: "/usr/bin/python3"}


def test_report_says_which_browsers_connect_and_which_are_open(settings, home, tmp_path, monkeypatch, linux):
    monkeypatch.setattr(browser, "connected_names", lambda: ["Chrome 2"])
    report = browser_connection.found_report(tmp_path / "data", **dependencies(home))
    assert report == {"automatic": True, "browsers": [
        {"name": "Chrome", "packaging": "native", "profiles": 1, "native": "on", "connected": True},
        {"name": "Chromium", "packaging": "snap", "profiles": 1, "native": "unavailable", "connected": False}]}
    assert (home / ".config" / "google-chrome" / "NativeMessagingHosts" / "app.arty.desktop.json").exists()


def test_switching_it_off_removes_the_registration(settings, home, tmp_path, monkeypatch, linux):
    monkeypatch.setattr(browser, "connected_names", lambda: [])
    browser_connection.found_report(tmp_path / "data", **dependencies(home))
    report = browser_connection.set_automatic(False, tmp_path / "data", **dependencies(home))
    assert report["automatic"] is False
    assert report["browsers"][0]["native"] == "off"
    assert not (home / ".config" / "google-chrome" / "NativeMessagingHosts" / "app.arty.desktop.json").exists()
    assert browser_connection.set_automatic(True, tmp_path / "data", **dependencies(home))["browsers"][0]["native"] == "on"


@pytest.mark.parametrize("headers", [{}, {"Origin": "chrome-extension://lepggpdmafbgbgfgacpglpjelmiofkli"},
                                     {"Origin": "chrome-extension://lepggpdmafbgbgfgacpglpjelmiofkli",
                                      "X-Arty-Pairing": "0" * 32}, {"X-Arty-Link": "x" * 40}])
def test_only_the_arty_window_can_see_or_change_browsers(headers, monkeypatch):
    monkeypatch.setattr(browser_connection, "found_report", lambda: {"automatic": True, "browsers": []})
    monkeypatch.setattr(browser_connection, "set_automatic", lambda enabled: {"automatic": enabled, "browsers": []})
    assert client.get("/browser/found", headers=headers).status_code == 403
    assert client.post("/browser/found/automatic", json={"enabled": False}, headers=headers).status_code == 403


def test_the_arty_window_can(monkeypatch):
    monkeypatch.setattr(browser_connection, "found_report", lambda: {"automatic": True, "browsers": []})
    monkeypatch.setattr(browser_connection, "set_automatic", lambda enabled: {"automatic": enabled, "browsers": []})
    assert client.get("/browser/found", headers=WINDOW).json() == {"automatic": True, "browsers": []}
    assert client.post("/browser/found/automatic", json={"enabled": False}, headers=WINDOW).json()["automatic"] is False
    assert client.post("/browser/found/automatic", json={"enabled": False, "x": 1}, headers=WINDOW).status_code == 422


def test_the_connect_button_turns_connecting_by_itself_on_and_needs_the_window_token(monkeypatch):
    from fastapi.testclient import TestClient

    import arty_server

    asked = []
    monkeypatch.setattr(browser_connection, "set_automatic", lambda enabled: asked.append(enabled) or {"automatic": enabled, "browsers": []})
    web = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
    assert web.post("/browser/found/connect").status_code == 403
    answer = web.post("/browser/found/connect", headers={"X-Arty-Token": arty_server.TOKEN})
    assert answer.json() == {"automatic": True, "browsers": []} and asked == [True]
