import asyncio
import json
import os
import sys
import types
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
os.environ.setdefault("HUB_TOKEN", "hub-test")
os.environ.setdefault("ARTY_API_KEY", "brain-test")
sys.modules.setdefault("faster_whisper", types.SimpleNamespace(WhisperModel=object))

import pytest
from aiohttp.test_utils import TestClient, TestServer

import headset
import headset_commands
import headset_panels
import music
from listening import UtteranceCollector


@pytest.mark.parametrize("command, expected", [
    ("play International Love by Pitbull on Spotify.", {"action": "spotify_play", "query": "International Love by Pitbull"}),
    ("can you play Fireball from spotify please", {"action": "spotify_play", "query": "Fireball"}),
    ("Spotify, play Timber", {"action": "spotify_play", "query": "Timber"}),
    ("pause Spotify.", {"action": "media_pause"}),
    ("Pause.", {"action": "media_pause"}),
    ("resume the music", {"action": "media_play"}),
    ("skip this song", {"action": "media_next"}),
    ("turn it down", {"action": "volume_down"}),
    ("open YouTube", {"action": "open_app", "app": "YouTube"}),
    ("can you launch the Beat Saber app please", {"action": "open_app", "app": "Beat Saber"}),
    ("Open up my Meta Quest Browser.", {"action": "open_app", "app": "Meta Quest Browser"}),
])
def test_headset_commands_are_recognised(command, expected):
    assert headset_commands.parse(command) == expected


@pytest.mark.parametrize("question", [
    "what is the most played song on Spotify?", "why did the music stop?", "play a game with me",
    "how do I pause a download?", "next week what is the weather",
])
def test_questions_go_to_the_brain(question):
    assert headset_commands.parse(question) is None


class FakeSenses:

    def __init__(self):
        self.collectors = []

    def wav(self, text):
        return b"RIFF" + text.encode()

    def new_collector(self):
        self.collectors.append(UtteranceCollector())
        return self.collectors[-1]


class FakeSocket:

    def __init__(self):
        self.closed = False
        self.sent = []
        self.audio = []

    async def send_json(self, message):
        self.sent.append(message)

    async def send_bytes(self, data):
        self.audio.append(data)


def run_session(scenario):
    async def run():
        ws = FakeSocket()
        session = headset.HeadsetSession(FakeSenses(), None, ws)
        return await scenario(session, ws)

    return asyncio.run(run())


def test_a_headset_command_is_sent_and_its_reply_spoken():
    async def scenario(session, ws):
        acting = asyncio.create_task(session.act("pause spotify"))
        await asyncio.sleep(0)
        action = next(m for m in ws.sent if m["type"] == "action")
        await session.handle_text(json.dumps({"type": "action_result", "id": action["id"], "say": "Paused."}))
        handled = await acting
        return handled, action, ws.audio

    handled, action, audio = run_session(scenario)
    assert handled and action["action"] == "media_pause"
    assert audio == [b"RIFFPaused."]


def test_a_silent_headset_is_reported(monkeypatch):
    monkeypatch.setattr(headset, "ACTION_TIMEOUT_SECONDS", 0.05)

    async def scenario(session, ws):
        return await session.act("next song"), ws.audio

    handled, audio = run_session(scenario)
    assert handled and audio == [b"RIFF" + headset.NO_REPLY.encode()]


def test_the_time_is_answered_without_the_brain_or_the_headset():
    async def scenario(session, ws):
        session.clock = lambda: datetime(2026, 11, 5, 17, 44)
        return await session.act("what's the time?"), ws.sent, ws.audio

    handled, sent, audio = run_session(scenario)
    assert handled and audio == [b"RIFFIt's 5:44 pm."]
    assert not any(m["type"] == "action" for m in sent)


def test_a_word_is_defined_from_the_dictionary(monkeypatch):
    async def dictionary(http, term):
        return f"{term} looked up"

    monkeypatch.setattr(headset.quick_answers, "look_up_word", dictionary)

    async def scenario(session, ws):
        session.http = object()
        return await session.act("what does ephemeral mean?"), ws.audio

    handled, audio = run_session(scenario)
    assert handled and audio == [b"RIFFephemeral looked up"]


def test_an_unknown_word_is_left_for_the_brain(monkeypatch):
    async def nothing(http, term):
        return None

    monkeypatch.setattr(headset.quick_answers, "look_up_word", nothing)

    async def scenario(session, ws):
        session.http = object()
        return await session.act("what does skibidi mean?"), ws.sent

    handled, sent = run_session(scenario)
    assert handled is False and sent == []


def test_a_search_request_asks_the_brain_to_search_the_web(monkeypatch):
    asked = []

    async def answer(question):
        asked.append(question)

    async def scenario(session, ws):
        monkeypatch.setattr(session.assistant, "answer", answer)
        return await session.act("search up Tesco opening times")

    assert run_session(scenario) is True
    assert asked == ["Search the web for Tesco opening times and tell me what you find."]


