import io
import logging
import os
import sys
import wave
from pathlib import Path

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
sys.path.insert(0, os.path.dirname(__file__))

from barge_in import (FRAME_BYTES, FRAME_SECONDS, BargeInDetector, BargeInSettings, barge_in_from_settings,
                      resample, wav_to_reference)
from sim_audio import (FRAME, RATE, ambient, frames_of, level_db, load_phrase, scale_to_db, speaker_to_mic,
                       synthetic_speech, to_pcm)

PHRASES = os.environ.get("ARTY_TEST_PHRASES", "")
ECHO_DB = -28.0
LEAD_FRAMES = 0
ROOMS = {
    "near": dict(delay=0.08, reverb=0.2, distortion=0.0),
    "living": dict(delay=0.2, reverb=0.35, distortion=0.1),
    "big": dict(delay=0.35, reverb=0.5, distortion=0.25),
}


class Always:

    def __init__(self, answer):
        self.answer = answer

    def is_speech(self, frame):
        return self.answer


def wav_of(samples):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(to_pcm(samples))
    return buffer.getvalue()


def arty_reply(seed, seconds=7.0):
    return scale_to_db(synthetic_speech(seconds, seed), -20.0)


def drew_speech(seed=50, seconds=2.0, over_echo_db=0.0):
    return scale_to_db(synthetic_speech(seconds, seed), ECHO_DB + over_echo_db)


def run_reply(detector, room, reply_seed, drew=None, drew_at=3.0, burst=None, noise_seed=0, room_seed=0):
    reply = arty_reply(reply_seed)
    parameters = ROOMS[room]
    echo = scale_to_db(speaker_to_mic(reply, numpy.random.default_rng(room_seed), parameters["delay"],
                                      parameters["reverb"], 1.0, parameters["distortion"]), ECHO_DB)
    mic = echo + ambient(numpy.random.default_rng(noise_seed), len(echo))
    if drew is not None:
        start = int(drew_at * RATE)
        mic[start:start + len(drew)] += drew[:len(mic) - start]
    if burst is not None:
        start = int(drew_at * RATE)
        mic[start:start + len(burst)] += burst
    detector.reset_turn()
    detector.play(wav_of(reply))
    for index, frame in enumerate(frames_of(mic)):
        found = detector.feed(to_pcm(frame))
        if found:
            return (index + 1) * FRAME_SECONDS, found
    return None, None


def warmed_up(room, speech=None, settings=None):
    detector = BargeInDetector(settings, speech)
    for seed in (1, 2):
        run_reply(detector, room, seed)
    return detector


def test_her_own_voice_coming_back_through_the_mic_never_triggers_it():
    for room in ROOMS:
        detector = BargeInDetector()
        for seed in (1, 2, 3):
            at, _ = run_reply(detector, room, seed)
            assert at is None, (room, seed)


def test_talking_over_her_stops_her_within_a_fraction_of_a_second():
    for room in ROOMS:
        for over_echo_db in (0.0, -6.0):
            detector = warmed_up(room)
            speech = drew_speech(over_echo_db=over_echo_db)
            at, found = run_reply(detector, room, 4, speech, drew_at=3.0)
            assert at is not None, (room, over_echo_db)
            assert 3.0 <= at <= 3.0 + (0.5 if over_echo_db >= 0 else 0.8), (room, over_echo_db, at)
            assert found.audio


def test_a_short_knock_or_cough_does_not_stop_her():
    detector = warmed_up("living")
    burst = scale_to_db(numpy.random.default_rng(1).standard_normal(int(0.09 * RATE)), ECHO_DB + 10)
    at, _ = run_reply(detector, "living", 4, burst=burst)
    assert at is None


def test_nothing_triggers_before_she_has_been_talking_for_the_warm_up_time():
    settings = BargeInSettings(warmup_ms=1500)
    detector = warmed_up("near", settings=settings)
    early, _ = run_reply(detector, "near", 4, drew_speech(over_echo_db=12.0), drew_at=0.3)
    assert early is None or early >= 1.5


