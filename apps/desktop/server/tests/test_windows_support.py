import importlib
import os
import sys

import pytest

from process import config, notifications


def test_private_files_are_written_where_the_system_cannot_set_a_mode(monkeypatch, tmp_path):
    monkeypatch.delattr(os, "fchmod", raising=False)
    target = tmp_path / "private.txt"
    config.write_private(target, "secret settings")
    assert target.read_text(encoding="utf-8") == "secret settings"


def test_notifications_load_without_the_linux_desktop_bus(monkeypatch):
    monkeypatch.setitem(sys.modules, "jeepney", None)
    monkeypatch.setitem(sys.modules, "jeepney.bus_messages", None)
    monkeypatch.setitem(sys.modules, "jeepney.io.blocking", None)
    try:
        assert importlib.reload(notifications).SPECS
    finally:
        monkeypatch.undo()
        importlib.reload(notifications)


def test_the_notification_watcher_is_not_started_where_it_cannot_work(monkeypatch):
    monkeypatch.setattr(notifications.sys, "platform", "win32")
    monkeypatch.setattr(notifications, "_started", False)
    monkeypatch.setattr(notifications.threading, "Thread", lambda *args, **kwargs: pytest.fail("no watcher thread on Windows"))
    notifications.start()
    assert notifications._started is True


def test_reading_other_apps_notifications_says_it_is_unavailable_on_windows(monkeypatch):
    monkeypatch.setattr(notifications.sys, "platform", "win32")
    monkeypatch.setattr(notifications, "_recent", notifications.collections.deque(maxlen=notifications.KEEP))
    assert "isn't available on Windows" in notifications.desktop_notifications()


def test_arty_announcements_still_reach_the_window_on_windows(monkeypatch):
    monkeypatch.setattr(notifications.sys, "platform", "win32")
    monkeypatch.setattr(notifications, "_recent", notifications.collections.deque(maxlen=notifications.KEEP))
    notifications.record_announcement({"title": "Reminder", "message": "Take the bins out", "kind": "reminder", "id": 7})
    fresh, _last = notifications.since(0, 0)
    assert [item["summary"] for item in fresh] == ["Reminder"]
