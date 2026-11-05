"""Window/system tools: what they ask before acting, and how they read the desktop's answers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import notifications
from process.tools import desktop


def test_asks_before_closing_a_window_or_cutting_wifi():
    assert "unsaved" in desktop.needs_ok("desktop_window", {"action": "close", "window": "kate"})
    assert desktop.needs_ok("desktop_window", {"action": "minimise"}) is None
    assert "brain" in desktop.needs_ok("system_wifi", {"state": "off"})
    assert desktop.needs_ok("system_wifi", {"state": "on"}) is None


def test_rejects_unknown_window_actions_without_touching_kwin(monkeypatch):
    monkeypatch.setattr(desktop, "_kwin", lambda *a: (_ for _ in ()).throw(AssertionError("called KWin")))
    assert "action must be one of" in desktop.desktop_window("explode")


def test_reads_volume_and_mute_from_wpctl(monkeypatch):
    monkeypatch.setattr(desktop, "_run", lambda args, timeout=None: "Volume: 0.45 [MUTED]")
    assert desktop.system_volume() == "Volume is 45% (muted)."


def test_window_errors_come_back_as_advice(monkeypatch):
    monkeypatch.setattr(desktop, "_kwin", lambda body, args: {"error": 'no window matches "zoom"'})
    assert "desktop_windows lists" in desktop.desktop_window("focus", "zoom")


def test_notifications_are_kept_newest_last_and_waited_for():
    notifications._recent.clear()
    notifications._record("Discord", "Sam", "see you at 8?")
    notifications._record("Thunderbird", "Invoice", "")
    fresh, last = notifications.since(0, 0)
    assert [n["app"] for n in fresh] == ["Discord", "Thunderbird"]
    assert notifications.since(last, 0)[0] == []
    text = notifications.desktop_notifications(5)
    assert text.index("Discord") < text.index("Thunderbird") and "Sam: see you at 8?" in text
