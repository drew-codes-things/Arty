import asyncio
import logging
import os
import sys
import threading
import time
import types
import wave
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
os.environ.setdefault("HUB_TOKEN", "hub-test")
os.environ.setdefault("ARTY_API_KEY", "brain-test")
sys.modules.setdefault("faster_whisper", types.SimpleNamespace(WhisperModel=object))

import numpy
import pytest

import arty_voice
from listening import FRAME_BYTES, MIN_SPEECH_FRAMES, PRE_ROLL_FRAMES, WHISPER_RATE, UtteranceCollector, Utterance
from live_text import LiveTranscript, LiveTextFailed, MoonshineEar
from turn_end import load_listening

TEST_MODELS = os.environ.get("ARTY_TEST_MODELS", "")
PHRASES = os.environ.get("ARTY_TEST_PHRASES", "")
MOONSHINE_FOLDER = "moonshine-tiny-streaming-en"
SAMPLE_BYTES = 2


def speech(seconds: float) -> bytes:
    samples = numpy.arange(int(seconds * WHISPER_RATE))
    return (8000 * numpy.sin(2 * numpy.pi * 220 * samples / WHISPER_RATE)).astype(numpy.int16).tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(seconds * WHISPER_RATE) * SAMPLE_BYTES)


class LoudSpeech:

    def is_speech(self, frame: bytes) -> bool:
        return any(frame)


class FakeStream:

    def __init__(self, text="what's the time", fail=False):
        self.text = text
        self.fail = fail
        self.samples = 0
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def add_audio(self, audio, sample_rate=16000):
        assert sample_rate == WHISPER_RATE
        assert all(-1.0 <= value <= 1.0 for value in audio[:50])
        if self.fail:
            raise RuntimeError("engine failed")
        self.samples += len(audio)

    def stop(self):
        self.stopped = True
        return types.SimpleNamespace(lines=[types.SimpleNamespace(text=part) for part in self.text.split("|")])

    def close(self):
        self.closed = True


class FakeEar:

    def __init__(self, **stream_options):
        self.stream_options = stream_options
        self.transcripts: list[LiveTranscript] = []
        self.streams: list[FakeStream] = []

    def start(self) -> LiveTranscript:
        def open_stream():
            self.streams.append(FakeStream(**self.stream_options))
            return self.streams[-1]
        self.transcripts.append(LiveTranscript(open_stream, threading.Lock()))
        return self.transcripts[-1]


