import os
import sys

import numpy
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
sys.path.insert(0, os.path.dirname(__file__))

from echo_cancel import FRAME_SAMPLES, DelayTracker, EchoCanceller
from sim_audio import RATE, frames_of, level_db, room_response, speaker_to_mic, synthetic_speech


def reply_audio(seconds=8.0, seed=1):
    return synthetic_speech(seconds, seed) * 0.1


def run_canceller(canceller, reference, mic):
    residual = []
    for ref_frame, mic_frame in zip(frames_of(reference), frames_of(mic)):
        residual.append(canceller.cancel(mic_frame, ref_frame).copy())
        canceller.learn(1.0)
    return numpy.concatenate(residual)


def erle(mic, residual, start, stop):
    window = slice(int(start * RATE), int(stop * RATE))
    return level_db(mic[:len(residual)][window]) - level_db(residual[window])


def test_the_canceller_learns_a_room_and_removes_most_of_the_echo():
    rng = numpy.random.default_rng(3)
    reference = reply_audio(10.0)
    mic = speaker_to_mic(reference, rng, 0.03, 0.2, 0.5)
    residual = run_canceller(EchoCanceller(partitions=12), reference, mic)
    assert erle(mic, residual, 0, 1) < 12
    assert erle(mic, residual, 6, 10) > 15


def test_a_learned_room_is_kept_between_replies_but_reset_forgets_it():
    rng = numpy.random.default_rng(4)
    reference = reply_audio(10.0)
    mic = speaker_to_mic(reference, rng, 0.03, 0.2, 0.5)
    canceller = EchoCanceller(partitions=12)
    run_canceller(canceller, reference, mic)
    canceller.clear_history()
    again = run_canceller(canceller, reference, mic)
    assert erle(mic, again, 0, 2) > 15
    canceller.reset()
    forgotten = run_canceller(canceller, reference, mic)
    assert erle(mic, forgotten, 0, 1) < 6


def test_the_strongest_partition_marks_where_the_echo_arrives():
    rng = numpy.random.default_rng(5)
    reference = reply_audio(10.0)
    mic = speaker_to_mic(reference, rng, 0.15, 0.05, 0.5)
    canceller = EchoCanceller(partitions=12)
    run_canceller(canceller, reference, mic)
    assert abs(canceller.strongest_partition() * FRAME_SAMPLES / RATE - 0.15) < 0.05
    assert EchoCanceller().strongest_partition() == 0


def test_silence_in_and_out_changes_nothing_and_stays_finite():
    canceller = EchoCanceller()
    silent = numpy.zeros(FRAME_SAMPLES)
    for _ in range(50):
        residual = canceller.cancel(silent, silent)
        canceller.learn(1.0)
    assert numpy.all(numpy.isfinite(residual)) and not residual.any()


def test_a_tiny_reference_with_a_louder_mic_does_not_blow_the_filter_up():
    canceller = EchoCanceller()
    rng = numpy.random.default_rng(6)
    for _ in range(20):
        reference = rng.standard_normal(FRAME_SAMPLES) * 1e-5
        mic = rng.standard_normal(FRAME_SAMPLES) * 1e-3
        canceller.cancel(mic, reference)
        canceller.learn(1.0)
    assert float(numpy.abs(canceller._weights).max()) < 1.0


def test_frames_of_the_wrong_size_and_bad_settings_are_refused():
    canceller = EchoCanceller()
    with pytest.raises(ValueError, match="frames must be"):
        canceller.cancel(numpy.zeros(10), numpy.zeros(FRAME_SAMPLES))
    with pytest.raises(ValueError):
        EchoCanceller(step=2.5)
    with pytest.raises(ValueError):
        EchoCanceller(partitions=0)


def test_a_zero_rate_does_not_learn():
    canceller = EchoCanceller()
    rng = numpy.random.default_rng(7)
    canceller.cancel(rng.standard_normal(FRAME_SAMPLES) * 0.1, rng.standard_normal(FRAME_SAMPLES) * 0.1)
    canceller.learn(0.0)
    assert not canceller._weights.any()


def delayed_echo(delay_seconds, seconds=4.0):
    rng = numpy.random.default_rng(8)
    reference = reply_audio(seconds)
    return reference, speaker_to_mic(reference, rng, delay_seconds, 0.1, 0.5)


def track(tracker, reference, mic):
    for ref_frame, mic_frame in zip(frames_of(reference), frames_of(mic)):
        tracker.push(ref_frame, mic_frame)
    return tracker


def test_the_delay_tracker_finds_how_late_the_echo_comes():
    for delay in (0.05, 0.2, 0.45):
        tracker = track(DelayTracker(), *delayed_echo(delay))
        assert abs(tracker.lag / RATE - delay) < 0.01, delay
        assert tracker.confidence > tracker.min_confidence


def test_the_delay_tracker_leaves_the_reference_alone_until_it_knows_the_delay():
    tracker = DelayTracker()
    frame = numpy.ones(FRAME_SAMPLES)
    assert tracker.lag is None and tracker.shift == 0
    assert (tracker.push(frame, frame) == frame).all()


def test_the_reference_is_handed_on_early_by_the_delay_less_a_margin():
    reference, mic = delayed_echo(0.3)
    tracker = DelayTracker()
    outputs = []
    for ref_frame, mic_frame in zip(frames_of(reference), frames_of(mic)):
        outputs.append(tracker.push(ref_frame, mic_frame))
    aligned = numpy.concatenate(outputs[-20:])
    end = len(outputs) * FRAME_SAMPLES
    expected = reference[end - len(aligned) - tracker.shift:end - tracker.shift]
    assert numpy.allclose(aligned, expected)


def test_a_one_off_jump_in_the_estimate_is_not_followed_but_a_lasting_one_is():
    tracker = DelayTracker()
    tracker._accept(3000)
    tracker._accept(8000)
    assert tracker.lag == 3000
    tracker._accept(3050)
    assert tracker.lag == 3000 and tracker._candidate is None
    tracker._accept(8000)
    tracker._accept(8100)
    assert tracker.lag == 8100


def test_noise_that_has_nothing_to_do_with_the_reference_gives_no_delay():
    rng = numpy.random.default_rng(9)
    tracker = DelayTracker()
    for _ in range(100):
        tracker.push(rng.standard_normal(FRAME_SAMPLES) * 0.1, rng.standard_normal(FRAME_SAMPLES) * 0.1)
    assert tracker.lag is None


def test_the_room_response_puts_its_main_peak_at_the_delay():
    response = room_response(numpy.random.default_rng(1), 0.1, 0.2)
    assert int(numpy.argmax(numpy.abs(response))) == int(0.1 * RATE)
