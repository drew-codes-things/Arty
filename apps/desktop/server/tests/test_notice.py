import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
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


def locked_for(active_time):
    def screensaver(method):
        if method == "GetSessionIdleTime":
            raise idle.NotSupported(method)
        return active_time
    return screensaver


def test_linux_falls_back_to_how_long_the_screen_has_been_locked():
    assert idle.linux_idle_seconds(locked_for(1500), desktop="GNOME") == 1500


def test_kde_reports_the_lock_time_in_milliseconds():
    assert idle.linux_idle_seconds(locked_for(30_840), desktop="KDE") == 30.84


def test_a_short_lock_on_kde_is_not_mistaken_for_a_long_absence():
    assert idle.linux_idle_seconds(locked_for(1_200), desktop="KDE") < 20 * 60


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
    monkeypatch.setattr(arty_server.brain, "save_turn", lambda said, reply, actions=(): saved.append((said, reply)))
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


def test_a_long_stretch_held_back_by_the_gap_is_still_mentioned_once_the_gap_is_over():
    clock = Clock()
    watcher = Noticer(away_minutes=20, focus_minutes=10, gap_minutes=30, clock=clock)
    watcher.observe(25 * MINUTE, "code")
    clock.now += 1 * MINUTE
    assert watcher.observe(1, "code") == Notice("back", 25)
    clock.now += 11 * MINUTE
    assert watcher.observe(1, "code") is None
    clock.now += 20 * MINUTE
    assert watcher.observe(1, "code") == Notice("focus", 31, "code")
    clock.now += 60 * MINUTE
    assert watcher.observe(1, "code") is None


def test_one_failed_reading_does_not_stop_the_watcher(monkeypatch, capsys):
    offered = []
    monkeypatch.setattr(arty_server, "offer_notice", offered.append)
    readings = iter([IndexError("no reply from the screensaver"), 25 * MINUTE, 1, None])
    clock = Clock()

    def read_idle():
        reading = next(readings)
        if isinstance(reading, Exception):
            raise reading
        return reading

    def sleep(_seconds):
        clock.now += 30

    arty_server.watch_for_notices(noticer(clock), read_idle=read_idle, read_app=lambda: "code", sleep=sleep)
    assert offered == [Notice("back", 25)]
    assert "no reply from the screensaver" in capsys.readouterr().out


def test_a_focus_notice_held_back_by_a_call_comes_once_the_call_is_over():
    clock = Clock()
    watcher = noticer(clock)
    watcher.observe(1, "code")
    clock.now += 121 * MINUTE
    assert watcher.observe(1, "code", holding_back=True) is None
    clock.now += 5 * MINUTE
    assert watcher.observe(1, "code") == Notice("focus", 126, "code")


def test_the_watcher_holds_notices_back_while_a_call_is_on(monkeypatch):
    offered = []
    monkeypatch.setattr(arty_server, "offer_notice", offered.append)
    calls_on = iter([True, False])
    monkeypatch.setattr(arty_server.call, "is_on", lambda: next(calls_on))
    readings = iter([1, 1, None])
    clock = Clock()
    watcher = noticer(clock)
    watcher.observe(1, "code")
    clock.now += 121 * MINUTE

    def sleep(_seconds):
        clock.now += 30

    arty_server.watch_for_notices(watcher, read_idle=lambda: next(readings), read_app=lambda: "code", sleep=sleep)
    assert offered == [Notice("focus", 121, "code")]


def test_the_watcher_slows_down_but_never_gives_up_when_readings_keep_failing(capsys):
    failures = arty_server.MAX_FAILED_READINGS_IN_A_ROW + 3
    readings = iter([RuntimeError("the screensaver is gone")] * failures + [None])
    naps = []

    def read_idle():
        reading = next(readings)
        if isinstance(reading, Exception):
            raise reading
        return reading

    arty_server.watch_for_notices(noticer(Clock()), read_idle=read_idle, read_app=lambda: "code", sleep=naps.append)
    quick = arty_server.MAX_FAILED_READINGS_IN_A_ROW - 1
    assert naps[:quick] == [arty_server.NOTICE_CHECK_SECONDS] * quick
    assert set(naps[quick:]) == {arty_server.NOTICE_SLOW_CHECK_SECONDS}
    assert len(naps) == failures
    assert "slowing down" in capsys.readouterr().out


def fake_screensaver(monkeypatch, reply=(), refuse=None):
    blocking = pytest.importorskip("jeepney.io.blocking")
    wrappers = pytest.importorskip("jeepney.wrappers")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def send_and_get_reply(self, _message, timeout):
            if refuse:
                raise refuse
            return object()

    monkeypatch.setattr(blocking, "open_dbus_connection", lambda bus: Connection())
    monkeypatch.setattr(wrappers, "unwrap_msg", lambda _reply: reply)
    return wrappers


def test_an_empty_screensaver_reply_is_a_failed_reading_the_watcher_retries(monkeypatch):
    fake_screensaver(monkeypatch, reply=())
    with pytest.raises(IndexError):
        idle.screensaver_call("GetSessionIdleTime")


def test_a_screensaver_timeout_is_a_failed_reading_the_watcher_retries(monkeypatch):
    fake_screensaver(monkeypatch, refuse=TimeoutError("no answer in 2 s"))
    with pytest.raises(TimeoutError):
        idle.screensaver_call("GetSessionIdleTime")


