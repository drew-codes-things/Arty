import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import arty_server
from process.call import CALL_STYLE, PICK_UP, PICK_UP_SAID, Call

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
HEADERS = {"X-Arty-Token": arty_server.TOKEN}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_call_counts_its_seconds_until_hung_up():
    clock = Clock()
    call = Call(clock)
    assert call.status() == {"on": False, "seconds": 0}
    call.start()
    clock.now += 42.4
    assert call.status() == {"on": True, "seconds": 42}
    assert call.end() == {"on": False, "seconds": 42}
    assert call.status() == {"on": False, "seconds": 0}


def test_starting_twice_keeps_the_first_start_time():
    clock = Clock()
    call = Call(clock)
    call.start()
    clock.now += 10
    call.start()
    assert call.status()["seconds"] == 10


def test_replies_during_a_call_get_the_phone_style_and_others_do_not():
    call = Call(Clock())
    assert call.note_for("screen") == "screen"
    call.start()
    assert call.note_for("screen") == "screen" + CALL_STYLE
    call.end()
    assert call.note_for("") == ""


def test_the_phone_style_is_hidden_from_the_brains_archive():
    assert CALL_STYLE.strip().startswith("<context>") and CALL_STYLE.strip().endswith("</context>")
    assert PICK_UP.strip().startswith("<context>") and PICK_UP.strip().endswith("</context>")


def fake_brain(monkeypatch):
    sent, saved = [], []

    def llm_stream(user_input, extra_system=None, note=""):
        sent.append((user_input, note))
        yield "Hiya, what's up?"

    monkeypatch.setattr(arty_server.brain, "llm_stream", llm_stream)
    monkeypatch.setattr(arty_server.brain, "save_turn", lambda said, reply, actions=(): saved.append((said, reply)))
    return sent, saved


def events_of(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_picking_up_greets_and_saves_it_as_a_call_not_as_words(monkeypatch):
    sent, saved = fake_brain(monkeypatch)
    try:
        response = client.post("/call/start", json={"speak": False}, headers=HEADERS)
        assert response.status_code == 200
        assert events_of(response)[-1]["reply"] == "Hiya, what's up?"
        assert sent == [(PICK_UP_SAID, PICK_UP + CALL_STYLE)]
        assert saved == [(PICK_UP_SAID, "Hiya, what's up?")]
        assert client.get("/call", headers=HEADERS).json()["on"] is True
    finally:
        client.post("/call/end", headers=HEADERS)
    assert client.get("/call", headers=HEADERS).json()["on"] is False


def test_messages_during_a_call_carry_the_phone_style(monkeypatch):
    sent, _ = fake_brain(monkeypatch)
    try:
        client.post("/call/start", json={"speak": False}, headers=HEADERS)
        client.post("/chat/stream", json={"text": "how are you", "speak": False}, headers=HEADERS)
    finally:
        client.post("/call/end", headers=HEADERS)
    client.post("/chat/stream", json={"text": "and now", "speak": False}, headers=HEADERS)
    assert sent[1] == ("how are you", CALL_STYLE)
    assert sent[2] == ("and now", "")


def test_hanging_up_says_how_long_it_lasted(monkeypatch):
    fake_brain(monkeypatch)
    client.post("/call/start", json={"speak": False}, headers=HEADERS)
    ended = client.post("/call/end", headers=HEADERS).json()
    assert ended["on"] is False and ended["seconds"] >= 0
