import asyncio
import logging
import os
import sys
import threading
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
from listening import WHISPER_RATE, Utterance
from live_text import LiveTranscript
from wake_spotter import (EMBEDDING_SIZE, FIRST_EMBEDDING_SAMPLES, WINDOW_FRAMES, WINDOW_SAMPLES, WakeClassifier,
                          WakeSpotter, load_wake_spotter, wake_spotter_from_settings)

REPO = Path(__file__).resolve().parents[2]
SPOTTER_MODEL = REPO / "voice" / "models" / "hey-arty"
SAMPLES = os.environ.get("ARTY_TEST_WAKE_SAMPLES", "")
HOP = 1280


def classifier_params(hidden=4, seed=0):
    rng = numpy.random.default_rng(seed)
    size = WINDOW_FRAMES * EMBEDDING_SIZE
    return {"mean": rng.standard_normal(size).astype(numpy.float32),
            "scale": rng.uniform(0.5, 2, size).astype(numpy.float32),
            "hidden_weights": rng.standard_normal((size, hidden)).astype(numpy.float32) * 0.05,
            "hidden_bias": rng.standard_normal(hidden).astype(numpy.float32),
            "output_weights": rng.standard_normal((hidden, 1)).astype(numpy.float32),
            "output_bias": rng.standard_normal(1).astype(numpy.float32)}


def test_the_classifier_is_a_one_layer_network_with_a_sigmoid():
    params = classifier_params()
    windows = numpy.random.default_rng(1).standard_normal((3, WINDOW_FRAMES * EMBEDDING_SIZE)).astype(numpy.float32)
    hidden = numpy.maximum(0, ((windows - params["mean"]) / params["scale"]) @ params["hidden_weights"]
                           + params["hidden_bias"])
    expected = 1 / (1 + numpy.exp(-(hidden @ params["output_weights"] + params["output_bias"])[:, 0]))
    assert numpy.allclose(WakeClassifier(params).probabilities(windows), expected, atol=1e-6)


class RecordingEmbedding:

    def __init__(self):
        self.heard = []

    def __call__(self, samples):
        self.heard.append(samples)
        frames = (len(samples) - FIRST_EMBEDDING_SAMPLES) // HOP + 1
        return numpy.arange(frames, dtype=numpy.float32)[:, None].repeat(EMBEDDING_SIZE, axis=1)


class LastFrameClassifier:

    def __init__(self, loud_frame):
        self.loud_frame = loud_frame

    def probabilities(self, windows):
        last = windows.reshape(len(windows), WINDOW_FRAMES, EMBEDDING_SIZE)[:, -1, 0]
        return numpy.where(last == self.loud_frame, 0.9, 0.1)


def test_the_spotter_pads_the_start_so_the_first_words_are_judged():
    embedding = RecordingEmbedding()
    spotter = WakeSpotter(embedding, LastFrameClassifier(WINDOW_FRAMES - 1))
    audio = numpy.full(WHISPER_RATE, 1000, dtype=numpy.int16).tobytes()
    assert spotter.heard(audio)
    [samples] = embedding.heard
    assert len(samples) >= WINDOW_SAMPLES
    assert not samples[:WINDOW_SAMPLES - FIRST_EMBEDDING_SAMPLES].any()
    assert samples.dtype == numpy.float32 and samples.max() <= 1.0


def test_the_spotter_takes_the_best_window():
    spotter = WakeSpotter(RecordingEmbedding(), LastFrameClassifier(-1))
    audio = numpy.full(WHISPER_RATE * 2, 1000, dtype=numpy.int16).tobytes()
    assert spotter.score(audio) == pytest.approx(0.1)
    assert not spotter.heard(audio)


def test_whisper_setting_turns_the_spotter_off():
    assert wake_spotter_from_settings({"WAKE_ENGINE": "whisper"}) is None


def test_an_unknown_wake_engine_fails_loudly():
    with pytest.raises(ValueError, match="WAKE_ENGINE"):
        wake_spotter_from_settings({"WAKE_ENGINE": "guess"})


