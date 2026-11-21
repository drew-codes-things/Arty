import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import arty_server
from process import brain
from process.tools import registry as tools


def events_of(stream):
    return [json.loads(line) for line in stream]


def test_an_unexpected_error_still_ends_the_stream_and_keeps_the_turn(monkeypatch):
    saved = []

    def failing_reply(user_text, note="", arty_started=False):
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

    def reply(user_text, note="", arty_started=False):
        yield brain.Activity("reading notes.txt", "computer")
        yield brain.Activity("Let Arty overwrite notes.txt?", "computer", confirm="abc")
        yield "Done."

    monkeypatch.setattr(arty_server.brain, "llm_stream", reply)
    monkeypatch.setattr(arty_server.brain, "save_turn", lambda *args, **kwargs: saved.append((args, kwargs)))
    events_of(arty_server._stream_reply("tidy my notes", speak=False))
    assert saved[0][1]["actions"] == ["reading notes.txt"]


def test_a_reply_that_starts_from_a_screen_look_counts_as_having_read_the_screen(monkeypatch):
    seen = {}

    def reply(user_text, note="", arty_started=False):
        tools.start_reply(user_text, arty_started)
        seen["read"] = tools._reply["read_a_web_page"]
        seen["asked"] = tools.needs_ok("computer_run_command", json.dumps({"command": "ls ~/Downloads"}), set())
        yield "Done."

    monkeypatch.setattr(arty_server.computer, "computer_look", lambda question="": "an error dialog")
    monkeypatch.setattr(arty_server.brain, "llm_stream", reply)
    monkeypatch.setattr(arty_server.brain, "save_turn", lambda *args, **kwargs: None)
    events_of(arty_server._stream_reply("what does this error mean", speak=False, look=True))
    assert seen["read"] is True and seen["asked"].startswith("This reply read a web page or your screen")
    events_of(arty_server._stream_reply("and now?", speak=False))
    assert seen["read"] is False
