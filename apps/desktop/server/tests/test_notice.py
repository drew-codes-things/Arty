import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from process import notifications
from process.notice import Notice, Noticer
from process.tools import idle

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
HEADERS = {"X-Arty-Token": arty_server.TOKEN}
MINUTE = 60


class Clock:
    def __init__(self):
        self.now = 10_000.0

    def __call__(self):
        return self.now


def noticer(clock):
    return Noticer(away_minutes=20, focus_minutes=120, gap_minutes=30, clock=clock)


def test_coming_back_after_a_long_while_is_noticed_once():
    clock = Clock()
    watcher = noticer(clock)
    assert watcher.observe(25 * MINUTE, "firefox") is None
    clock.now += 10 * MINUTE
    assert watcher.observe(35 * MINUTE, "firefox") is None
    clock.now += 30
    assert watcher.observe(5, "firefox") == Notice("back", 35)
    clock.now += 30
    assert watcher.observe(35, "firefox") is None


def test_a_short_break_is_not_worth_a_word():
    clock = Clock()
    watcher = noticer(clock)
    assert watcher.observe(5 * MINUTE, "konsole") is None
    clock.now += 30
    assert watcher.observe(2, "konsole") is None


def test_hours_in_one_app_are_noticed_once_per_stretch():
    clock = Clock()
    watcher = noticer(clock)
    assert watcher.observe(0, "code") is None
    clock.now += 119 * MINUTE
    assert watcher.observe(0, "code") is None
    clock.now += 2 * MINUTE
    assert watcher.observe(0, "code") == Notice("focus", 121, "code")
    clock.now += 60 * MINUTE
    assert watcher.observe(0, "code") is None


def test_switching_app_starts_the_stretch_again():
    clock = Clock()
    watcher = noticer(clock)
    watcher.observe(0, "code")
    clock.now += 100 * MINUTE
    watcher.observe(0, "firefox")
    clock.now += 30 * MINUTE
    assert watcher.observe(0, "code") is None


def test_arty_itself_and_the_lock_screen_do_not_count_as_what_they_are_doing():
    clock = Clock()
    watcher = noticer(clock)
    for app in ("Arty", "plasmashell", "kscreenlocker_greet", "LockApp.exe", ""):
        watcher.observe(0, app)
        clock.now += 200 * MINUTE
        assert watcher.observe(0, app) is None


def test_two_notices_never_come_close_together():
    clock = Clock()
    watcher = noticer(clock)
    watcher.observe(0, "code")
    clock.now += 121 * MINUTE
    assert watcher.observe(0, "code") is not None
    clock.now += 1 * MINUTE
    watcher.observe(21 * MINUTE, "code")
    clock.now += 1 * MINUTE
    assert watcher.observe(1, "code") is None


def test_each_notice_asks_for_a_short_line_inside_a_hidden_note():
    for notice in (Notice("back", 35), Notice("focus", 130, "code")):
        assert notice.prompt().strip().startswith("<context>") and notice.prompt().strip().endswith("</context>")
    assert "35 minutes" in Notice("back", 35).prompt()
    assert "code" in Notice("focus", 130, "code").prompt() and "2 hours" in Notice("focus", 130, "code").prompt()


def test_windows_idle_time_survives_the_tick_counter_wrapping():
    assert idle.idle_from_ticks(5_000, 2_000) == 3.0
    assert idle.idle_from_ticks(1_000, 2**32 - 1_000) == 2.0


def test_linux_uses_the_session_idle_time_where_the_desktop_has_one():
    assert idle.linux_idle_seconds(lambda method: {"GetSessionIdleTime": 90, "GetActiveTime": 0}[method]) == 90


def test_linux_falls_back_to_how_long_the_screen_has_been_locked():
    def kde(method):
        if method == "GetSessionIdleTime":
            raise idle.NotSupported(method)
        return 1500
    assert idle.linux_idle_seconds(kde) == 1500


def test_linux_without_either_cannot_tell():
    def nothing(method):
        raise idle.NotSupported(method)
    assert idle.linux_idle_seconds(nothing) is None


def test_a_notice_is_offered_to_the_window_and_answered_by_arty(monkeypatch):
    sent, saved = [], []

    def llm_stream(user_input, extra_system=None, note=""):
        sent.append((user_input, note))
        yield "Welcome back."

    monkeypatch.setattr(arty_server.brain, "llm_stream", llm_stream)
    monkeypatch.setattr(arty_server.brain, "save_turn", lambda said, reply: saved.append((said, reply)))
    _, before = notifications.since(0, 0)
    notice_id = arty_server.offer_notice(Notice("back", 35))
    fresh, _ = notifications.since(before, 0)
    assert fresh[-1]["kind"] == "notice" and fresh[-1]["notice"] == notice_id
    response = client.post("/notice/reply", json={"id": notice_id, "speak": False}, headers=HEADERS)
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert lines[-1]["reply"] == "Welcome back."
    assert sent == [(Notice("back", 35).said(), Notice("back", 35).prompt())]
    assert saved == [(Notice("back", 35).said(), "Welcome back.")]
    again = client.post("/notice/reply", json={"id": notice_id, "speak": False}, headers=HEADERS)
    assert json.loads(again.text.splitlines()[0])["type"] == "cancelled"


class Stop(Exception):
    pass


def test_the_watcher_offers_what_it_notices_and_stops_where_idle_time_is_unknown(monkeypatch):
    offered = []
    monkeypatch.setattr(arty_server, "offer_notice", offered.append)
    readings = iter([25 * MINUTE, 1, None])
    clock = Clock()

    def sleep(_seconds):
        clock.now += 30

    arty_server.watch_for_notices(noticer(clock), read_idle=lambda: next(readings), read_app=lambda: "code", sleep=sleep)
    assert offered == [Notice("back", 25)]


def test_nothing_is_offered_during_a_call(monkeypatch):
    offered = []
    monkeypatch.setattr(arty_server, "offer_notice", offered.append)
    readings = iter([25 * MINUTE, 1, None])
    arty_server.call.start()
    try:
        arty_server.watch_for_notices(noticer(Clock()), read_idle=lambda: next(readings), read_app=lambda: "code",
                                      sleep=lambda _seconds: None)
    finally:
        arty_server.call.end()
    assert offered == []
