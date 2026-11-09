import json

import pytest

from process.tools import computer, context, desktop, registry, shell, win_desktop, win_system

NETSH_CONNECTED = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    State                  : connected
    SSID                   : Home Network
    BSSID                  : aa:bb:cc:dd:ee:ff
"""

NETSH_OFF = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : disconnected
"""


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(shell.sys, "platform", "win32")


def test_snapping_takes_half_of_the_work_area_each_side():
    work = (0, 0, 1920, 1040)
    assert win_system.snap_rect(work, "left") == (0, 0, 960, 1040)
    assert win_system.snap_rect(work, "right") == (960, 0, 960, 1040)
    assert win_system.snap_rect((100, 20, 1001, 500), "right") == (600, 20, 501, 500)


def test_the_wifi_name_and_state_are_read_from_netsh():
    assert win_system.parse_wlan(NETSH_CONNECTED) == ("connected", "Home Network")
    assert win_system.parse_wlan(NETSH_OFF) == ("disconnected", "")
    assert win_system.parse_wlan("There is no wireless interface on the system.") == (None, "")


@pytest.mark.parametrize("ac, flag, percent, expected", [
    (1, 8, 80, "battery 80% (charging)"),
    (1, 1, 100, "battery 100% (plugged in)"),
    (0, 1, 55, "battery 55% (on battery)"),
    (0, 4, 8, "battery 8% (on battery, low)"),
    (1, 128, 255, None),
    (0, 255, 255, None),
])
def test_battery_is_described_in_plain_words(ac, flag, percent, expected):
    assert win_system.battery_text(ac, flag, percent) == expected


def test_cpu_load_is_the_share_of_time_not_idle():
    assert win_system.cpu_percent((100, 500, 300), (120, 600, 400)) == 90
    assert win_system.cpu_percent((100, 500, 300), (100, 500, 300)) == 0


def test_the_volume_script_sets_what_was_asked():
    script = win_system.volume_script(level=30, change=None, mute=None)
    assert "[Audio]::Volume = 0.3" in script
    assert "[Audio]::Mute =" not in script
    changed = win_system.volume_script(level=None, change=-10, mute=True)
    assert "-0.1" in changed and "[Audio]::Mute = $true" in changed
    assert "[Audio]::Mute = $false" in win_system.volume_script(level=None, change=None, mute=False)


def test_the_volume_level_is_kept_between_zero_and_a_hundred():
    assert "[Audio]::Volume = 1.0" in win_system.volume_script(level=250, change=None, mute=None)
    assert "[Audio]::Volume = 0.0" in win_system.volume_script(level=-5, change=None, mute=None)


@pytest.mark.parametrize("kind, state, fragment", [("WiFi", "on", "SetStateAsync"), ("Bluetooth", "off", "Bluetooth")])
def test_the_radio_script_names_the_radio_and_sets_the_state(kind, state, fragment):
    script = win_system.radio_script(kind, state)
    assert f"'{kind}'" in script and fragment in script


def test_the_radio_script_only_reads_the_state_for_status():
    assert "SetStateAsync" not in win_system.radio_script("WiFi", "status")


def fake_powershell(monkeypatch, answer):
    calls = []
    monkeypatch.setattr(win_system, "powershell", lambda script, env=None, timeout=20: calls.append((script, env)) or answer)
    return calls


def test_the_volume_tool_reports_what_windows_says(windows, monkeypatch):
    calls = fake_powershell(monkeypatch, json.dumps({"level": 20, "muted": False}))
    assert desktop.system_volume(level=20) == "Volume is 20%."
    assert "[Audio]::Volume = 0.2" in calls[0][0]
    fake_powershell(monkeypatch, json.dumps({"level": 0, "muted": True}))
    assert desktop.system_volume(mute=True) == "Volume is 0% (muted)."


def test_the_volume_tool_explains_when_there_is_no_speaker(windows, monkeypatch):
    def broken(script, env=None, timeout=20):
        raise win_system.WindowsToolError("Element not found")

    monkeypatch.setattr(win_system, "powershell", broken)
    assert desktop.system_volume().startswith("Couldn't change the volume:")


def test_brightness_is_set_and_read_through_wmi(windows, monkeypatch):
    calls = fake_powershell(monkeypatch, "60")
    assert desktop.system_brightness(60) == "Screen brightness is 60%."
    assert "WmiSetBrightness" in calls[0][0]


def test_brightness_says_so_when_the_screen_cannot_be_changed(windows, monkeypatch):
    def broken(script, env=None, timeout=20):
        raise win_system.WindowsToolError("Not supported")

    monkeypatch.setattr(win_system, "powershell", broken)
    assert "desktop monitors usually can't" in desktop.system_brightness(50)