def test_without_any_reference_loud_sound_is_left_to_the_normal_listening_path():
    detector = BargeInDetector()
    loud = scale_to_db(synthetic_speech(3.0, 9), -20.0)
    for frame in frames_of(loud):
        assert detector.feed(to_pcm(frame)) is None
    assert detector.armed is False


def test_the_speech_check_can_veto_a_loud_noise():
    detector = warmed_up("near", speech=Always(False))
    at, _ = run_reply(detector, "near", 4, drew_speech(over_echo_db=12.0))
    assert at is None
    allowed = warmed_up("near", speech=Always(True))
    at, _ = run_reply(allowed, "near", 4, drew_speech(over_echo_db=12.0))
    assert at is not None


def test_the_audio_handed_over_starts_before_the_trigger_and_is_your_voice_not_her_echo():
    detector = warmed_up("living")
    speech = drew_speech(over_echo_db=0.0)
    at, found = run_reply(detector, "living", 4, speech, drew_at=3.0)
    frames_kept = len(found.audio) // FRAME_BYTES
    assert found.heard_frames == frames_kept
    assert FRAME_SECONDS * frames_kept <= BargeInSettings().preroll_ms / 1000 + FRAME_SECONDS
    pcm = numpy.frombuffer(found.audio, dtype="<i2").astype(numpy.float64) / 32768.0
    assert level_db(pcm[-FRAME * 3:]) > ECHO_DB - 8


def test_a_trigger_ends_the_turn_so_the_same_speech_does_not_fire_twice():
    detector = warmed_up("near")
    _, found = run_reply(detector, "near", 4, drew_speech(over_echo_db=6.0))
    assert found is not None
    assert detector.armed is False
    assert detector.feed(bytes(FRAME_BYTES)) is None


def test_reset_turn_drops_the_reference_still_waiting_to_be_played():
    detector = BargeInDetector()
    detector.play(wav_of(arty_reply(1)))
    detector.reset_turn()
    assert detector._pending.size == 0


def test_forgetting_the_room_throws_away_what_was_learned():
    detector = warmed_up("living")
    assert detector.canceller._weights.any()
    detector.forget_room()
    assert not detector.canceller._weights.any()
    assert detector.delay.lag is None


def test_a_long_gap_between_frames_clears_the_stale_reference():
    now = [0.0]
    detector = BargeInDetector(clock=lambda: now[0])
    detector.play(wav_of(arty_reply(1)))
    detector.feed(bytes(FRAME_BYTES))
    now[0] = 5.0
    detector.play(wav_of(arty_reply(2)[:RATE]))
    assert detector._pending.size == RATE


