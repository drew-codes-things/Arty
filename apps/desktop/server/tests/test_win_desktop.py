import sys

import pytest

from process.tools import win_desktop


@pytest.mark.parametrize("key, code", [
    ("a", 0x41), ("z", 0x5A), ("5", 0x35), ("0", 0x30),
    ("return", 0x0D), ("enter", 0x0D), ("tab", 0x09), ("space", 0x20), ("backspace", 0x08), ("delete", 0x08),
    ("forwarddelete", 0x2E), ("escape", 0x1B), ("esc", 0x1B), ("home", 0x24), ("end", 0x23),
    ("pageup", 0x21), ("pagedown", 0x22), ("left", 0x25), ("up", 0x26), ("right", 0x27), ("down", 0x28),
    ("f1", 0x70), ("f12", 0x7B),
    ("=", 0xBB), ("-", 0xBD), ("[", 0xDB), ("]", 0xDD), ("/", 0xBF), (".", 0xBE), (",", 0xBC), ("`", 0xC0),
])
def test_keys_have_windows_virtual_key_codes(key, code):
    assert win_desktop.virtual_key(key) == code


@pytest.mark.parametrize("key", ["", "nope", "f13", "ctrl", "ab"])
def test_unknown_keys_have_no_code(key):
    assert win_desktop.virtual_key(key) is None


def test_modifiers_have_codes():
    assert [win_desktop.MODIFIERS[name] for name in ("shift", "ctrl", "alt", "win")] == [0x10, 0x11, 0x12, 0x5B]
    assert win_desktop.MODIFIERS["control"] == win_desktop.MODIFIERS["ctrl"]
    assert win_desktop.MODIFIERS["super"] == win_desktop.MODIFIERS["win"]


def test_plain_text_is_typed_as_characters():
    assert win_desktop.text_events("hi") == [("unicode", 104), ("unicode", 105)]


def test_new_lines_and_tabs_are_real_keys_not_characters():
    assert win_desktop.text_events("a\nb") == [("unicode", 97), ("vk", 0x0D), ("unicode", 98)]
    assert win_desktop.text_events("a\r\nb") == [("unicode", 97), ("vk", 0x0D), ("unicode", 98)]
    assert win_desktop.text_events("\t") == [("vk", 0x09)]


def test_characters_outside_the_basic_plane_are_sent_as_two_halves():
    assert win_desktop.text_events("\U0001F600") == [("unicode", 0xD83D), ("unicode", 0xDE00)]


def test_no_text_means_no_events():
    assert win_desktop.text_events("") == []


@pytest.mark.parametrize("direction, amount, expected", [
    ("down", 3, ("vertical", -360)), ("up", 2, ("vertical", 240)),
    ("left", 1, ("horizontal", -120)), ("right", 1, ("horizontal", 120)),
])
def test_scrolling_is_in_wheel_notches(direction, amount, expected):
    assert win_desktop.wheel(direction, amount) == expected


def test_windows_are_grouped_by_app_in_the_order_they_were_seen():
    seen = [("notepad", "a.txt"), ("chrome", "Mail"), ("notepad", "b.txt")]
    assert win_desktop.group_by_app(seen) == {"notepad": ["a.txt", "b.txt"], "chrome": ["Mail"]}


@pytest.mark.skipif(sys.platform == "win32", reason="checks the refusal on other systems")
@pytest.mark.parametrize("call", [
    lambda: win_desktop.capture("x.png"),
    lambda: win_desktop.click(1, 1),
    lambda: win_desktop.type_text("x"),
    lambda: win_desktop.front_window(),
])
def test_the_real_calls_refuse_to_run_off_windows(call):
    with pytest.raises(RuntimeError, match="only works on Windows"):
        call()


def test_the_alt_tap_that_wins_focus_has_a_key_inside_so_it_never_opens_a_menu():
    keys = win_desktop.ALT_TAP_KEYS
    assert keys[0] == (win_desktop.VK_MENU, 0) and keys[-1] == (win_desktop.VK_MENU, win_desktop.KEYEVENTF_KEYUP)
    inside = keys[1:-1]
    assert len(inside) == 2 and all(code == win_desktop.VK_UNASSIGNED for code, _ in inside)
    assert [flags for _, flags in inside] == [0, win_desktop.KEYEVENTF_KEYUP]