def test_wifi_status_names_the_network(windows, monkeypatch):
    monkeypatch.setattr(win_system, "powershell", lambda script, env=None, timeout=20: json.dumps({"present": True, "state": "On"}))
    monkeypatch.setattr(win_system, "run_command", lambda argv, timeout=20: NETSH_CONNECTED)
    assert desktop.system_wifi() == "Wi-Fi is on, connected to Home Network."


def test_wifi_off_is_reported_after_switching_it(windows, monkeypatch):
    calls = fake_powershell(monkeypatch, json.dumps({"present": True, "state": "Off"}))
    assert desktop.system_wifi("off") == "Wi-Fi is off."
    assert "SetStateAsync" in calls[0][0]


def test_a_computer_without_wifi_says_so(windows, monkeypatch):
    fake_powershell(monkeypatch, json.dumps({"present": False, "state": ""}))
    assert desktop.system_wifi() == "This computer has no Wi-Fi."


def test_bluetooth_status_lists_connected_devices(windows, monkeypatch):
    answers = iter([json.dumps({"present": True, "state": "On"}), "Headphones\nMouse"])
    monkeypatch.setattr(win_system, "powershell", lambda script, env=None, timeout=20: next(answers))
    assert desktop.system_bluetooth() == "Bluetooth is on, connected to Headphones, Mouse."


def test_a_computer_without_bluetooth_says_so(windows, monkeypatch):
    fake_powershell(monkeypatch, json.dumps({"present": False, "state": ""}))
    assert desktop.system_bluetooth() == "This computer has no Bluetooth."


def test_windows_are_listed_with_the_front_one_marked(windows, monkeypatch):
    monkeypatch.setattr(win_desktop, "front_window", lambda: ("notepad", "Untitled - Notepad"))
    monkeypatch.setattr(win_desktop, "open_windows", lambda: [("chrome", "Mail"), ("notepad", "Untitled - Notepad")])
    listed = desktop.desktop_windows()
    assert "- chrome: Mail" in listed and "* notepad: Untitled - Notepad" in listed


def test_no_open_windows_is_said_plainly(windows, monkeypatch):
    monkeypatch.setattr(win_desktop, "front_window", lambda: None)
    monkeypatch.setattr(win_desktop, "open_windows", lambda: [])
    assert desktop.desktop_windows() == "No windows are open."


@pytest.fixture
def one_window(windows, monkeypatch):
    calls = []
    monkeypatch.setattr(win_desktop, "find_window", lambda name="": (7, "notepad", "Untitled - Notepad") if "note" in name or not name else None)
    monkeypatch.setattr(win_desktop, "focus_window", lambda hwnd: calls.append(("focus", hwnd)))
    monkeypatch.setattr(win_desktop, "show_window", lambda hwnd, state: calls.append(("show", hwnd, state)))
    monkeypatch.setattr(win_desktop, "close_window", lambda hwnd: calls.append(("close", hwnd)))
    monkeypatch.setattr(win_desktop, "window_rect", lambda hwnd: (100, 200, 640, 480))
    monkeypatch.setattr(win_desktop, "work_area", lambda: (0, 0, 1920, 1040))
    monkeypatch.setattr(win_desktop, "place_window", lambda hwnd, x, y, w, h: calls.append(("place", hwnd, x, y, w, h)))
    return calls


@pytest.mark.parametrize("action, expected_call", [
    ("focus", ("focus", 7)),
    ("minimise", ("show", 7, win_desktop.SW_MINIMIZE)),
    ("maximise", ("show", 7, win_desktop.SW_MAXIMIZE)),
    ("restore", ("show", 7, win_desktop.SW_RESTORE)),
    ("close", ("close", 7)),
    ("snap_left", ("place", 7, 0, 0, 960, 1040)),
    ("snap_right", ("place", 7, 960, 0, 960, 1040)),
])
def test_window_actions_go_to_the_right_windows_call(one_window, action, expected_call):
    assert desktop.desktop_window(action, "notepad") == f"Done: {action.replace('_', ' ')} on notepad (Untitled - Notepad)."
    assert one_window == [expected_call]


def test_a_window_can_be_moved_and_resized_keeping_the_other_values(one_window):
    desktop.desktop_window("move", "notepad", x=10, y=20)
    desktop.desktop_window("resize", "notepad", width=800, height=600)
    assert one_window == [("place", 7, 10, 20, 640, 480), ("place", 7, 100, 200, 800, 600)]


def test_a_window_that_is_not_there_is_explained(one_window):
    assert "no window matches" in desktop.desktop_window("focus", "spreadsheet")
    assert one_window == []


