import io
import json
import socket
import socketserver
import sys
import threading
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process.tts_func import engine, server_voice

RATE = 22050
PCM_ONE_SECOND = b"\x01\x00" * RATE


class FakeWyoming(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, reply):
        super().__init__(("127.0.0.1", 0), FakeWyomingHandler)
        self.reply = reply
        self.asked = []
        self.connections = 0

    @property
    def port(self):
        return self.server_address[1]


class FakeWyomingHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.server.connections += 1
        header = json.loads(self.rfile.readline())
        data = json.loads(self.rfile.read(header["data_length"])) if header.get("data_length") else {}
        self.server.asked.append((header["type"], data))
        for kind, body, payload in self.server.reply(header["type"], data):
            encoded = json.dumps(body).encode() if body else b""
            head = {"type": kind, "version": "1.0.0", "data_length": len(encoded), "payload_length": len(payload)}
            self.wfile.write(json.dumps(head).encode() + b"\n" + encoded + payload)
        self.wfile.flush()


def speaks_one_second(kind, data):
    fmt = {"rate": RATE, "width": 2, "channels": 1}
    yield "audio-start", fmt, b""
    yield "audio-chunk", fmt, PCM_ONE_SECOND
    yield "audio-chunk", fmt, PCM_ONE_SECOND[: RATE]
    yield "audio-stop", None, b""


def refuses(kind, data):
    yield "error", {"text": "Arty couldn't speak that text."}, b""


@pytest.fixture
def wyoming():
    started = []

    def start(reply):
        server = FakeWyoming(reply)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


@pytest.fixture
def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(autouse=True)
def fresh_voice_state(monkeypatch):
    monkeypatch.setattr(server_voice, "_down_until", 0.0)
    engine.forget_kokoro()
    yield
    engine.forget_kokoro()


def use_server(monkeypatch, port, timeout=2.0):
    monkeypatch.setattr(server_voice, "_server_cfg", lambda: {"host": "127.0.0.1", "port": port, "timeout_seconds": timeout})