def test_a_missing_spotter_model_falls_back_to_whisper(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert wake_spotter_from_settings({"WAKE_SPOTTER_MODEL": str(tmp_path / "gone")}) is None
    assert "gone" in caplog.text


class Spotter:

    def __init__(self, hears: bool):
        self.hears = hears
        self.calls = 0

    def heard(self, audio: bytes) -> bool:
        self.calls += 1
        return self.hears


class RecordingSenses:

    def __init__(self):
        self.wake_model, self.command_model = object(), object()
        self.used: list[str] = []

    def transcribe(self, model, audio, beam_size, primer=None):
        self.used.append("wake" if model is self.wake_model else "command")
        return "hey arty what's the time"

    def wav(self, text):
        return b"RIFF" + text.encode()


class Quiet:

    def __init__(self):
        self.wake_states = []

    async def play(self, wav):
        pass

    async def state(self, name, text="", seconds=0):
        pass

    async def wake_changed(self, enabled):
        self.wake_states.append(enabled)


class FixedStream:

    def __init__(self, text):
        self.text = text

    def start(self):
        pass

    def add_audio(self, audio, sample_rate=16000):
        pass

    def stop(self):
        return types.SimpleNamespace(lines=[types.SimpleNamespace(text=self.text)])

    def close(self):
        pass


def live_utterance(text: str) -> Utterance:
    live = LiveTranscript(lambda: FixedStream(text), threading.Lock())
    return Utterance(bytes(WHISPER_RATE * 4), live)


def run_turn(spotter, utterance):
    senses, output, acted = RecordingSenses(), Quiet(), []

    async def act(command):
        acted.append(command)
        return True

    assistant = arty_voice.Assistant(senses, output, None, act=act, wake_spotter=spotter)
    asyncio.run(assistant.handle_utterance(utterance))
    return senses.used, acted, output, assistant


def test_a_spotted_wake_word_skips_the_whisper_checks():
    spotter = Spotter(True)
    used, acted, _, _ = run_turn(spotter, live_utterance("Hey Arty, play the next song"))
    assert acted == ["play the next song"]
    assert used == []
    assert spotter.calls == 1


def test_no_spotted_wake_word_keeps_the_whisper_wake_check():
    used, acted, _, _ = run_turn(Spotter(False), live_utterance("Hey Arty, play the next song"))
    assert used[0] == "wake"
    assert acted == ["play the next song"]


def test_a_spotted_wake_word_without_the_name_in_the_text_falls_back_to_whisper():
    used, acted, _, _ = run_turn(Spotter(True), live_utterance("hey party people"))
    assert used[0] == "wake"
    assert acted == ["what's the time"]


def test_a_spotted_switch_phrase_turns_the_wake_word_off():
    used, acted, output, assistant = run_turn(Spotter(True), live_utterance("Hey Arty off"))
    assert output.wake_states == [False]
    assert not assistant.wake_enabled
    assert acted == [] and used == []


def test_plain_audio_is_not_given_to_the_spotter():
    spotter = Spotter(True)
    used, _, _, _ = run_turn(spotter, bytes(WHISPER_RATE * 2))
    assert spotter.calls == 0
    assert used[0] == "wake"


def wav_samples(path: Path) -> bytes:
    with wave.open(str(path)) as reader:
        assert reader.getframerate() == WHISPER_RATE
        return reader.readframes(reader.getnframes())


def test_the_trained_spotter_hears_hey_arty_and_not_other_speech():
    pytest.importorskip("onnxruntime")
    if not (SPOTTER_MODEL / "hey_arty.npz").exists() or not SAMPLES or not Path(SAMPLES).is_dir():
        pytest.skip("the spotter model or its held-out samples are missing (set ARTY_TEST_WAKE_SAMPLES)")
    spotter = load_wake_spotter(SPOTTER_MODEL)
    wake = sorted(Path(SAMPLES).glob("wake-*.wav"))
    other = sorted(Path(SAMPLES).glob("other-*.wav"))
    assert wake and other
    heard = sum(spotter.heard(wav_samples(path)) for path in wake)
    false = sum(spotter.heard(wav_samples(path)) for path in other)
    assert heard >= len(wake) * 0.8
    assert false <= len(other) * 0.1