def test_open_app_is_sent_to_the_headset():
    async def scenario(session, ws):
        acting = asyncio.create_task(session.act("open YouTube"))
        await asyncio.sleep(0)
        action = next(m for m in ws.sent if m["type"] == "action")
        await session.handle_text(json.dumps({"type": "action_result", "id": action["id"], "say": ""}))
        return await acting, action

    handled, action = run_session(scenario)
    assert handled and action["action"] == "open_app" and action["app"] == "YouTube"


def test_questions_are_left_for_the_brain():
    async def scenario(session, ws):
        return await session.act("what's the weather like?"), ws.sent

    handled, sent = run_session(scenario)
    assert handled is False and sent == []


def test_the_headset_can_switch_the_wake_word_off():
    async def scenario(session, ws):
        await session.handle_text(json.dumps({"type": "wake", "enabled": False}))
        return session.assistant.wake_enabled, ws.sent[-1]

    enabled, last = run_session(scenario)
    assert enabled is False and last["state"] == "off"


def test_unknown_headsets_are_refused(monkeypatch):
    monkeypatch.setattr(headset, "HEADSET_TOKEN", "right-key")

    async def no_panels(self):
        return {"type": "panels"}

    monkeypatch.setattr(headset_panels.Dashboard, "current", no_panels)

    async def run():
        client = TestClient(TestServer(headset.build_app(FakeSenses(), None)))
        await client.start_server()
        try:
            wrong = await client.get("/ws/headset", headers={"Authorization": "Bearer wrong-key"})
            ws = await client.ws_connect("/ws/headset", headers={"Authorization": "Bearer right-key"})
            hello = await ws.receive_json(timeout=5)
            await ws.close()
            return wrong.status, hello
        finally:
            await client.close()

    status, hello = asyncio.run(run())
    assert status == 401 and hello == {"type": "hello", "wake_enabled": True}


STATUS = """Uptime Kuma isn't connected yet, so here is the Proxmox server status instead:
Host pve1 (192.0.2.253): online, CPU 8% of 6 cores, RAM 12.3G/15.5G, up 88h
CT 100 web (192.0.2.10): running, CPU 4%, RAM 1.0G/4.0G, up 88h
VM 106 test-debian: stopped
Storage tank: 206.4G used of 348.1G"""


def test_server_status_becomes_panel_rows():
    assert headset_panels.servers_from(STATUS) == [
        {"name": "pve1", "state": "online", "ok": True, "detail": "CPU 8% of 6 cores, RAM 12.3G/15.5G"},
        {"name": "web", "state": "running", "ok": True, "detail": "CPU 4%, RAM 1.0G/4.0G"},
        {"name": "test-debian", "state": "stopped", "ok": False, "detail": ""},
    ]


def test_reminders_are_the_next_active_ones_in_order():
    schedules = {"schedules": [
        {"title": "Later", "next_run": "2026-11-03T09:00", "enabled": True},
        {"title": "Off", "next_run": "2026-11-02T10:00", "enabled": False},
        {"title": "Soon", "next_run": "2026-11-02T18:00", "enabled": True},
        {"title": "Never", "next_run": None, "enabled": True},
    ]}
    assert [r["title"] for r in headset_panels.reminders_from(schedules)] == ["Soon", "Later"]


def test_missing_calendar_data_is_an_empty_list():
    assert headset_panels.events_from({}) == []


def test_room_volume_phrases():
    from headset_commands import parse_volume
    assert parse_volume("turn the volume down a bit") == {"change": "down"}
    assert parse_volume("Louder please.") == {"change": "up"}
    assert parse_volume("set the volume to 40%") == {"level": 40}
    assert parse_volume("volume 150 percent") == {"level": 100}
    assert parse_volume("mute the speakers") == {"change": "mute"}
    assert parse_volume("unmute") == {"change": "unmute"}
    assert parse_volume("what's the volume of a sphere") is None
    assert parse_volume("turn the lights down") is None


def test_reminder_times_that_arrive_as_epoch_seconds_become_the_text_the_headset_shows():
    epoch = 1794394300.0
    expected = datetime.fromtimestamp(epoch).strftime("%Y-%m-%dT%H:%M")
    schedules = {"schedules": [
        {"title": "Epoch", "next_run": epoch, "enabled": True},
        {"title": "Text", "next_run": "2030-01-01T09:00", "enabled": True},
    ]}
    assert headset_panels.reminders_from(schedules) == [
        {"title": "Epoch", "next_run": expected},
        {"title": "Text", "next_run": "2030-01-01T09:00"},
    ]


