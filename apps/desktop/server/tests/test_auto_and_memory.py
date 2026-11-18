import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arty_server
from process import brain
from test_brain import FakeStream, call, delta

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")
CODE_PICK = {"model": "qwen3:14b", "role": "code", "auto": True, "context_window": 24576}
USAGE = {"prompt_tokens": 1200, "completion_tokens": 60, "context_tokens": 1260, "cached_tokens": 1100, "tokens_per_second": 27.4}


def finished(generator):
    pieces = []
    try:
        while True:
            pieces.append(next(generator))
    except StopIteration as done:
        return pieces, done.value


def test_the_brains_report_on_the_last_chunk_comes_back_with_the_message(monkeypatch):
    last = {**delta(), "arty": {**CODE_PICK, "steps": 1, "usage": USAGE}}
    monkeypatch.setattr(brain.requests, "post", lambda *args, **kwargs: FakeStream([delta(content="Done."), last]))
    pieces, message = finished(brain._stream([{"role": "user", "content": "fix this"}], None))
    assert pieces == ["Done."]
    assert message["arty"] == {**CODE_PICK, "steps": 1, "usage": USAGE}


def test_an_older_brain_without_a_report_still_works(monkeypatch):
    monkeypatch.setattr(brain.requests, "post", lambda *args, **kwargs: FakeStream([delta(content="Hi.")]))
    assert finished(brain._stream([], None))[1]["arty"] is None


@pytest.fixture
def rounds(monkeypatch, tmp_path):
    monkeypatch.setattr(brain, "_history_path", lambda: tmp_path / "history.json")
    monkeypatch.setattr(brain.tools, "definitions", lambda request_text=None: [])
    monkeypatch.setattr(brain.tools, "describe", lambda name, arguments: ("", "computer"))
    monkeypatch.setattr(brain.tools, "needs_ok", lambda name, arguments, consented: None)
    monkeypatch.setattr(brain.tools, "run", lambda name, arguments: "done")
    replies = []

    def stream(messages, offer, lead=""):
        reply = replies.pop(0)
        if reply.get("content"):
            yield lead + reply["content"]
        return reply

    monkeypatch.setattr(brain, "_stream", stream)
    return replies


def test_a_reply_ends_with_one_report_counting_steps_here_and_on_the_brain(rounds):
    rounds += [
        {"content": "", "tool_calls": [call("computer_open", {"app": "a"}), call("computer_open", {"app": "b"})],
         "arty": {**CODE_PICK, "steps": 1, "usage": {**USAGE, "context_tokens": 900}}},
        {"content": "Both open.", "arty": {**CODE_PICK, "role": "chat", "steps": 0, "usage": USAGE}},
    ]
    pieces = list(brain.llm_stream("open a and b"))
    numbers = [piece for piece in pieces if isinstance(piece, brain.TurnNumbers)]
    assert len(numbers) == 1 and pieces[-1] is numbers[0]
    assert numbers[0] == ""
    assert numbers[0].report == {**CODE_PICK, "steps": 3, "usage": USAGE}


def test_no_report_is_made_up_when_the_brain_sent_none(rounds):
    rounds += [{"content": "Hi."}]
    assert not [piece for piece in brain.llm_stream("hi") if isinstance(piece, brain.TurnNumbers)]


def test_the_stream_to_the_window_carries_the_numbers_and_the_turn_keeps_them(monkeypatch, tmp_path):
    monkeypatch.setattr(brain, "_history_path", lambda: tmp_path / "history.json")
    report = {**CODE_PICK, "steps": 2, "usage": USAGE}

    def reply(user_text, note=""):
        yield "Fixed it."
        yield brain.TurnNumbers(report)

    monkeypatch.setattr(arty_server.brain, "llm_stream", reply)
    events = [json.loads(line) for line in arty_server._stream_reply("fix my script", speak=False)]
    assert {"type": "numbers", "arty": report} in events
    assert events[-1]["type"] == "done" and events[-1]["reply"] == "Fixed it."
    assert brain.load_history()[-1] == {"role": "assistant", "content": "Fixed it.", "arty": report}


