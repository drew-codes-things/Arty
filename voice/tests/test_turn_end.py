import logging
import math
import os
import sys
import wave
from pathlib import Path

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))

from listening import (END_SILENCE_FRAMES, FRAME_BYTES, MIN_SPEECH_FRAMES, PAUSE_FRAMES, RECHECK_FRAMES,
                       TURN_WAIT_FRAMES, WHISPER_RATE, UtteranceCollector, WebrtcSpeech, to_float)
from turn_end import (SILERO_CHUNK, SILERO_CONTEXT, SMART_TURN_SAMPLES, SileroSpeech, SmartTurn,
                      load_listening)

TEST_MODELS = os.environ.get("ARTY_TEST_MODELS", "")
PHRASES = os.environ.get("ARTY_TEST_PHRASES", "")
FRAME_SECONDS = 0.03
SPOKEN_LEVEL = 650
TAIL_SAMPLES = WHISPER_RATE // 10


def tone(seconds: float) -> bytes:
    samples = numpy.arange(int(seconds * WHISPER_RATE))
    wave_form = 8000 * numpy.sin(2 * math.pi * 220 * samples / WHISPER_RATE) * (
        1 + 0.5 * numpy.sin(2 * math.pi * 3 * samples / WHISPER_RATE))
    return wave_form.astype(numpy.int16).tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(seconds * WHISPER_RATE) * 2)


def frames(count: int) -> float:
    return count * FRAME_SECONDS


class ScriptedJudge:

    def __init__(self, *answers):
        self.answers = list(answers)
        self.heard: list[bytes] = []

    def finished(self, audio: bytes) -> bool:
        self.heard.append(audio)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class BrokenJudge:

    def __init__(self):
        self.calls = 0

    def finished(self, audio: bytes) -> bool:
        self.calls += 1
        raise RuntimeError("model failed")


class LoudSpeech:

    def is_speech(self, frame: bytes) -> bool:
        return any(frame)


def judged(judge) -> UtteranceCollector:
    return UtteranceCollector(speech=LoudSpeech(), turn=judge)


class DeafSpeech:

    def is_speech(self, frame: bytes) -> bool:
        return False


def test_a_finished_turn_ends_at_a_short_pause():
    collector = judged(ScriptedJudge(True))
    found = collector.feed(silence(0.5) + tone(1.5) + silence(frames(PAUSE_FRAMES + 1)))
    assert len(found) == 1
    assert len(found[0]) >= len(tone(1.5))


def test_without_a_judge_a_short_pause_does_not_end_the_turn():
    collector = UtteranceCollector(speech=LoudSpeech())
    assert collector.feed(silence(0.5) + tone(1.5) + silence(frames(PAUSE_FRAMES + 1))) == []


def test_an_unfinished_turn_waits_past_the_old_silence_rule():
    collector = judged(ScriptedJudge(False))
    assert collector.feed(tone(1.5) + silence(frames(END_SILENCE_FRAMES + 2))) == []
    assert len(collector.feed(silence(frames(TURN_WAIT_FRAMES)))) == 1


def test_speech_after_a_pause_joins_the_same_turn():
    judge = ScriptedJudge(False, True)
    collector = judged(judge)
    found = collector.feed(tone(1) + silence(0.4) + tone(1) + silence(frames(PAUSE_FRAMES + 1)))
    assert len(found) == 1
    assert len(found[0]) >= len(tone(1) + silence(0.4) + tone(1))
    assert len(judge.heard) == 2


def test_the_judge_hears_the_turn_so_far_once_per_check():
    judge = ScriptedJudge(False)
    collector = judged(judge)
    collector.feed(tone(1.5) + silence(frames(PAUSE_FRAMES + RECHECK_FRAMES + 1)))
    assert len(judge.heard) == 2
    assert len(judge.heard[1]) - len(judge.heard[0]) == RECHECK_FRAMES * FRAME_BYTES
    assert judge.heard[0].endswith(bytes(PAUSE_FRAMES * FRAME_BYTES))


def test_a_short_noise_is_not_judged_and_ends_on_the_old_rule():
    judge = ScriptedJudge(False)
    collector = judged(judge)
    noise_frames = MIN_SPEECH_FRAMES - 4
    assert collector.feed(tone(frames(noise_frames)) + silence(frames(END_SILENCE_FRAMES + 1))) == []
    assert judge.heard == []
    assert len(collector.feed(tone(1.5) + silence(frames(PAUSE_FRAMES + 1)))) == 0
    assert len(judge.heard) == 1


