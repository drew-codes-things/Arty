import importlib
import json
import os
import sys
import unittest.mock

import pytest

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture
def bot(monkeypatch):
    fake_discord = unittest.mock.MagicMock()
    monkeypatch.setitem(sys.modules, "discord", fake_discord)
    monkeypatch.setitem(sys.modules, "discord.app_commands", fake_discord.app_commands)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("DISCORD_OWNER_ID", "42")
    monkeypatch.setenv("ARTY_API_KEY", "brain-key")
    sys.modules.pop("arty_discord", None)
    yield importlib.import_module("arty_discord")
    sys.modules.pop("arty_discord", None)


class BrainReply:
    def read(self):
        return b'{"choices": [{"message": {"content": "Hi."}}]}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_brain_is_told_the_discord_bot_is_asking(bot, monkeypatch):
    sent = {}

    def record(request, timeout=None):
        sent.update(json.loads(request.data))
        return BrainReply()

    monkeypatch.setattr(bot.urllib.request, "urlopen", record)
    assert bot.ask_brain([{"role": "user", "content": "hi"}]) == "Hi."
    assert sent["client"] == "discord"
