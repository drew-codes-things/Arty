import importlib.util
import json
import os
import stat
import sys
import threading
import time
import types
from pathlib import Path

import pytest

CLIENT = Path(__file__).resolve().parent.parent / "hub" / "client" / "my_voice_client.py"
SMALL_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "


@pytest.fixture
def client(monkeypatch, tmp_path):
    websockets = types.ModuleType("websockets")
    exceptions = types.ModuleType("websockets.exceptions")
    exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
    exceptions.InvalidStatus = type("InvalidStatus", (Exception,), {})
    websockets.exceptions = exceptions
    monkeypatch.setitem(sys.modules, "websockets", websockets)
    monkeypatch.setitem(sys.modules, "websockets.exceptions", exceptions)
    real_read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: json.dumps({"hub": "http://hub", "token": "t"})
                        if self.name == "config.json" else real_read_text(self, *a, **k))
    spec = importlib.util.spec_from_file_location("my_voice_client_under_test", CLIENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "WINDOWS", False)
    return module


def install_slow_paplay(folder, monkeypatch):
    stub = folder / "paplay"
    stub.write_text("#!/bin/sh\nexec sleep 30\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")


def test_a_stop_message_is_recognised_and_other_text_is_not(client):
    assert client.is_stop_message('{"type": "stop"}') is True
    for other in ('{"type": "volume"}', "not json", "[1, 2]", "42", "null"):
        assert client.is_stop_message(other) is False, other


def test_stopping_ends_the_player_that_is_running_and_is_harmless_when_none_is(client, tmp_path, monkeypatch):
    install_slow_paplay(tmp_path, monkeypatch)
    client.stop_playback()
    playing = threading.Thread(target=client.play_wav, args=(SMALL_WAV,))
    playing.start()
    deadline = time.monotonic() + 5
    while client.PLAYER is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.PLAYER is not None
    began = time.monotonic()
    client.stop_playback()
    playing.join(5)
    assert not playing.is_alive()
    assert time.monotonic() - began < 2.0
    assert client.PLAYER is None