def test_a_failing_judge_falls_back_to_the_old_silence_rule(caplog):
    judge = BrokenJudge()
    collector = judged(judge)
    with caplog.at_level(logging.ERROR):
        assert collector.feed(tone(1.5) + silence(frames(END_SILENCE_FRAMES - 2))) == []
        assert len(collector.feed(silence(frames(3)))) == 1
    assert judge.calls == 1
    assert "end-of-turn" in caplog.text


def test_an_early_end_does_not_skip_the_next_request():
    collector = judged(ScriptedJudge(True))
    pause = silence(frames(PAUSE_FRAMES + 1))
    assert len(collector.feed(tone(1.5) + pause + tone(1.5) + pause)) == 2


def test_the_cap_still_cuts_a_long_turn_with_a_judge():
    collector = judged(ScriptedJudge(False))
    found = collector.feed(tone(16) + silence(frames(TURN_WAIT_FRAMES + 1)))
    assert len(found) == 1


def test_the_collector_uses_the_speech_detector_it_is_given():
    collector = UtteranceCollector(0, speech=DeafSpeech())
    assert collector.feed(tone(1.5) + silence(1.5)) == []


def test_webrtc_speech_hears_a_tone_and_not_silence():
    assert WebrtcSpeech(0).is_speech(tone(0.03)[:FRAME_BYTES])
    assert not WebrtcSpeech(0).is_speech(silence(0.03)[:FRAME_BYTES])


class FakeSileroSession:

    def __init__(self, probability: float):
        self.probability = probability
        self.calls: list[dict] = []

    def run(self, outputs, feeds):
        self.calls.append(feeds)
        next_state = feeds["state"] + 1
        return numpy.array([[self.probability]], dtype=numpy.float32), next_state


def test_silero_waits_for_a_whole_chunk_then_scores_it():
    session = FakeSileroSession(0.9)
    detector = SileroSpeech(session)
    first = detector.is_speech(tone(0.03)[:FRAME_BYTES])
    assert first is False and session.calls == []
    assert detector.is_speech(tone(0.06)[FRAME_BYTES:2 * FRAME_BYTES]) is True
    [feeds] = session.calls
    assert feeds["input"].shape == (1, SILERO_CONTEXT + SILERO_CHUNK)
    assert feeds["input"].dtype == numpy.float32
    assert feeds["sr"].dtype == numpy.int64 and int(feeds["sr"]) == WHISPER_RATE
    assert not feeds["input"][0, :SILERO_CONTEXT].any()


def test_silero_carries_its_state_and_context_between_chunks():
    session = FakeSileroSession(0.1)
    detector = SileroSpeech(session)
    audio = tone(0.1)
    for start in range(0, 3 * FRAME_BYTES * 2, FRAME_BYTES):
        assert detector.is_speech(audio[start:start + FRAME_BYTES]) is False
    first, second = session.calls[0], session.calls[1]
    assert (second["state"] == first["state"] + 1).all()
    assert numpy.array_equal(second["input"][0, :SILERO_CONTEXT], first["input"][0, -SILERO_CONTEXT:])


class FakeTurnSession:

    def __init__(self, probability: float):
        self.probability = probability
        self.features = None

    def run(self, outputs, feeds):
        self.features = feeds["input_features"]
        return [numpy.array([[self.probability]], dtype=numpy.float32)]


class RecordingFeatures:

    def __init__(self):
        self.audio = None

    def __call__(self, audio):
        self.audio = audio
        return numpy.zeros((80, 800), dtype=numpy.float32)


def test_smart_turn_pads_short_audio_at_the_start_and_normalises_it():
    features = RecordingFeatures()
    session = FakeTurnSession(0.8)
    judge = SmartTurn(session, features)
    assert judge.finished(tone(1)) is True
    assert len(features.audio) == SMART_TURN_SAMPLES
    assert not features.audio[:WHISPER_RATE].std()
    assert abs(float(features.audio.mean())) < 1e-4
    assert abs(float(features.audio.std()) - 1) < 1e-3
    assert session.features.shape == (1, 80, 800)


