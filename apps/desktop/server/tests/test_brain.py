"""The brain client's tool loop, with the brain and the tools faked."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import requests

from process import brain, confirm

NO_PERMISSION_BITS = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no Unix permission bits; the file sits in the user's own profile folder")


def call(name, arguments):
    return {"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(brain, "_history_path", lambda: tmp_path / "history.json")
    monkeypatch.setattr(brain.tools, "definitions", lambda request_text=None: [{"type": "function", "function": {"name": "computer_open"}}])
    monkeypatch.setattr(brain.tools, "describe", lambda name, arguments: (f"doing {name}", "computer"))
    monkeypatch.setattr(brain.tools, "needs_ok", lambda name, arguments, consented: None)
    ran = []
    monkeypatch.setattr(brain.tools, "run", lambda name, arguments: ran.append((name, json.loads(arguments))) or "opened")
    replies = []
    sent = []

    def stream(messages, offer, lead=""):
        sent.append([dict(m) for m in messages])
        reply = replies.pop(0)
        if reply.get("content"):
            yield lead + reply["content"]
        return reply

    monkeypatch.setattr(brain, "_stream", stream)
    return {"ran": ran, "replies": replies, "sent": sent}


def test_runs_the_brains_tool_calls_here_and_sends_back_the_results(fake):
    fake["replies"] += [{"content": "", "tool_calls": [call("computer_open", {"app": "Dolphin"})]}, {"content": "Dolphin's open."}]
    pieces = list(brain.llm_stream("open dolphin"))
    assert fake["ran"] == [("computer_open", {"app": "Dolphin"})]
    activities = [p for p in pieces if isinstance(p, brain.Activity)]
    assert [a.label for a in activities] == ["doing computer_open"]
    assert "".join(p for p in pieces if not isinstance(p, brain.Activity)) == "Dolphin's open."
    second = fake["sent"][1]
    assert second[-1] == {"role": "tool", "tool_call_id": "call_computer_open", "content": "opened"}
    assert second[-2]["tool_calls"][0]["function"]["name"] == "computer_open"


def test_asks_first_and_skips_the_action_when_refused(fake, monkeypatch):
    monkeypatch.setattr(brain.tools, "needs_ok", lambda name, arguments, consented: "Delete the folder?")
    monkeypatch.setattr(confirm, "wait", lambda ask, timeout=None: False)
    fake["replies"] += [{"tool_calls": [call("computer_run_command", {"command": "rm -r x"})]}, {"content": "OK, I left it."}]
    pieces = list(brain.llm_stream("delete x"))
    questions = [p for p in pieces if isinstance(p, brain.Activity) and p.confirm]
    assert [q.label for q in questions] == ["Delete the folder?"]
    assert fake["ran"] == []
    assert "didn't allow" in fake["sent"][1][-1]["content"]


def test_keeps_the_exchange_for_next_time(fake):
    fake["replies"] += [{"content": "Hi."}]
    list(brain.llm_stream("hello"))
    brain.save_turn("hello", "Hi.")
    fake["replies"] += [{"content": "Again."}]
    list(brain.llm_stream("and again"))
    assert [m["content"] for m in fake["sent"][1][1:3]] == ["hello", "Hi."]


def test_an_unreachable_brain_gives_a_plain_reason(monkeypatch):
    def refuse(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(brain.requests, "post", refuse)
    with pytest.raises(brain.BrainError, match="Can't reach Arty's brain"):
        list(brain._stream([{"role": "user", "content": "hi"}], None))


def test_words_said_before_a_tool_runs_are_kept_apart_from_the_answer(fake):
    fake["replies"] += [{"content": "Opening it.", "tool_calls": [call("computer_open", {"app": "Dolphin"})]}, {"content": "Done."}]
    pieces = list(brain.llm_stream("open dolphin"))
    assert "".join(p for p in pieces if isinstance(p, str)) == "Opening it.\n\nDone."


class FakeStream:
    """A streamed reply from the brain (server-sent events)."""

    def __init__(self, events, status_code=200):
        self.status_code = status_code
        self.lines = [f"data: {json.dumps(event)}" for event in events] + ["data: [DONE]"]

    def iter_lines(self, decode_unicode=False):
        yield from self.lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def delta(**fields):
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def test_the_reply_is_passed_on_as_it_arrives_and_tool_calls_are_collected(monkeypatch):
    tool_call = {"index": 0, **call("computer_open", {"app": "Dolphin"})}
    events = [delta(role="assistant", content="Opening"), delta(content=" it."), delta(tool_calls=[tool_call])]
    monkeypatch.setattr(brain.requests, "post", lambda *args, **kwargs: FakeStream(events))
    pieces = brain._stream([{"role": "user", "content": "open dolphin"}], None, lead="\n\n")
    received = []
    try:
        while True:
            received.append(next(pieces))
    except StopIteration as finished:
        message = finished.value
    assert received == ["\n\nOpening", " it."]
    assert message["content"] == "Opening it."
    assert message["tool_calls"] == [call("computer_open", {"app": "Dolphin"})]


def test_a_garbled_stream_is_reported(monkeypatch):
    stream = FakeStream([])
    stream.lines = ["data: {not json"]
    monkeypatch.setattr(brain.requests, "post", lambda *args, **kwargs: stream)
    with pytest.raises(brain.BrainError, match="unreadable"):
        list(brain._stream([{"role": "user", "content": "hi"}], None))


def test_each_request_is_offered_only_the_tool_groups_it_needs():
    from process.tools import registry
    chrome = registry.wanted_tools("open example.com in Chrome")
    assert "chrome_open" in chrome and "system_volume" not in chrome
    assert "system_volume" in registry.wanted_tools("turn the volume down")
    assert registry.wanted_tools("what's the weather tomorrow?") == registry.CORE_TOOLS


def test_the_conversation_is_trimmed_in_steps_so_its_start_stays_put():
    exchange = lambda n: [{"role": "user", "content": f"q{n}"}, {"role": "assistant", "content": f"a{n}"}]
    history = []
    starts = []
    for n in range(40):
        history = brain.trimmed(history + exchange(n))
        starts.append(history[0]["content"])
        assert history[0]["role"] == "user"
        assert len(history) <= (brain.HISTORY_TURNS + brain.HISTORY_STEP_TURNS) * 2
    # The start moves once per step, not with every message.
    assert len(set(starts)) <= 40 // brain.HISTORY_STEP_TURNS + 1


class FakeDownload:
    def __init__(self, status_code):
        self.status_code = status_code
        self.headers = {"content-type": "image/png"}
        self.closed = False

    def close(self):
        self.closed = True


def test_media_is_fetched_from_the_brain_by_its_download_path(monkeypatch):
    asked = {}

    def fetch(url, **kwargs):
        asked["url"] = url
        return FakeDownload(200)

    monkeypatch.setattr(brain.requests, "get", fetch)
    monkeypatch.setattr(brain.route, "base", lambda: "http://brain:8081/v1")
    brain.media("https://192.168.1.13:8443/arty-files/abc123/tok_en-1/shot.png")
    assert asked["url"] == "http://brain:8081/arty-files/abc123/tok_en-1/shot.png"


@pytest.mark.parametrize("link", [
    "https://example.com/picture.png",
    "https://h/arty-files/abc/tok/../../v1/models",
    "https://h/arty-files/abc/shot.png",
])
def test_links_that_are_not_arty_files_are_refused(link):
    with pytest.raises(ValueError):
        brain.media(link)


def test_an_expired_file_gives_a_plain_reason(monkeypatch):
    download = FakeDownload(404)
    monkeypatch.setattr(brain.requests, "get", lambda *args, **kwargs: download)
    with pytest.raises(brain.BrainError, match="expired"):
        brain.media("https://h/arty-files/abc/tok/shot.png")
    assert download.closed


@NO_PERMISSION_BITS
def test_the_saved_conversation_is_readable_by_this_user_only(fake, tmp_path):
    brain.save_turn("hello", "hi there")
    saved = tmp_path / "history.json"
    assert saved.stat().st_mode & 0o777 == 0o600
    assert brain.load_history()[-1] == {"role": "assistant", "content": "hi there"}
