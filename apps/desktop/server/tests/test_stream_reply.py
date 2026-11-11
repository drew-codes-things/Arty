import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arty_server
from process import brain


def events_of(stream):
    return [json.loads(line) for line in stream]


def test_an_unexpected_error_still_ends_the_stream_and_keeps_the_turn(monkeypatch):
    saved = []

    def failing_reply(user_text, note=""):
        yield brain.Activity("reading notes.txt", "computer")
        yield "Here is the first part. "
        raise KeyError("arguments")

    monkeypatch.setattr(arty_server.brain, "llm_stream", failing_reply)
    monkeypatch.setattr(arty_server.brain, "save_turn", lambda *args, **kwargs: saved.append((args, kwargs)))
    events = events_of(arty_server._stream_reply("read my notes", speak=False))
    assert events[-1]["type"] == "error"
    assert "KeyError" not in events[-1]["message"]
    assert saved and saved[0][0][0] == "read my notes"


def test_the_tools_a_reply_used_are_kept_with_it(monkeypatch):
    saved = []

    def reply(user_text, note=""):
        yield brain.Activity("reading notes.txt", "computer")
        yield brain.Activity("Let Arty overwrite notes.txt?", "computer", confirm="abc")
        yield "Done."

    monkeypatch.setattr(arty_server.brain, "llm_stream", reply)
    monkeypatch.setattr(arty_server.brain, "save_turn", lambda *args, **kwargs: saved.append((args, kwargs)))
    events_of(arty_server._stream_reply("tidy my notes", speak=False))
    assert saved[0][1]["actions"] == ["reading notes.txt"]
