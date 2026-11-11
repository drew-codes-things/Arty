import asyncio
import hashlib
import hmac
import os
import sys
import threading
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
os.environ.setdefault("HUB_TOKEN", "hub-test")
os.environ.setdefault("ARTY_API_KEY", "brain-test")
sys.modules.setdefault("faster_whisper", types.SimpleNamespace(WhisperModel=object))

import numpy
import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

import arty_voice
import headset
import headset_panels
import pairing
import satellite
from listening import FRAME_BYTES, MAX_FRAMES, WHISPER_RATE, Downsampler, UtteranceCollector


class FakeSenses:

    def wav(self, text):
        return b"RIFF" + text.encode()


class RecordingOutput:

    def __init__(self):
        self.states = []
        self.played = []

    async def play(self, wav):
        self.played.append(wav)

    async def state(self, name, text="", seconds=0):
        self.states.append(name)

    async def wake_changed(self, enabled):
        return None


class FakeSocket:

    def __init__(self, close_after=None):
        self.closed = False
        self.sent = []
        self.close_after = close_after

    async def send_json(self, message):
        self.sent.append(message)
        if self.close_after and len(self.sent) >= self.close_after:
            self.closed = True

    async def send_bytes(self, data):
        return None


def tone(seconds):
    samples = numpy.arange(int(seconds * WHISPER_RATE))
    wave = 8000 * numpy.sin(2 * numpy.pi * 220 * samples / WHISPER_RATE) * (1 + 0.5 * numpy.sin(2 * numpy.pi * 3 * samples / WHISPER_RATE))
    return wave.astype(numpy.int16).tobytes()


def silence(seconds):
    return bytes(int(seconds * WHISPER_RATE) * 2)


def test_a_failed_sentence_does_not_leave_the_assistant_deaf(monkeypatch):
    async def run():
        assistant = arty_voice.Assistant(FakeSenses(), RecordingOutput(), None)

        async def brain(question):
            yield "This first sentence is long enough to be spoken on its own right away. And a second one."

        async def broken_say(text):
            raise RuntimeError("the voice broke")

        monkeypatch.setattr(assistant, "ask_brain", brain)
        monkeypatch.setattr(assistant, "say", broken_say)
        await assistant.answer("hello")
        return assistant.speaking

    assert asyncio.run(run()) is False


def test_one_broken_turn_does_not_stop_the_assistant_hearing_the_next(monkeypatch):
    async def run():
        assistant = arty_voice.Assistant(FakeSenses(), RecordingOutput(), None)
        handled = []

        async def handle(utterance):
            if utterance == b"first":
                raise RuntimeError("whisper fell over")
            handled.append(utterance)

        monkeypatch.setattr(assistant, "handle_utterance", handle)
        worker = asyncio.create_task(assistant.handle_utterances())
        assistant.hear(b"first")
        assistant.hear(b"second")
        for _ in range(20):
            await asyncio.sleep(0)
        still_running = not worker.done()
        worker.cancel()
        return handled, still_running

    assert asyncio.run(run()) == ([b"second"], True)


def test_a_panel_refresh_error_does_not_freeze_the_panels(monkeypatch):
    class FlakyDashboard:
        calls = 0

        async def current(self):
            FlakyDashboard.calls += 1
            if FlakyDashboard.calls == 1:
                raise RuntimeError("brain hiccup")
            return {"type": "panels"}

    monkeypatch.setattr(headset, "REFRESH_SECONDS", 0)
    monkeypatch.setattr(headset, "PANEL_RETRY_SECONDS", 0, raising=False)
    ws = FakeSocket(close_after=2)
    asyncio.run(asyncio.wait_for(headset.keep_panels_fresh(FlakyDashboard(), headset.HeadsetOutput(ws)), 2))
    assert ws.sent == [{"type": "panels"}, {"type": "panels"}]


def test_a_headset_whose_listener_dies_is_disconnected_so_it_reconnects(monkeypatch):
    monkeypatch.setattr(headset, "HEADSET_TOKEN", "right-key")

    async def no_panels(self):
        return {"type": "panels"}

    async def dies(self):
        raise RuntimeError("listener crashed")

    monkeypatch.setattr(headset_panels.Dashboard, "current", no_panels)
    monkeypatch.setattr(arty_voice.Assistant, "handle_utterances", dies)

    async def run():
        client = TestClient(TestServer(headset.build_app(FakeSenses(), None)))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws/headset", headers={"Authorization": "Bearer right-key"})
            kinds = []
            for _ in range(10):
                message = await ws.receive(timeout=5)
                kinds.append(message.type)
                if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                    break
            return kinds[-1]
        finally:
            await client.close()

    assert asyncio.run(run()) in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)


