import importlib
import io
import json
import os
import re
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_PAIR_CODE", "right-code-123456")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    sys.modules.pop("arty_telegram", None)
    module = importlib.import_module("arty_telegram")
    monkeypatch.setattr(module, "OWNER_FILE", tmp_path / "owner.txt")
    monkeypatch.setattr(module, "PAIR_CODE", "right-code-123456")
    replies = []
    monkeypatch.setattr(module, "reply", lambda chat_id, text, reply_to=None: replies.append(text))
    module.pair_failures.clear()
    module.replies = replies
    return module


def pairing(code, sender=42):
    return {"from": {"id": sender}, "chat": {"id": sender, "type": "private"}, "text": f"/pair {code}", "message_id": 1}


ME = {"id": 1, "username": "arty_bot"}


def test_the_right_code_pairs(bot):
    bot.handle(pairing("right-code-123456"), ME)
    assert bot.owner_id() == 42


def test_guessing_is_limited(bot):
    for attempt in range(bot.PAIR_ATTEMPTS_PER_HOUR):
        bot.handle(pairing(f"wrong-{attempt}"), ME)
    bot.handle(pairing("right-code-123456"), ME)
    assert bot.owner_id() is None


def test_guesses_age_out(bot, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(bot.time, "time", lambda: clock[0])
    for attempt in range(bot.PAIR_ATTEMPTS_PER_HOUR):
        bot.handle(pairing(f"wrong-{attempt}"), ME)
    clock[0] += bot.PAIR_WINDOW_SECONDS + 1
    bot.handle(pairing("right-code-123456"), ME)
    assert bot.owner_id() == 42


def test_a_non_ascii_code_is_a_wrong_code_not_a_crash(bot):
    bot.handle(pairing("é" * 10), ME)
    assert bot.owner_id() is None
    assert len(bot.pair_failures) == 1


def test_task_action_survives_a_non_json_error_page(bot, monkeypatch):
    def bad_gateway(*args, **kwargs):
        raise urllib.error.HTTPError("u", 502, "Bad Gateway", {}, io.BytesIO(b"<html>Bad Gateway</html>"))
    monkeypatch.setattr(bot.urllib.request, "urlopen", bad_gateway)
    assert bot.task_action(5, "approve") == "Error 502"


def test_task_action_survives_a_non_json_reply(bot, monkeypatch):
    class Page:
        def __enter__(self):
            return self

        def __exit__(self, *details):
            return False

        def read(self):
            return b"<html>login</html>"
    monkeypatch.setattr(bot.urllib.request, "urlopen", lambda *a, **k: Page())
    assert "didn't understand" in bot.task_action(5, "approve")


def test_brain_failure_message_names_no_container(bot, monkeypatch):
    def down(*args, **kwargs):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(bot.urllib.request, "urlopen", down)
    assert not re.search(r"\bCT \d+", bot.ask_brain([{"role": "user", "content": "hi"}]))


def test_a_locked_pairing_says_so_once_per_lock(bot, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(bot.time, "time", lambda: clock[0])
    for attempt in range(bot.PAIR_ATTEMPTS_PER_HOUR):
        bot.handle(pairing(f"wrong-{attempt}"), ME)
    assert bot.replies == []
    bot.handle(pairing("right-code-123456"), ME)
    bot.handle(pairing("right-code-123456"), ME)
    assert bot.replies == [bot.PAIRING_LOCKED_REPLY]
    clock[0] += bot.PAIR_WINDOW_SECONDS + 1
    for attempt in range(bot.PAIR_ATTEMPTS_PER_HOUR):
        bot.handle(pairing(f"again-{attempt}"), ME)
    bot.handle(pairing("right-code-123456"), ME)
    assert bot.replies == [bot.PAIRING_LOCKED_REPLY] * 2
    assert bot.owner_id() is None


def test_a_locked_pairing_ignores_group_chats(bot):
    for attempt in range(bot.PAIR_ATTEMPTS_PER_HOUR):
        bot.handle(pairing(f"wrong-{attempt}"), ME)
    group = {**pairing("right-code-123456"), "chat": {"id": -5, "type": "group"}}
    bot.handle(group, ME)
    assert bot.replies == []


class BrainReply:
    def read(self):
        return b'{"choices": [{"message": {"content": "Hi."}}]}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_brain_is_told_the_telegram_bot_is_asking(bot, monkeypatch):
    sent = {}

    def record(request, timeout=None):
        sent.update(json.loads(request.data))
        return BrainReply()

    monkeypatch.setattr(bot.urllib.request, "urlopen", record)
    assert bot.ask_brain([{"role": "user", "content": "hi"}]) == "Hi."
    assert sent["client"] == "telegram"