def wait_until(check, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_a_live_transcript_hears_every_frame_and_returns_the_text():
    ear = FakeEar(text="Arty, what's| the time?")
    live = ear.start()
    for _ in range(5):
        live.add(speech(0.03)[:FRAME_BYTES])
    assert live.finish(timeout=2) == "Arty, what's the time?"
    [stream] = ear.streams
    assert stream.started and stream.stopped and stream.closed
    assert stream.samples == 5 * FRAME_BYTES // SAMPLE_BYTES


def test_a_cancelled_transcript_stops_without_finishing():
    ear = FakeEar()
    live = ear.start()
    live.add(speech(0.03)[:FRAME_BYTES])
    live.cancel()
    wait_until(lambda: ear.streams and ear.streams[0].closed)
    assert not ear.streams[0].stopped


def test_an_abandoned_transcript_gives_up_after_a_quiet_spell():
    ear = FakeEar()
    live = LiveTranscript(lambda: ear.streams.append(FakeStream()) or ear.streams[-1], threading.Lock(),
                          idle_seconds=0.05)
    live.add(speech(0.03)[:FRAME_BYTES])
    wait_until(lambda: ear.streams and ear.streams[0].closed)
    with pytest.raises(LiveTextFailed):
        live.finish(timeout=1)


def test_an_engine_error_reaches_whoever_asks_for_the_text():
    live = FakeEar(fail=True).start()
    live.add(speech(0.03)[:FRAME_BYTES])
    with pytest.raises(LiveTextFailed, match="engine failed"):
        live.finish(timeout=2)


def test_the_collector_streams_the_turn_to_the_live_transcript():
    ear = FakeEar()
    collector = UtteranceCollector(speech=LoudSpeech(), live=ear)
    [utterance] = collector.feed(silence(0.5) + speech(1.5) + silence(1.0))
    assert isinstance(utterance, Utterance)
    assert utterance.live is ear.transcripts[0]
    assert utterance.live.finish(timeout=2) == "what's the time"
    assert ear.streams[0].samples * SAMPLE_BYTES == len(utterance)


def test_a_discarded_noise_cancels_its_live_transcript():
    ear = FakeEar()
    collector = UtteranceCollector(speech=LoudSpeech(), live=ear)
    noise = speech((MIN_SPEECH_FRAMES - 4) * 0.03)
    assert collector.feed(noise + silence(1.0)) == []
    wait_until(lambda: ear.streams and ear.streams[0].closed)
    assert not ear.streams[0].stopped


def test_resetting_the_collector_cancels_a_turn_in_progress():
    ear = FakeEar()
    collector = UtteranceCollector(speech=LoudSpeech(), live=ear)
    collector.feed(speech(1.0))
    collector.reset()
    wait_until(lambda: ear.streams and ear.streams[0].closed)
    assert not ear.streams[0].stopped


def test_without_a_live_ear_utterances_are_plain_audio():
    collector = UtteranceCollector(speech=LoudSpeech())
    [utterance] = collector.feed(speech(1.5) + silence(1.0))
    assert utterance.live is None
    assert len(utterance) >= PRE_ROLL_FRAMES


class RecordingSenses:

    def __init__(self, wake_text="hey arty", command_text="hey arty what's the time"):
        self.wake_model, self.command_model = object(), object()
        self.wake_text, self.command_text = wake_text, command_text
        self.used: list[str] = []

    def transcribe(self, model, audio, beam_size, primer=None):
        self.used.append("wake" if model is self.wake_model else "command")
        return self.wake_text if model is self.wake_model else self.command_text


class Quiet:

    async def play(self, wav):
        pass

    async def state(self, name, text="", seconds=0):
        pass

    async def wake_changed(self, enabled):
        pass


def live_utterance(text=None, fail=False) -> Utterance:
    ear = FakeEar(text=text, fail=fail) if text is not None or fail else FakeEar()
    live = ear.start()
    audio = speech(1.5)
    live.add(audio)
    return Utterance(audio, live)


def test_the_command_comes_from_the_live_transcript_when_there_is_one():
    senses = RecordingSenses()
    assistant = arty_voice.Assistant(senses, Quiet(), None)
    command = asyncio.run(assistant.command_from(live_utterance("Hey Arty, play the next song"), "hey arty"))
    assert command == "play the next song"
    assert senses.used == []


def test_a_failed_live_transcript_falls_back_to_whisper(caplog):
    senses = RecordingSenses()
    assistant = arty_voice.Assistant(senses, Quiet(), None)
    with caplog.at_level(logging.WARNING):
        command = asyncio.run(assistant.command_from(live_utterance(fail=True), "hey arty"))
    assert command == "what's the time"
    assert senses.used == ["command"]
    assert "live transcript" in caplog.text


def test_an_empty_live_transcript_falls_back_to_whisper():
    senses = RecordingSenses()
    assistant = arty_voice.Assistant(senses, Quiet(), None)
    assert asyncio.run(assistant.command_from(live_utterance(""), "hey arty")) == "what's the time"
    assert senses.used == ["command"]


def test_plain_audio_still_uses_whisper():
    senses = RecordingSenses()
    assistant = arty_voice.Assistant(senses, Quiet(), None)
    assert asyncio.run(assistant.command_from(speech(1.5), "hey arty")) == "what's the time"
    assert senses.used == ["command"]


def test_whisper_setting_keeps_the_old_command_transcription():
    listening = load_listening({"VOICE_TURN_END": "silence", "VOICE_VAD": "webrtc", "COMMAND_ENGINE": "whisper"})
    assert listening.new_collector().live is None


def test_an_unknown_command_engine_fails_loudly():
    with pytest.raises(ValueError, match="COMMAND_ENGINE"):
        load_listening({"VOICE_TURN_END": "silence", "VOICE_VAD": "webrtc", "COMMAND_ENGINE": "guess"})


def test_a_missing_moonshine_model_falls_back_to_whisper(tmp_path, caplog):
    settings = {"VOICE_TURN_END": "silence", "VOICE_VAD": "webrtc", "MOONSHINE_MODEL": str(tmp_path / "gone")}
    with caplog.at_level(logging.WARNING):
        assert load_listening(settings).new_collector().live is None
    assert "gone" in caplog.text


def phrase_pcm(name: str) -> bytes:
    path = Path(PHRASES, f"{name}.wav")
    if not PHRASES or not path.exists():
        pytest.skip(f"{path.name} is not recorded (set ARTY_TEST_PHRASES to its folder)")
    with wave.open(str(path)) as reader:
        rate = reader.getframerate()
        samples = numpy.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(numpy.float32)
    wanted = int(len(samples) * WHISPER_RATE / rate)
    resampled = numpy.interp(numpy.linspace(0, len(samples) - 1, wanted), numpy.arange(len(samples)), samples)
    return resampled.astype(numpy.int16).tobytes()


def test_real_moonshine_transcribes_a_spoken_request_as_it_streams():
    pytest.importorskip("moonshine_voice")
    moonshine = Path(TEST_MODELS, MOONSHINE_FOLDER)
    if not TEST_MODELS or not moonshine.is_dir():
        pytest.skip("the Moonshine model is not downloaded (set ARTY_TEST_MODELS to its parent folder)")
    ear = MoonshineEar.load(moonshine)
    collector = UtteranceCollector(speech=LoudSpeech(), live=ear)
    found = collector.feed(silence(0.5) + phrase_pcm("time") + silence(1.0))
    assert found
    text = found[0].live.finish(timeout=10)
    assert "time" in text.lower()


def test_a_turn_dropped_from_a_full_queue_releases_its_live_transcript():
    async def run():
        assistant = arty_voice.Assistant(RecordingSenses(), Quiet(), None)
        ear = FakeEar()
        for _ in range(assistant.utterances.maxsize + 1):
            assistant.hear(Utterance(speech(0.5), ear.start()))
        return ear

    ear = asyncio.run(run())
    wait_until(lambda: len(ear.streams) == 5 and ear.streams[0].closed)
    assert not any(stream.closed for stream in ear.streams[1:])


def test_a_turn_heard_while_muted_releases_its_live_transcript():
    async def run():
        assistant = arty_voice.Assistant(RecordingSenses(), Quiet(), None)
        assistant.speaking = True
        ear = FakeEar()
        assistant.hear(Utterance(speech(0.5), ear.start()))
        worker = asyncio.create_task(assistant.handle_utterances())
        await asyncio.sleep(0.05)
        worker.cancel()
        return ear

    ear = asyncio.run(run())
    wait_until(lambda: ear.streams and ear.streams[0].closed)


def test_a_live_transcript_without_the_name_is_checked_again_with_whisper():
    senses = RecordingSenses()
    assistant = arty_voice.Assistant(senses, Quiet(), None, bare_wake=True)
    command = asyncio.run(assistant.command_from(live_utterance("RTE play the next song"), "arty play the next song"))
    assert command == "what's the time"
    assert senses.used == ["command"]