def test_a_screensaver_without_the_method_means_the_desktop_cannot_tell(monkeypatch):
    wrappers = fake_screensaver(monkeypatch)
    refusal = wrappers.DBusErrorResponse.__new__(wrappers.DBusErrorResponse)
    refusal.name, refusal.data = "org.freedesktop.DBus.Error.UnknownMethod", ("no such method",)
    monkeypatch.setattr(wrappers, "unwrap_msg", lambda _reply: (_ for _ in ()).throw(refusal))
    with pytest.raises(idle.NotSupported):
        idle.screensaver_call("GetSessionIdleTime")


def test_a_failed_offer_does_not_stop_the_watcher(monkeypatch, capsys):
    offered = []

    def offer(notice):
        if not offered:
            offered.append(None)
            raise OSError("the notice file is locked")
        offered.append(notice)

    monkeypatch.setattr(arty_server, "offer_notice", offer)
    readings = iter([25 * MINUTE, 1, 25 * MINUTE, 1, None])
    clock = Clock()

    def sleep(_seconds):
        clock.now += 31 * MINUTE

    arty_server.watch_for_notices(noticer(clock), read_idle=lambda: next(readings), read_app=lambda: "code", sleep=sleep)
    assert offered[1:] == [Notice("back", 25)]
    assert "the notice file is locked" in capsys.readouterr().out


def test_without_jeepney_the_desktop_cannot_tell(monkeypatch):
    monkeypatch.setitem(sys.modules, "jeepney", None)
    monkeypatch.setitem(sys.modules, "jeepney.io.blocking", None)
    with pytest.raises(idle.NotSupported):
        idle.screensaver_call("GetSessionIdleTime")


def test_a_notice_that_could_not_be_offered_comes_back_on_the_next_check():
    clock = Clock()
    watcher = noticer(clock)
    watcher.observe(25 * MINUTE, "code")
    clock.now += 60
    notice = watcher.observe(1, "code")
    assert notice == Notice("back", 25)
    watcher.retract(notice)
    clock.now += 30
    assert watcher.observe(1, "code") == Notice("back", 25)


def test_a_focus_notice_that_could_not_be_offered_comes_back_too():
    clock = Clock()
    watcher = noticer(clock)
    watcher.observe(1, "code")
    clock.now += 121 * MINUTE
    notice = watcher.observe(1, "code")
    assert notice == Notice("focus", 121, "code")
    watcher.retract(notice)
    clock.now += 30
    assert watcher.observe(1, "code") == Notice("focus", 121, "code")


def test_the_watcher_offers_a_failed_notice_again(monkeypatch):
    offered = []

    def offer(notice):
        if not offered:
            offered.append(None)
            raise OSError("the notice file is locked")
        offered.append(notice)

    monkeypatch.setattr(arty_server, "offer_notice", offer)
    readings = iter([25 * MINUTE, 1, 1, None])
    clock = Clock()

    def sleep(_seconds):
        clock.now += 30

    arty_server.watch_for_notices(noticer(clock), read_idle=lambda: next(readings), read_app=lambda: "code", sleep=sleep)
    assert offered[1:] == [Notice("back", 25)]


@pytest.mark.parametrize("failure", [FileNotFoundError("no bus socket"), ConnectionRefusedError("bus refused"), KeyError("DBUS_SESSION_BUS_ADDRESS")])
def test_a_session_bus_that_is_not_there_yet_is_a_failed_reading_the_watcher_retries(monkeypatch, failure):
    fake_screensaver(monkeypatch, refuse=failure)
    with pytest.raises(type(failure)):
        idle.screensaver_call("GetSessionIdleTime")


@pytest.mark.parametrize("failure", [IndexError("empty reply"), ValueError("odd reply"), TypeError("odd reply")])
def test_odd_replies_from_both_idle_methods_mean_the_desktop_cannot_tell(failure):
    def call(method):
        if method == "GetSessionIdleTime":
            raise idle.NotSupported(method)
        raise failure

    assert idle.linux_idle_seconds(call=call, desktop="") is None


@pytest.mark.parametrize("failure", [IndexError("empty reply"), ValueError("odd reply"), TypeError("odd reply")])
def test_an_odd_reply_from_the_idle_method_still_falls_back_to_the_lock_time(failure):
    def call(method):
        if method == "GetSessionIdleTime":
            raise failure
        return 5000

    assert idle.linux_idle_seconds(call=call, desktop="") == 5000.0


@pytest.mark.parametrize("failure", [TimeoutError("no answer"), OSError("bus error"), KeyError("DBUS_SESSION_BUS_ADDRESS")])
def test_a_passing_failure_of_the_idle_method_is_retried_not_swapped_for_the_lock_time(failure):
    def call(method):
        if method == "GetSessionIdleTime":
            raise failure
        return 5000

    with pytest.raises(type(failure)):
        idle.linux_idle_seconds(call=call, desktop="")


def test_a_short_absence_that_could_not_be_offered_comes_back_too():
    clock = Clock()
    watcher = Noticer(away_minutes=0.5, focus_minutes=120, gap_minutes=30, clock=clock)
    watcher.observe(40, "code")
    clock.now += 5
    notice = watcher.observe(1, "code")
    assert notice == Notice("back", 0)
    watcher.retract(notice)
    clock.now += 30
    assert watcher.observe(1, "code") == Notice("back", 0)


def test_a_notice_that_could_not_be_recorded_is_not_left_pending(monkeypatch):
    def fail(summary, notice_id):
        raise OSError("the notice file is locked")

    monkeypatch.setattr(arty_server.notifications, "record_notice", fail)
    with pytest.raises(OSError):
        arty_server.offer_notice(Notice("back", 25))
    assert arty_server.take_notice(arty_server._notices["id"]) is None
