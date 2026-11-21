import os

from process import browser
from process import own_browser
from process import screen_control
from process.tools import computer

VERSION_VARIABLE = "ARTY_VERSION"
CONNECTED = "connected"


def _entry(capability_id, available, **extra):
    return {"id": capability_id, "version": 1, "available": bool(available), **extra}


def listed():
    computer_on = computer.available()
    browsers = [{"name": name, "state": CONNECTED} for name in browser.connected_names()] if computer_on else []
    return [
        _entry("screen.look", computer_on and computer.can_see()),
        _entry("browser.open", computer_on and bool(browsers), browsers=browsers),
        _entry("browser.own", computer_on and own_browser.available()),
        _entry("screen.control", computer_on and screen_control.enabled() and computer.can_control()),
        _entry("quiz", computer_on and (own_browser.available() or bool(browsers))),
    ]


def status():
    return {"ok": True, "computer": computer.available(), "arty_desktop": os.environ.get(VERSION_VARIABLE, ""),
            "capabilities": listed()}