def test_smart_turn_keeps_only_the_last_eight_seconds():
    features = RecordingFeatures()
    judge = SmartTurn(FakeTurnSession(0.2), features)
    audio = tone(4) + silence(5) + tone(1)
    assert judge.finished(audio) is False
    window = to_float(audio)[-SMART_TURN_SAMPLES:]
    expected = (window - window.mean()) / numpy.sqrt(window.var() + 1e-7)
    assert numpy.allclose(features.audio, expected, atol=1e-5)


def test_silence_settings_keep_the_old_listening():
    listening = load_listening({"VOICE_TURN_END": "silence", "VOICE_VAD": "webrtc"})
    collector = listening.new_collector()
    assert collector.turn is None
    assert isinstance(collector.speech, WebrtcSpeech)


def test_an_unknown_setting_fails_loudly():
    with pytest.raises(ValueError, match="VOICE_TURN_END"):
        load_listening({"VOICE_TURN_END": "guess"})
    with pytest.raises(ValueError, match="VOICE_VAD"):
        load_listening({"VOICE_VAD": "guess"})


def test_a_bad_wait_setting_fails_loudly():
    with pytest.raises(ValueError, match="VOICE_TURN_WAIT_MS"):
        load_listening({"VOICE_TURN_END": "silence", "VOICE_VAD": "webrtc", "VOICE_TURN_WAIT_MS": "soon"})


def test_missing_models_fall_back_to_the_old_listening(tmp_path, caplog):
    settings = {"SMART_TURN_MODEL": str(tmp_path / "none.onnx"), "SILERO_VAD_MODEL": str(tmp_path / "none.onnx")}
    with caplog.at_level(logging.WARNING):
        collector = load_listening(settings).new_collector()
    assert collector.turn is None
    assert isinstance(collector.speech, WebrtcSpeech)
    assert "none.onnx" in caplog.text


def model_path(name: str) -> Path:
    path = Path(TEST_MODELS, name)
    if not TEST_MODELS or not path.exists():
        pytest.skip(f"{name} is not downloaded (set ARTY_TEST_MODELS to its folder)")
    return path


def real_model_settings() -> dict:
    pytest.importorskip("onnxruntime")
    try:
        from faster_whisper.feature_extractor import FeatureExtractor
    except ImportError:
        pytest.skip("faster-whisper is not installed")
    assert FeatureExtractor
    return {"SMART_TURN_MODEL": str(model_path("smart-turn-v3.2-cpu.onnx")),
            "SILERO_VAD_MODEL": str(model_path("silero_vad.onnx"))}


def phrase_pcm(name: str) -> bytes:
    path = Path(PHRASES, f"{name}.wav")
    if not PHRASES or not path.exists():
        pytest.skip(f"{path.name} is not recorded (set ARTY_TEST_PHRASES to its folder)")
    with wave.open(str(path)) as reader:
        rate = reader.getframerate()
        samples = numpy.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(numpy.float32)
    wanted = int(len(samples) * WHISPER_RATE / rate)
    resampled = numpy.interp(numpy.linspace(0, len(samples) - 1, wanted), numpy.arange(len(samples)), samples)
    voiced = numpy.flatnonzero(numpy.abs(resampled) > SPOKEN_LEVEL)
    return resampled[:voiced[-1] + TAIL_SAMPLES].astype(numpy.int16).tobytes()


def test_real_silero_hears_a_spoken_phrase_and_not_silence():
    listening = load_listening(real_model_settings())
    speech = listening.new_collector().speech
    audio = phrase_pcm("meaning")
    heard = [speech.is_speech(audio[start:start + FRAME_BYTES]) for start in range(0, len(audio), FRAME_BYTES)
             if len(audio[start:start + FRAME_BYTES]) == FRAME_BYTES]
    assert sum(heard) > len(heard) // 3
    quiet = listening.new_collector().speech
    assert not any(quiet.is_speech(silence(0.03)[:FRAME_BYTES]) for _ in range(40))


def test_real_smart_turn_ends_a_whole_request_sooner_than_the_old_rule():
    listening = load_listening(real_model_settings())
    collector = listening.new_collector()
    spoken = phrase_pcm("meaning")
    pending = silence(0.5) + spoken
    assert collector.feed(pending) == []
    waited = 0
    while not collector.feed(silence(FRAME_SECONDS)):
        waited += 1
        assert waited < END_SILENCE_FRAMES
