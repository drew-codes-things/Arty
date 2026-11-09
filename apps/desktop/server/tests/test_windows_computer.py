import os

import pytest
from PIL import Image

from process.tools import computer, session, shell, win_apps, win_desktop


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(shell.sys, "platform", "win32")
    monkeypatch.setattr(computer, "_has_pillow", lambda: True)
    monkeypatch.setattr(computer, "_guard_front", lambda: None)
    monkeypatch.setattr(session, "front_window", lambda: ("notepad", "Untitled - Notepad"))


def test_windows_needs_no_extra_tools_to_see_or_control(windows):
    assert session.kind() == "windows"
    assert computer._input_tool() == "windows"
    assert computer.can_see() and computer.can_control()
    status = computer.status()
    assert status["session"] == "windows" and status["missing"] == []


def test_without_pillow_windows_says_what_is_missing(windows, monkeypatch):
    monkeypatch.setattr(computer, "_has_pillow", lambda: False)
    assert not computer.can_see()
    assert "Pillow" in computer.why_no_screen()


def test_a_click_goes_to_the_windows_backend(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(win_desktop, "click", lambda x, y, right=False, double=False: calls.append((x, y, right, double)))
    answer = computer.computer_click(x=10, y=20, button="right", double=True)
    assert calls == [(10.0, 20.0, True, True)]
    assert answer.startswith("Clicked (10, 20)")


def test_typing_goes_to_the_windows_backend(windows, monkeypatch):
    typed = []
    monkeypatch.setattr(win_desktop, "type_text", lambda text: typed.append(text) or [])
    answer = computer.computer_type("hello")
    assert typed == ["hello"]
    assert "Typed 5 characters into notepad: Untitled - Notepad" in answer


def test_key_combinations_go_to_the_windows_backend(windows, monkeypatch):
    pressed = []
    monkeypatch.setattr(win_desktop, "press_keys", lambda modifiers, key: pressed.append((modifiers, key)))
    assert computer.computer_key("ctrl+shift+s") == "Pressed ctrl+shift+s."
    assert pressed == [(["ctrl", "shift"], "s")]


def test_a_key_windows_does_not_know_is_explained(windows, monkeypatch):
    monkeypatch.setattr(win_desktop, "press_keys", lambda *args: pytest.fail("nothing should be sent"))
    assert "Don't know" in computer.computer_key("ctrl+nope")


def test_scrolling_goes_to_the_windows_backend(windows, monkeypatch):
    scrolled = []
    monkeypatch.setattr(win_desktop, "scroll", lambda direction, amount: scrolled.append((direction, amount)))
    assert computer.computer_scroll("down", 3) == "Scrolled down."
    assert scrolled == [("down", 3)]


def test_a_screenshot_is_taken_with_the_windows_backend(windows, monkeypatch):
    monkeypatch.setattr(win_desktop, "capture", lambda path: Image.new("RGB", (200, 100), "red").save(path))
    image, width, height = computer.screenshot()
    assert image and (width, height) == (200, 100)


def test_the_windows_desktop_is_described_through_the_backend(monkeypatch):
    monkeypatch.setattr(shell.sys, "platform", "win32")
    monkeypatch.setattr(win_desktop, "front_window", lambda: ("chrome", "Mail"))
    monkeypatch.setattr(win_desktop, "window_titles", lambda: {"chrome": ["Mail"], "notepad": ["a.txt"]})
    assert session.front_window() == ("chrome", "Mail")
    assert session.window_titles() == {"chrome": ["Mail"], "notepad": ["a.txt"]}


def make_start_menu(root, names):
    for name in names:
        shortcut = root / "Programs" / f"{name}.lnk"
        shortcut.parent.mkdir(parents=True, exist_ok=True)
        shortcut.write_text("")
    return root / "Programs"


def test_installed_apps_come_from_the_start_menu_without_helpers_and_uninstallers(tmp_path):
    folder = make_start_menu(tmp_path, ["Google Chrome", "Notepad++", "Uninstall Notepad++", "Chrome Help", "Visit our website"])
    names = sorted(entry["name"] for entry in win_apps.installed_apps([folder]).values())
    assert names == ["Google Chrome", "Notepad++"]


def test_an_app_is_found_by_a_partial_name_on_windows(windows, monkeypatch, tmp_path):
    folder = make_start_menu(tmp_path, ["Google Chrome", "Google Earth Pro", "Notepad++"])
    monkeypatch.setattr(win_apps, "start_menu_folders", lambda: [folder])
    assert computer._find_app("chrome")["name"] == "Google Chrome"
    assert computer._find_app("notepad++")["name"] == "Notepad++"
    assert computer._find_app("nothing like it") is None


def test_opening_an_installed_app_uses_its_start_menu_shortcut(windows, monkeypatch, tmp_path):
    folder = make_start_menu(tmp_path, ["Google Chrome"])
    monkeypatch.setattr(win_apps, "start_menu_folders", lambda: [folder])
    launched = []
    monkeypatch.setattr(win_apps, "launch", lambda entry, url="": launched.append((entry["name"], url)) or True)
    monkeypatch.setattr(computer, "_bring_forward", lambda app, wait=10.0, settle=0.0: True)
    assert "is open and in front" in computer.computer_open("chrome")
    assert launched == [("Google Chrome", "")]


def test_a_program_that_is_not_in_the_start_menu_is_started_by_name(windows, monkeypatch, tmp_path):
    monkeypatch.setattr(win_apps, "start_menu_folders", lambda: [tmp_path])
    started = []
    monkeypatch.setattr(session, "have", lambda command: command == "notepad")
    monkeypatch.setattr(session, "spawn", lambda command: started.append(command) or True)
    monkeypatch.setattr(computer, "_bring_forward", lambda app, wait=10.0, settle=0.0: True)
    assert "is open" in computer.computer_open("notepad")
    assert started == [["notepad"]]


def test_a_link_opens_in_the_default_browser_on_windows(windows, monkeypatch, tmp_path):
    monkeypatch.setattr(win_apps, "start_menu_folders", lambda: [tmp_path])
    opened = []
    monkeypatch.setattr(os, "startfile", lambda target: opened.append(target), raising=False)
    assert computer.computer_open(url="https://example.com") == "Opened https://example.com."
    assert opened == ["https://example.com"]


def test_a_link_in_a_named_browser_is_passed_to_that_apps_program(windows, monkeypatch, tmp_path):
    folder = make_start_menu(tmp_path, ["Google Chrome"])
    monkeypatch.setattr(win_apps, "start_menu_folders", lambda: [folder])
    monkeypatch.setattr(win_apps, "shortcut_target", lambda path: "C:\\Chrome\\chrome.exe")
    started = []
    monkeypatch.setattr(session, "spawn", lambda command: started.append(command) or True)
    assert computer.computer_open(app="chrome", url="https://example.com") == "Opened https://example.com in Google Chrome."
    assert started == [["C:\\Chrome\\chrome.exe", "https://example.com"]]