def wav_facts(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as reader:
        return reader.getframerate(), reader.getnchannels(), reader.getsampwidth(), reader.getnframes()


class FakeKokoro:
    created = 0

    def __init__(self, model, voices):
        FakeKokoro.created += 1
        self.model = model

    def create_timed(self, text, voice, speed, lang):
        import numpy as np

        return np.zeros(2400, dtype="float32"), 24000, []

    def get_voices(self):
        return ["bm_george"]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def local_voice(monkeypatch, tmp_path):
    FakeKokoro.created = 0
    clock = Clock()
    full = tmp_path / "kokoro-v1.0.onnx"
    voices = tmp_path / "voices-v1.0.bin"
    full.write_bytes(b"x")
    voices.write_bytes(b"x")
    monkeypatch.setattr(engine, "_new_kokoro", lambda model, voices_file: FakeKokoro(model, voices_file))
    monkeypatch.setattr(engine, "_kokoro_cfg", lambda: {"model_path": str(full), "voices_path": str(voices), "voice": "bm_george", "lang": "en-gb"})
    monkeypatch.setattr(engine, "_clock", clock)
    monkeypatch.setattr(engine, "_save_audio", lambda audio, rate: b"WAV")
    monkeypatch.setattr(server_voice, "_server_cfg", lambda: {})
    return clock, tmp_path


def test_the_server_voice_asks_for_the_text_and_returns_its_audio_as_a_wav(wyoming, monkeypatch):
    server = wyoming(speaks_one_second)
    use_server(monkeypatch, server.port)
    wav = server_voice.speak("Hello there.")
    assert server.asked == [("synthesize", {"text": "Hello there."})]
    assert wav_facts(wav) == (RATE, 1, 2, RATE + RATE // 2)


def test_a_voice_that_is_not_configured_is_never_called(monkeypatch):
    monkeypatch.setattr(server_voice, "_server_cfg", lambda: {})
    assert server_voice.speak("Hello.") is None
    monkeypatch.setattr(server_voice, "_server_cfg", lambda: {"host": "  "})
    assert server_voice.speak("Hello.") is None


def test_an_unreachable_server_gives_none_and_is_not_tried_again_until_the_pause_is_over(free_port, monkeypatch):
    use_server(monkeypatch, free_port, timeout=0.5)
    clock = Clock()
    monkeypatch.setattr(server_voice, "_clock", clock)
    attempts = []
    real_connect = socket.create_connection
    monkeypatch.setattr(server_voice.socket, "create_connection", lambda *args, **kwargs: attempts.append(args) or real_connect(*args, **kwargs))
    assert server_voice.speak("One.") is None
    assert server_voice.speak("Two.") is None
    assert len(attempts) == 1
    clock.now += server_voice.RETRY_AFTER_SECONDS + 1
    assert server_voice.speak("Three.") is None
    assert len(attempts) == 2


def test_a_server_that_comes_back_is_used_again_after_the_pause(wyoming, free_port, monkeypatch):
    use_server(monkeypatch, free_port, timeout=0.5)
    clock = Clock()
    monkeypatch.setattr(server_voice, "_clock", clock)
    assert server_voice.speak("One.") is None
    server = wyoming(speaks_one_second)
    use_server(monkeypatch, server.port)
    assert server_voice.speak("Two.") is None
    clock.now += server_voice.RETRY_AFTER_SECONDS + 1
    assert server_voice.speak("Three.") is not None


def test_an_error_from_the_server_fails_this_line_only(wyoming, monkeypatch):
    server = wyoming(refuses)
    use_server(monkeypatch, server.port)
    with pytest.raises(server_voice.ServerVoiceError, match="couldn't speak"):
        server_voice.speak_or_raise("Hello.")
    assert server_voice.speak("Hello.") is None
    assert server.connections == 2


def test_audio_that_is_not_16_bit_is_refused(wyoming, monkeypatch):
    def eight_bit(kind, data):
        fmt = {"rate": RATE, "width": 1, "channels": 1}
        yield "audio-start", fmt, b""
        yield "audio-chunk", fmt, b"\x01" * 100
        yield "audio-stop", None, b""

    server = wyoming(eight_bit)
    use_server(monkeypatch, server.port)
    with pytest.raises(server_voice.ServerVoiceError, match="16-bit"):
        server_voice.speak_or_raise("Hello.")


def test_a_server_that_sends_no_audio_is_an_error(wyoming, monkeypatch):
    def silent(kind, data):
        yield "audio-start", {"rate": RATE, "width": 2, "channels": 1}, b""
        yield "audio-stop", None, b""

    server = wyoming(silent)
    use_server(monkeypatch, server.port)
    with pytest.raises(server_voice.ServerVoiceError, match="no audio"):
        server_voice.speak_or_raise("Hello.")


def test_a_server_that_hangs_up_early_is_an_error(wyoming, monkeypatch):
    def hangs_up(kind, data):
        yield "audio-start", {"rate": RATE, "width": 2, "channels": 1}, b""

    server = wyoming(hangs_up)
    use_server(monkeypatch, server.port)
    with pytest.raises(server_voice.ServerVoiceError, match="ended before"):
        server_voice.speak_or_raise("Hello.")


def test_speech_goes_to_the_server_and_the_local_voice_is_never_loaded(wyoming, monkeypatch, local_voice):
    server = wyoming(speaks_one_second)
    use_server(monkeypatch, server.port)
    wav, visemes = engine.synthesize("Good evening.")
    assert wav_facts(wav)[:3] == (RATE, 1, 2)
    assert visemes == []
    assert FakeKokoro.created == 0
    assert engine.local_voice_loaded() is False


def test_speech_falls_back_to_the_local_voice_when_the_server_is_unreachable(free_port, monkeypatch, local_voice):
    use_server(monkeypatch, free_port, timeout=0.5)
    wav, _visemes = engine.synthesize("Good evening.")
    assert wav == b"WAV"
    assert FakeKokoro.created == 1
    assert engine.local_voice_loaded() is True


def test_speech_falls_back_to_the_local_voice_when_the_server_refuses_the_line(wyoming, monkeypatch, local_voice):
    server = wyoming(refuses)
    use_server(monkeypatch, server.port)
    wav, _visemes = engine.synthesize("Good evening.")
    assert wav == b"WAV"


def test_the_local_voice_is_not_loaded_until_something_is_spoken(local_voice):
    assert FakeKokoro.created == 0
    assert engine.local_voice_loaded() is False
    engine.synthesize("Good evening.")
    assert FakeKokoro.created == 1


def test_the_local_voice_is_unloaded_after_ten_minutes_idle_and_loaded_again_when_needed(local_voice):
    clock, _folder = local_voice
    assert engine.IDLE_UNLOAD_SECONDS == 10 * 60
    engine.synthesize("Good evening.")
    clock.now += engine.IDLE_UNLOAD_SECONDS - 1
    assert engine.unload_if_idle() is False
    assert engine.local_voice_loaded() is True
    clock.now += 2
    assert engine.unload_if_idle() is True
    assert engine.local_voice_loaded() is False
    engine.synthesize("Good evening again.")
    assert FakeKokoro.created == 2


def test_speaking_again_restarts_the_idle_wait(local_voice):
    clock, _folder = local_voice
    engine.synthesize("One.")
    clock.now += engine.IDLE_UNLOAD_SECONDS - 10
    engine.synthesize("Two.")
    clock.now += engine.IDLE_UNLOAD_SECONDS - 10
    assert engine.unload_if_idle() is False
    assert engine.local_voice_loaded() is True


def test_the_local_voice_is_not_unloaded_while_it_is_speaking(local_voice):
    clock, _folder = local_voice
    with engine.local_voice_in_use():
        clock.now += engine.IDLE_UNLOAD_SECONDS * 3
        assert engine.unload_if_idle() is False
        assert engine.local_voice_loaded() is True
    assert engine.unload_if_idle() is False
    clock.now += engine.IDLE_UNLOAD_SECONDS + 1
    assert engine.unload_if_idle() is True


def test_the_idle_wait_can_be_set_in_the_config(local_voice, monkeypatch):
    clock, folder = local_voice
    settings = {"model_path": str(folder / "kokoro-v1.0.onnx"), "voices_path": str(folder / "voices-v1.0.bin"), "idle_unload_seconds": 30}
    monkeypatch.setattr(engine, "_kokoro_cfg", lambda: settings)
    engine.synthesize("Good evening.")
    clock.now += 31
    assert engine.unload_if_idle() is True


def test_the_smaller_model_file_is_used_when_it_is_next_to_the_configured_one(local_voice):
    _clock, folder = local_voice
    assert engine.model_file({"model_path": str(folder / "kokoro-v1.0.onnx")}) == folder / "kokoro-v1.0.onnx"
    light = folder / engine.LIGHT_MODEL_NAME
    light.write_bytes(b"x")
    assert engine.model_file({"model_path": str(folder / "kokoro-v1.0.onnx")}) == light
    assert engine.LIGHT_MODEL_NAME == "kokoro-v1.0.int8.onnx"


def test_the_local_voice_opens_the_smaller_file_when_there_is_one(local_voice):
    _clock, folder = local_voice
    light = folder / engine.LIGHT_MODEL_NAME
    light.write_bytes(b"x")
    opened = []
    engine._new_kokoro = lambda model, voices: opened.append(model) or FakeKokoro(model, voices)
    engine.synthesize("Good evening.")
    assert opened == [str(light)]


def test_the_status_line_does_not_give_the_server_address(monkeypatch):
    monkeypatch.setattr(server_voice, "_server_cfg", lambda: {"host": "192.0.2.16", "port": 10300})
    described = engine.describe()
    assert "192.168" not in described
    assert "server" in described and "kokoro" in described


def test_warming_up_asks_the_server_and_leaves_the_local_voice_alone(wyoming, monkeypatch, local_voice):
    server = wyoming(speaks_one_second)
    use_server(monkeypatch, server.port)
    engine.warmup()
    assert server.asked == [("synthesize", {"text": "Ready."})]
    assert FakeKokoro.created == 0


def test_warming_up_loads_the_local_voice_when_there_is_no_server(local_voice):
    engine.warmup()
    assert FakeKokoro.created == 1