@pytest.mark.parametrize("next_run", [1794394300000, 1e30, -1e30, True])
def test_a_reminder_time_that_is_not_a_real_timestamp_is_shown_as_it_came_and_keeps_the_rest(next_run):
    schedules = {"schedules": [
        {"title": "Odd", "next_run": next_run, "enabled": True},
        {"title": "Fine", "next_run": "2030-01-01T09:00", "enabled": True},
    ]}
    assert [r["title"] for r in headset_panels.reminders_from(schedules)] == ["Fine", "Odd"]


def test_reminders_stay_in_time_order_across_epoch_and_text_times():
    epoch = datetime(2026, 11, 11, 8, 0).timestamp()
    schedules = {"schedules": [
        {"title": "Later", "next_run": "2026-11-11T09:00", "enabled": True},
        {"title": "Middle", "next_run": epoch, "enabled": True},
        {"title": "First", "next_run": "2026-11-11T07:00", "enabled": True},
    ]}
    assert [r["title"] for r in headset_panels.reminders_from(schedules)] == ["First", "Middle", "Later"]


def test_a_text_time_outside_the_calendar_does_not_break_the_reminders():
    schedules = {"schedules": [
        {"title": "Odd", "next_run": "0001-01-01T00:00", "enabled": True},
        {"title": "Fine", "next_run": "2030-01-01T09:00", "enabled": True},
    ]}
    assert [r["title"] for r in headset_panels.reminders_from(schedules)] == ["Fine", "Odd"]


def test_text_times_are_shown_in_the_same_format_as_epoch_times():
    moment = datetime.fromisoformat("2026-11-11T09:00:30+00:00")
    schedules = {"schedules": [{"title": "Offset", "next_run": "2026-11-11T09:00:30+00:00", "enabled": True}]}
    assert headset_panels.reminders_from(schedules)[0]["next_run"] == moment.astimezone().strftime("%Y-%m-%dT%H:%M")


@pytest.mark.parametrize("command, expected", [
    ("play Timber by Pitbull on YouTube", {"action": "play_on", "app": "YouTube", "query": "Timber by Pitbull"}),
    ("play lofi beats on YouTube Music please", {"action": "play_on", "app": "YouTube Music", "query": "lofi beats"}),
    ("YouTube, play Timber", {"action": "play_on", "app": "YouTube", "query": "Timber"}),
    ("put on some jazz on Deezer", {"action": "play_on", "app": "Deezer", "query": "some jazz"}),
    ("play Timber by Pitbull", {"action": "play_on", "app": "YouTube", "query": "Timber by Pitbull"}),
    ("can you play my workout playlist", {"action": "play_on", "app": "YouTube", "query": "my workout playlist"}),
    ("play the song Fireball", {"action": "play_on", "app": "YouTube", "query": "the song Fireball"}),
    ("play International Love by Pitbull on Spotify", {"action": "spotify_play", "query": "International Love by Pitbull"}),
    ("play music", {"action": "media_play"}),
])
def test_music_requests_pick_an_app_and_a_song(command, expected):
    assert headset_commands.parse(command) == expected


@pytest.mark.parametrize("question", [
    "play a game with me", "play it", "play something", "play with the settings", "play Timber",
    "play it on my headset", "what is the best song to play",
])
def test_vague_play_requests_are_left_for_the_brain(question):
    assert headset_commands.parse(question) is None


class FakeResponse:

    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def text(self):
        return self.body


class FakeWeb:

    def __init__(self, status=200, body="", failure=None):
        self.response = FakeResponse(status, body)
        self.failure = failure
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append((url, kwargs))
        if self.failure:
            raise self.failure
        return self.response


def test_the_first_video_in_the_search_page_is_the_one_to_play():
    page = 'junk {"videoId":"hHUbLv4ThOo","x":1} more {"videoId":"AAAAAAAAAAA"}'
    web = FakeWeb(body=page)
    url = asyncio.run(music.youtube_watch_url(web, "Timber Pitbull"))
    assert url == "https://www.youtube.com/watch?v=hHUbLv4ThOo"
    assert web.asked[0][1]["params"] == {"search_query": "Timber Pitbull"}


@pytest.mark.parametrize("web", [FakeWeb(status=429), FakeWeb(body="no videos here"), FakeWeb(failure=asyncio.TimeoutError())])
def test_no_video_found_gives_no_url(web):
    assert asyncio.run(music.youtube_watch_url(web, "anything")) is None


@pytest.mark.parametrize("name, expected", [
    ("YouTube", True), ("youtube music", True), ("You Tube", True), ("Deezer", False), ("Spotify", False),
])
def test_youtube_is_recognised_by_its_spoken_name(name, expected):
    assert music.is_youtube(name) is expected