@pytest.mark.parametrize("bad_id", [[1], {"a": 1}, None, "7", True])
def test_an_action_result_with_an_odd_id_is_ignored(bad_id):
    async def run():
        session = headset.HeadsetSession(FakeSenses(), None, FakeSocket())
        await session.handle_text('{"type": "action_result", "id": %s, "say": "x"}' % __import__("json").dumps(bad_id))
        return True

    assert asyncio.run(run())


def test_an_odd_byte_from_the_headset_does_not_shift_later_audio():
    session = headset.HeadsetSession(FakeSenses(), None, FakeSocket())
    session.hear(b"\x01\x02\x03")
    session.hear(b"\x04\x05")
    assert session.collector.pending == b"\x01\x02\x04\x05"


def test_the_downsampler_keeps_a_split_sample_for_the_next_chunk():
    downsampler = Downsampler()
    whole = numpy.full(6, 300, dtype=numpy.int16).tobytes()
    first = downsampler.feed(whole[:5])
    second = downsampler.feed(whole[5:])
    assert numpy.frombuffer(first + second, dtype=numpy.int16).tolist() == [300, 300]


def test_speech_longer_than_the_cap_does_not_become_a_second_wakeless_request():
    collector = UtteranceCollector(0)
    found = collector.feed(tone(MAX_FRAMES * 0.03 + 5) + silence(1.5) + silence(0.5))
    assert len(found) == 1
    assert len(found[0]) == MAX_FRAMES * FRAME_BYTES


def test_after_a_cut_the_next_request_is_heard_again():
    collector = UtteranceCollector(0)
    collector.feed(tone(MAX_FRAMES * 0.03 + 2) + silence(1.5))
    found = collector.feed(tone(1.5) + silence(1.5))
    assert len(found) == 1


@pytest.mark.parametrize("header", [b"[1, 2]\n", b'"text"\n', b'{"type": "x", "data": [1]}\n',
                                    b'{"type": "x", "data_length": [1]}\n', b'{"type": "x", "payload_length": "a"}\n'])
def test_a_wyoming_header_that_is_not_an_object_is_a_value_error(header):
    async def read():
        reader = asyncio.StreamReader()
        reader.feed_data(header)
        reader.feed_eof()
        return await satellite.read_event(reader)

    with pytest.raises(ValueError):
        asyncio.run(read())


def test_a_wyoming_body_that_is_not_an_object_is_a_value_error():
    async def read():
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"type": "x", "data_length": 3}\n[1]')
        reader.feed_eof()
        return await satellite.read_event(reader)

    with pytest.raises(ValueError):
        asyncio.run(read())


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_one_device_guessing_wrong_does_not_lock_out_another():
    codes = pairing.PairingCodes(Clock())
    code = codes.issue()
    for _ in range(pairing.MAX_WRONG_TRIES):
        assert codes.redeem("AAAA-AAAA", "192.0.2.66") == pairing.WRONG
    assert codes.redeem("AAAA-AAAA", "192.0.2.66") == pairing.LOCKED
    assert codes.redeem(code, "192.0.2.7") == pairing.OK


def test_wrong_codes_from_many_devices_still_pause_every_code():
    clock = Clock()
    codes = pairing.PairingCodes(clock)
    code = codes.issue()
    for attempt in range(pairing.MAX_WRONG_TRIES_EVERYWHERE):
        assert codes.redeem("AAAA-AAAA", f"192.0.2.{attempt // (pairing.MAX_WRONG_TRIES - 1)}") == pairing.WRONG
    assert codes.redeem(code, "192.0.2.250") == pairing.LOCKED
    clock.now += pairing.LOCKOUT_SECONDS + 1
    assert codes.redeem(code, "192.0.2.250") == pairing.OK


def test_the_wrong_guess_record_stays_small():
    clock = Clock()
    codes = pairing.PairingCodes(clock)
    for attempt in range(pairing.MAX_TRACKED_ADDRESSES * 3):
        codes.redeem("AAAA-AAAA", f"addr-{attempt}")
        clock.now += pairing.LOCKOUT_SECONDS / pairing.MAX_WRONG_TRIES_EVERYWHERE * 2
    assert len(codes.wrong_by_address) <= pairing.MAX_TRACKED_ADDRESSES