@pytest.mark.parametrize("action", ["fullscreen", "to_desktop"])
def test_actions_windows_cannot_do_from_here_say_so(one_window, action):
    assert "isn't available on Windows" in desktop.desktop_window(action, "notepad", desktop=2)
    assert one_window == []


def test_switching_virtual_desktops_says_it_is_not_available(windows):
    assert "isn't available on Windows" in desktop.desktop_switch(2)


@pytest.mark.parametrize("action, key", [("next", 0xB0), ("previous", 0xB1), ("stop", 0xB2), ("play_pause", 0xB3), ("pause", 0xB3)])
def test_media_actions_press_the_media_keys(windows, monkeypatch, action, key):
    pressed = []
    monkeypatch.setattr(win_desktop, "press_media_key", lambda code: pressed.append(code))
    assert desktop.system_media(action).startswith("Pressed")
    assert pressed == [key]


def test_what_is_playing_cannot_be_read_on_windows(windows):
    assert "can't read what is playing" in desktop.system_media("status")


def test_the_screen_is_locked_through_windows(windows, monkeypatch):
    locked = []
    monkeypatch.setattr(win_desktop, "lock_screen", lambda: locked.append(True))
    assert desktop.system_lock() == "Locked the screen."
    assert locked == [True]


def test_do_not_disturb_switches_toast_notifications(windows, monkeypatch):
    states = []
    monkeypatch.setattr(win_system, "set_toasts", lambda enabled: states.append(enabled))
    assert desktop.system_do_not_disturb(True) == "Do not disturb is on: notifications are held back."
    assert desktop.system_do_not_disturb(False) == "Do not disturb is off."
    assert states == [False, True]


def test_a_notification_is_shown_with_the_text_passed_safely(windows, monkeypatch):
    calls = fake_powershell(monkeypatch, "")
    assert desktop.desktop_notify("Done", "It's finished & ready") == "Notification shown."
    script, env = calls[0]
    assert env == {"ARTY_TITLE": "Done", "ARTY_MESSAGE": "It's finished & ready"}
    assert "It's finished" not in script


def test_the_computer_status_is_one_plain_sentence(windows, monkeypatch):
    monkeypatch.setattr(win_system, "volume_state", lambda: (35, False))
    monkeypatch.setattr(win_system, "battery_status", lambda: "battery 80% (charging)")
    monkeypatch.setattr(win_system, "cpu_load", lambda: 12)
    monkeypatch.setattr(win_system, "memory_status", lambda: (41, 16))
    monkeypatch.setattr(win_system, "free_disk_gb", lambda: 120)
    assert desktop.system_status() == (
        "This computer: volume 35%, battery 80% (charging), CPU load 12%, memory 41% used of 16 GB, 120 GB free disk.")


def test_the_status_leaves_out_what_this_computer_does_not_have(windows, monkeypatch):
    def no_speaker():
        raise win_system.WindowsToolError("no speaker")

    monkeypatch.setattr(win_system, "volume_state", no_speaker)
    monkeypatch.setattr(win_system, "battery_status", lambda: None)
    monkeypatch.setattr(win_system, "cpu_load", lambda: 3)
    monkeypatch.setattr(win_system, "memory_status", lambda: (50, 8))
    monkeypatch.setattr(win_system, "free_disk_gb", lambda: 10)
    assert desktop.system_status() == "This computer: CPU load 3%, memory 50% used of 8 GB, 10 GB free disk."


def test_the_desktop_tools_are_available_on_windows(windows):
    assert desktop.available()


def test_the_battery_note_reads_windows_power_status(windows, monkeypatch):
    monkeypatch.setattr(win_system, "battery_levels", lambda: (12, "Discharging"))
    assert context.battery() == "on battery, down to 12% and not charging"
    monkeypatch.setattr(win_system, "battery_levels", lambda: None)
    assert context.battery() == ""


def test_the_clipboard_is_read_through_windows(windows, monkeypatch):
    monkeypatch.setattr(win_desktop, "get_clipboard_text", lambda: " copied text ")
    assert registry._clipboard_available()
    assert registry.read_clipboard() == "The clipboard contains:\ncopied text"


def test_a_busy_windows_clipboard_is_reported(windows, monkeypatch):
    def busy():
        raise RuntimeError("the clipboard is busy")

    monkeypatch.setattr(win_desktop, "get_clipboard_text", busy)
    assert registry.read_clipboard().startswith("Could not read the clipboard")


def test_links_open_through_windows(windows, monkeypatch):
    opened = []
    monkeypatch.setattr(computer, "open_default", lambda url: opened.append(url) or True)
    assert registry._open_url_available()
    assert registry.open_url("https://example.com/a") == "Opened example.com."
    assert opened == ["https://example.com/a"]