def run_play(command, link_result):
    async def scenario(session, ws):
        async def fake_lookup(_http, _query):
            return link_result

        music_lookup = headset.music.youtube_watch_url
        headset.music.youtube_watch_url = fake_lookup
        try:
            acting = asyncio.create_task(session.act(command))
            await asyncio.sleep(0.05)
            action = next(m for m in ws.sent if m["type"] == "action")
            await session.handle_text(json.dumps({"type": "action_result", "id": action["id"], "say": "Playing it."}))
            return await acting, action
        finally:
            headset.music.youtube_watch_url = music_lookup

    return run_session(scenario)


def test_a_youtube_request_is_sent_with_the_video_it_found():
    handled, action = run_play("play Timber by Pitbull", "https://www.youtube.com/watch?v=hHUbLv4ThOo")
    assert handled
    assert action["action"] == "youtube_play" and action["query"] == "Timber by Pitbull"
    assert action["url"] == "https://www.youtube.com/watch?v=hHUbLv4ThOo"


def test_a_youtube_request_without_a_found_video_still_goes_out_for_a_search():
    handled, action = run_play("play my workout playlist on youtube", None)
    assert handled and action["action"] == "youtube_play" and action["url"] == ""


def test_another_app_is_sent_by_name():
    handled, action = run_play("put on some jazz on Deezer", None)
    assert handled and action["action"] == "app_play" and action["app"] == "Deezer" and action["query"] == "some jazz"


@pytest.mark.parametrize("command, expected", [
    ("play the next song", {"action": "media_next"}),
    ("play the previous track", {"action": "media_previous"}),
    ("play the video on the youtube app", {"action": "play_on", "app": "youtube", "query": "the video"}),
    ("play Timber by Pitbull on the Spotify app", {"action": "spotify_play", "query": "Timber by Pitbull"}),
    ("play lofi on Deezer", {"action": "play_on", "app": "Deezer", "query": "lofi"}),
    ("play my mix on the Foobar app", {"action": "play_on", "app": "Foobar", "query": "my mix"}),
])
def test_play_phrases_map_to_the_right_action(command, expected):
    assert headset_commands.parse(command) == expected


@pytest.mark.parametrize("question", [
    "play chess in the garden", "play cards with me in the garden", "play the piano in the morning",
    "play it on youtube", "play that on spotify please", "play something on youtube",
])
def test_everyday_play_phrases_are_left_for_the_brain(question):
    assert headset_commands.parse(question) is None


def test_an_ad_or_shelf_video_before_the_results_is_skipped():
    page = ('{"adSlotRenderer":{"videoId":"ADADADADADA"}} {"reelItemRenderer":{"videoId":"SHORTSSHORT"}} '
            '{"videoRenderer":{"videoId":"hHUbLv4ThOo","title":"x"}} {"videoRenderer":{"videoId":"BBBBBBBBBBB"}}')
    url = asyncio.run(music.youtube_watch_url(FakeWeb(body=page), "Timber"))
    assert url == "https://www.youtube.com/watch?v=hHUbLv4ThOo"


@pytest.mark.parametrize("command, expected", [
    ("play dancing in the dark on youtube", {"action": "play_on", "app": "youtube", "query": "dancing in the dark"}),
    ("play hello from adele on youtube", {"action": "play_on", "app": "youtube", "query": "hello from adele"}),
    ("play music from the 90s on youtube", {"action": "play_on", "app": "youtube", "query": "music from the 90s"}),
    ("play the next song on spotify", {"action": "media_next"}),
    ("play the previous song on spotify", {"action": "media_previous"}),
    ("play the next song on youtube", {"action": "media_next"}),
])
def test_play_phrases_split_at_the_last_separator_and_skip_phrases_skip(command, expected):
    assert headset_commands.parse(command) == expected


def test_a_page_that_cannot_be_decoded_gives_no_url():
    class BrokenResponse(FakeResponse):
        async def text(self):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    web = FakeWeb()
    web.response = BrokenResponse(200, "")
    assert asyncio.run(music.youtube_watch_url(web, "anything")) is None


def test_the_youtube_names_are_shared_between_the_parser_and_the_lookup():
    assert music.YOUTUBE_NAMES <= headset_commands.KNOWN_PLAYERS


@pytest.mark.parametrize("command, expected", [
    ("play the next song on the headset", {"action": "media_next"}),
    ("play the previous track on apple music", {"action": "media_previous"}),
    ("play the last song on the playlist", {"action": "media_previous"}),
])
def test_skip_phrases_skip_whatever_follows_them(command, expected):
    assert headset_commands.parse(command) == expected


def test_the_command_parser_does_not_need_the_network_library(monkeypatch):
    import importlib

    monkeypatch.setitem(sys.modules, "aiohttp", None)
    monkeypatch.delitem(sys.modules, "headset_commands", raising=False)
    monkeypatch.delitem(sys.modules, "music", raising=False)
    parser = importlib.import_module("headset_commands")
    assert parser.parse("pause") == {"action": "media_pause"}
