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

    def wav(self, text):
        return b"RIFF" + text.encode()


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
Host servmox (192.168.1.253): online, CPU 8% of 6 cores, RAM 12.3G/15.5G, up 88h
CT 100 core-sys (192.168.1.10): running, CPU 4%, RAM 1.0G/4.0G, up 88h
VM 106 test-debian: stopped
Storage DataStarServ: 206.4G used of 348.1G"""


def test_server_status_becomes_panel_rows():
    assert headset_panels.servers_from(STATUS) == [
        {"name": "servmox", "state": "online", "ok": True, "detail": "CPU 8% of 6 cores, RAM 12.3G/15.5G"},
        {"name": "core-sys", "state": "running", "ok": True, "detail": "CPU 4%, RAM 1.0G/4.0G"},
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