def test_the_picker_is_arty_auto_then_each_model_once():
    listed = [
        {"id": "Arty", "model": "qwen3:14b", "description": "Picks the model for each request"},
        {"id": "arty/qwen3:14b", "name": "Arty (qwen3:14b)", "model": "qwen3:14b", "description": "14B"},
        {"id": "arty/mimo-v2.6:9b", "name": "Arty (mimo-v2.6:9b)", "model": "mimo-v2.6:9b"},
        {"id": "solo/mimo-v2.6:9b", "name": "mimo-v2.6:9b (on its own)", "model": "mimo-v2.6:9b"},
        {"id": "lab/p9", "name": "P9"},
        {"id": "extra/pal", "name": "Pal", "model": "qwen3:14b"},
        {"id": "extra/buddy", "name": "Buddy", "model": "tiny:1b"},
    ]
    picked = brain.picker_models(listed, {"id": "auto", "description": "Picks the model for each message"})
    assert [(entry["id"], entry["name"]) for entry in picked] == [
        ("auto", "Arty Auto"), ("arty/qwen3:14b", "qwen3:14b"), ("arty/mimo-v2.6:9b", "mimo-v2.6:9b"), ("lab/p9", "P9"),
        ("extra/buddy", "Buddy"),
    ]
    assert picked[0]["description"] == "Picks the model for each message"
    assert brain.picker_models([])[0] == {"id": "auto", "name": "Arty Auto", "model": "", "description": brain.AUTO_HINT}


@pytest.mark.parametrize("saved", ["Arty", "assistant-core", ""])
def test_the_main_arty_entry_under_any_name_now_means_arty_auto(saved):
    assert brain.as_current_name(saved) == "auto"


def test_picking_a_model_by_hand_sticks_and_is_saved(monkeypatch):
    saved = []
    monkeypatch.setattr(brain, "MODEL", brain.MODEL)
    monkeypatch.setattr(arty_server.cfg, "set_value", lambda *args: saved.append(args))
    headers = {"X-Arty-Token": arty_server.TOKEN}
    assert client.post("/model", json={"id": "arty/qwen3:14b"}, headers=headers).json() == {"current": "arty/qwen3:14b"}
    assert client.post("/model", json={"id": "Arty"}, headers=headers).json() == {"current": "auto"}
    assert saved == [("brain", "model", "arty/qwen3:14b"), ("brain", "model", "auto")]


class Answer:
    def __init__(self, status_code, body):
        self.status_code, self.body = status_code, body

    def json(self):
        return self.body

    def raise_for_status(self):
        return None


def test_memory_changes_go_to_the_brains_memory_routes(monkeypatch):
    sent = []
    monkeypatch.setattr(brain.route, "base", lambda: "http://brain:8081/v1")
    monkeypatch.setattr(brain.requests, "post", lambda url, json, **kwargs: sent.append((url, json)) or Answer(201, {"entity": "Rowan"}))
    assert brain.change_memory("add", {"entity": "Rowan", "fact": "Likes tea"}) == (201, {"entity": "Rowan"})
    assert sent == [("http://brain:8081/v1/memory/add", {"entity": "Rowan", "fact": "Likes tea"})]
    with pytest.raises(ValueError):
        brain.change_memory("rename", {})


def test_the_window_reads_and_changes_memory_through_the_local_service(monkeypatch):
    changes = []
    monkeypatch.setattr(arty_server.brain, "memory", lambda: [{"name": "Rowan", "entityType": "person", "observations": ["Likes tea"]}])
    monkeypatch.setattr(arty_server.brain, "change_memory", lambda action, change: changes.append((action, change)) or (400, {"error": {"message": "no"}}))
    headers = {"X-Arty-Token": arty_server.TOKEN}
    assert client.get("/memory", headers=headers).json()["entities"][0]["name"] == "Rowan"
    refused = client.post("/memory/edit", json={"entity": "Rowan", "old": "Likes tea", "new": "my pin is 1"}, headers=headers)
    assert (refused.status_code, refused.json()) == (400, {"error": {"message": "no"}})
    assert client.post("/memory/rename", json={}, headers=headers).status_code == 404
    assert client.get("/memory").status_code == 403
    assert changes == [("edit", {"entity": "Rowan", "old": "Likes tea", "new": "my pin is 1"})]