def identity(client, nonce):
    return client.get("/identity", params={"nonce": nonce})


def test_identity_proves_the_server_holds_the_key_without_revealing_it(monkeypatch):
    monkeypatch.setattr(headset, "HEADSET_TOKEN", "right-key")
    nonce = "0123456789abcdef0123456789ABCDEF"

    async def run():
        client = TestClient(TestServer(headset.build_app(None, None)))
        await client.start_server()
        try:
            good = await identity(client, nonce)
            body = await good.json()
            statuses = [(await identity(client, bad)).status
                        for bad in ("", "abc", "g" * 16, "a" * 129, "0123456789abcdef-")]
            missing = (await client.get("/identity")).status
            return good.status, body, statuses, missing
        finally:
            await client.close()

    status, body, statuses, missing = asyncio.run(run())
    expected = hmac.new(b"right-key", ("arty-identity:" + nonce).encode(), hashlib.sha256).hexdigest()
    assert status == 200 and body == {"proof": expected}
    assert "right-key" not in str(body)
    assert statuses == [400] * 5 and missing == 400


def test_a_wake_check_is_not_held_up_by_a_long_satellite_transcription(monkeypatch):
    release = threading.Event()

    class FakeModel:
        def __init__(self, name, **options):
            self.name = name

        def transcribe(self, audio, **options):
            if self.name == arty_voice.COMMAND_MODEL:
                release.wait(5)
            return [], None

    class FakeVoice:
        def __init__(self, path):
            pass

    monkeypatch.setattr(arty_voice, "WhisperModel", FakeModel)
    monkeypatch.setattr(arty_voice, "Voice", FakeVoice)
    senses = arty_voice.Senses()
    slow = threading.Thread(target=senses.transcribe, args=(senses.command_model, b"\x00\x00" * 10, 2))
    slow.start()
    quick = threading.Thread(target=senses.transcribe, args=(senses.wake_model, b"\x00\x00" * 10, 1))
    quick.start()
    quick.join(1)
    finished_first = not quick.is_alive()
    release.set()
    slow.join(5)
    quick.join(5)
    assert finished_first


def test_hub_output_survives_a_timeout():
    class TimingOut:
        def post(self, *args, **kwargs):
            raise asyncio.TimeoutError()

    asyncio.run(arty_voice.HubOutput(TimingOut()).play(b"RIFF"))


def test_a_loop_that_crashes_is_restarted_and_a_clean_finish_is_left_alone(monkeypatch):
    monkeypatch.setattr(arty_voice, "RETRY_SECONDS", (0,))
    runs = []

    async def flaky():
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("boom")

    asyncio.run(asyncio.wait_for(arty_voice.keep_running("test loop", flaky), 2))
    assert len(runs) == 3


def test_a_refused_hub_key_still_stops_the_service(monkeypatch):
    async def refused():
        raise SystemExit("refused")

    with pytest.raises(SystemExit):
        asyncio.run(arty_voice.keep_running("mic", refused))


def test_a_crash_after_a_long_healthy_run_waits_the_shortest_delay_again(monkeypatch):
    clock = [0.0]
    waits = []
    run_lengths = [0, 0, 0, arty_voice.HEALTHY_RUN_SECONDS + 1, 0]
    monkeypatch.setattr(arty_voice.time, "monotonic", lambda: clock[0])

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(arty_voice.asyncio, "sleep", no_wait)

    async def crashing():
        if not run_lengths:
            return
        clock[0] += run_lengths.pop(0)
        raise RuntimeError("boom")

    asyncio.run(arty_voice.keep_running("test loop", crashing))
    first, second, third = arty_voice.RETRY_SECONDS[:3]
    assert waits == [first, second, third, first, second]


class RecordingBrainHttp:
    def __init__(self):
        self.sent = {}

    def post(self, url, json=None, **_):
        self.sent = json
        return self

    async def __aenter__(self):
        self.status = 200

        async def lines():
            yield b'data: {"choices": [{"delta": {"content": "Hi."}}]}\n'
            yield b"data: [DONE]\n"

        self.content = lines()
        return self

    async def __aexit__(self, *exc):
        return False


def test_the_brain_is_told_the_voice_assistant_is_asking():
    async def run():
        http = RecordingBrainHttp()
        assistant = arty_voice.Assistant(FakeSenses(), RecordingOutput(), http)
        return [piece async for piece in assistant.ask_brain("hello")], http.sent

    pieces, sent = asyncio.run(run())
    assert pieces == ["Hi."]
    assert sent["client"] == "voice"