def test_audio_that_is_not_a_whole_number_of_frames_is_carried_over():
    detector = BargeInDetector()
    assert detector.feed(bytes(FRAME_BYTES // 2)) is None
    assert len(detector._pcm_rest) == FRAME_BYTES // 2
    assert detector.feed(bytes(FRAME_BYTES // 2 + 4)) is None
    assert len(detector._pcm_rest) == 4


def test_speech_in_another_sample_rate_is_resampled_to_16k_for_the_reference():
    tone = numpy.sin(2 * numpy.pi * 440 * numpy.arange(22050) / 22050)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(22050)
        writer.writeframes((tone * 16000).astype("<i2").tobytes())
    reference = wav_to_reference(buffer.getvalue())
    assert abs(len(reference) - RATE) <= 1
    assert abs(level_db(reference) - level_db(tone * 16000 / 32768)) < 0.5
    assert len(resample(numpy.zeros(0), 22050)) == 0


def test_stereo_speech_is_mixed_down_and_other_widths_are_refused():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(numpy.tile(numpy.array([1000, -1000], dtype="<i2"), 100).tobytes())
    assert numpy.allclose(wav_to_reference(buffer.getvalue()), 0)
    eight = io.BytesIO()
    with wave.open(eight, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(1)
        writer.setframerate(RATE)
        writer.writeframes(bytes(100))
    with pytest.raises(ValueError, match="16-bit"):
        wav_to_reference(eight.getvalue())


def test_speech_that_is_not_a_wav_is_logged_and_left_out_of_the_reference(caplog):
    detector = BargeInDetector()
    with caplog.at_level(logging.WARNING, logger="arty-voice"):
        detector.play(b"not a wav at all")
    assert detector._pending.size == 0
    assert "barge-in stays off" in caplog.text


def test_unreasonable_settings_are_refused():
    for bad in (dict(margin_db=0), dict(speech_ms=10), dict(warmup_ms=-1)):
        with pytest.raises(ValueError):
            BargeInSettings(**bad)


def test_barge_in_is_on_by_default_and_can_be_switched_off_or_tuned_from_the_environment():
    assert isinstance(barge_in_from_settings({}), BargeInDetector)
    assert barge_in_from_settings({"VOICE_BARGE_IN": " OFF "}) is None
    tuned = barge_in_from_settings({"VOICE_BARGE_MARGIN_DB": "12", "VOICE_BARGE_SPEECH_MS": "240",
                                    "VOICE_BARGE_WARMUP_MS": "900"})
    assert (tuned.settings.margin_db, tuned.settings.speech_ms, tuned.settings.warmup_ms) == (12.0, 240, 900)


def test_a_mistyped_barge_in_setting_names_the_setting():
    with pytest.raises(ValueError, match="VOICE_BARGE_IN must be on or off"):
        barge_in_from_settings({"VOICE_BARGE_IN": "maybe"})
    with pytest.raises(ValueError, match="VOICE_BARGE_MARGIN_DB must be a number"):
        barge_in_from_settings({"VOICE_BARGE_MARGIN_DB": "loud"})


def test_the_detector_keeps_up_with_real_time_by_a_wide_margin():
    import time
    detector = BargeInDetector()
    reply = arty_reply(1, 4.0)
    mic = reply * 0.1
    detector.play(wav_of(reply))
    started = time.perf_counter()
    count = 0
    for frame in frames_of(mic):
        detector.feed(to_pcm(frame))
        count += 1
    per_frame = (time.perf_counter() - started) / count
    assert per_frame < FRAME_SECONDS / 4


@pytest.mark.skipif(not PHRASES, reason="no recorded phrases (set ARTY_TEST_PHRASES)")
def test_with_recorded_phrases_her_echo_is_ignored_and_you_are_heard():
    folder = Path(PHRASES)
    reply = scale_to_db(numpy.concatenate([load_phrase(folder / f"{name}.wav") for name in ("dancing", "playlist", "spotify")]), -20.0)
    drew = scale_to_db(load_phrase(folder / "time.wav"), ECHO_DB)

    def play(detector, with_drew):
        echo = scale_to_db(speaker_to_mic(reply, numpy.random.default_rng(0), 0.2, 0.35, 1.0, 0.1), ECHO_DB)
        mic = echo + ambient(numpy.random.default_rng(1), len(echo))
        if with_drew:
            mic[int(3 * RATE):int(3 * RATE) + len(drew)] += drew
        detector.reset_turn()
        detector.play(wav_of(reply))
        for index, frame in enumerate(frames_of(mic)):
            if detector.feed(to_pcm(frame)):
                return (index + 1) * FRAME_SECONDS
        return None

    detector = BargeInDetector()
    assert play(detector, False) is None
    assert play(detector, False) is None
    at = play(detector, True)
    assert at is not None and 3.0 <= at <= 3.5


def test_the_first_reply_after_a_quiet_wait_does_not_stop_on_her_own_echo():
    for room in ROOMS:
        detector = BargeInDetector()
        for frame in frames_of(ambient(numpy.random.default_rng(1), int(2.5 * RATE))):
            assert detector.feed(to_pcm(frame)) is None
        at, _ = run_reply(detector, room, 1)
        assert at is None, room


def test_the_pause_after_a_barge_in_is_tunable_and_must_be_a_real_length():
    assert barge_in_from_settings({"VOICE_BARGE_PAUSE_MS": "600"}).pause_frames == 20
    assert barge_in_from_settings({}).pause_frames == 30
    with pytest.raises(ValueError):
        BargeInSettings(pause_ms=5)
    with pytest.raises(ValueError, match="VOICE_BARGE_PAUSE_MS must be a number"):
        barge_in_from_settings({"VOICE_BARGE_PAUSE_MS": "long"})
