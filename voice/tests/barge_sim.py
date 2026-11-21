import argparse
import io
import os
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))
sys.path.insert(0, os.path.dirname(__file__))

from barge_in import FRAME_SAMPLES, FRAME_SECONDS, BargeInDetector, BargeInSettings, WebrtcSpeech
from listening import UtteranceCollector
from sim_audio import RATE, ambient, frames_of, load_phrase, scale_to_db, speaker_to_mic, to_pcm

ARTY_PHRASES = ("dancing", "playlist", "spotify", "youtube_app", "garden")
DREW_PHRASES = ("time", "pause", "skip", "meaning", "game", "volume_up")
ROOMS = {
    "near speaker": dict(delay=0.08, reverb=0.20, distortion=0.0),
    "living room": dict(delay=0.20, reverb=0.35, distortion=0.1),
    "big room, cheap speaker": dict(delay=0.35, reverb=0.50, distortion=0.25),
}
ECHO_DB = -28.0
LEAD_SECONDS = 0.4
GAP_SECONDS = 0.25
ONSET_DB_BELOW_PEAK = 30.0
QUIET_WAIT_SECONDS = 2.5


@dataclass
class Result:
    triggered_at: float | None
    onset: float | None

    @property
    def latency(self) -> float | None:
        if self.triggered_at is None or self.onset is None:
            return None
        return self.triggered_at - self.onset


def phrase_folder() -> Path:
    folder = os.environ.get("ARTY_TEST_PHRASES", "")
    if not folder:
        raise SystemExit("set ARTY_TEST_PHRASES to a folder of phrase WAV files")
    return Path(folder)


def arty_reply(folder: Path, names, gap=GAP_SECONDS) -> numpy.ndarray:
    parts = []
    for name in names:
        parts += [load_phrase(folder / f"{name}.wav"), numpy.zeros(int(gap * RATE))]
    return scale_to_db(numpy.concatenate(parts), -20.0)


def speech_onset(speech: numpy.ndarray) -> float:
    window = int(0.01 * RATE)
    peak = numpy.abs(speech).max()
    for start in range(0, len(speech) - window, window):
        if numpy.abs(speech[start:start + window]).max() > peak * 10 ** (-ONSET_DB_BELOW_PEAK / 20):
            return start / RATE
    return 0.0


def make_detector(settings: BargeInSettings | None = None) -> BargeInDetector:
    return BargeInDetector(settings, WebrtcSpeech())


def play_reply(detector: BargeInDetector, reference, mic) -> Result:
    detector.play(to_wav(reference))
    triggered = None
    for index, frame in enumerate(frames_of(mic)):
        if detector.feed(to_pcm(frame)):
            triggered = (index + 1) * FRAME_SECONDS
            break
    return Result(triggered, None)


def to_wav(samples: numpy.ndarray) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(to_pcm(samples))
    return buffer.getvalue()


def scenario(folder, room, seed, drew_db_over_echo=None, drew_name="time", drew_at=2.0, detector=None,
             reply_names=ARTY_PHRASES[:3], drew_signal=None):
    rng = numpy.random.default_rng(seed)
    room_rng = numpy.random.default_rng(seed // 100)
    reply = arty_reply(folder, reply_names)
    reference = numpy.concatenate((numpy.zeros(int(LEAD_SECONDS * RATE)), reply))
    parameters = ROOMS[room]
    echo = speaker_to_mic(reference, room_rng, parameters["delay"], parameters["reverb"], 1.0, parameters["distortion"])
    echo = scale_to_db(echo, ECHO_DB)
    mic = echo + ambient(rng, len(echo))
    onset = None
    if drew_db_over_echo is not None:
        speech = drew_signal if drew_signal is not None else load_phrase(folder / f"{drew_name}.wav")
        speech = scale_to_db(speech, ECHO_DB + drew_db_over_echo)
        start = int(drew_at * RATE)
        stop = min(len(mic), start + len(speech))
        mic[start:stop] += speech[:stop - start]
        onset = drew_at + speech_onset(speech)
    detector = detector or make_detector()
    detector.reset_turn()
    result = play_reply(detector, reference[int(LEAD_SECONDS * RATE):], mic[int(LEAD_SECONDS * RATE):])
    result.onset = None if onset is None else onset - LEAD_SECONDS
    return result


def quiet_then_reply(folder, room, seed, detector, names) -> Result:
    rng = numpy.random.default_rng(seed)
    parameters = ROOMS[room]
    reply = arty_reply(folder, names)
    echo = scale_to_db(speaker_to_mic(reply, numpy.random.default_rng(seed // 100), parameters["delay"],
                                      parameters["reverb"], 1.0, parameters["distortion"]), ECHO_DB)
    wait = ambient(rng, int(QUIET_WAIT_SECONDS * RATE))
    mic = numpy.concatenate((wait, echo + ambient(rng, len(echo))))
    quiet_frames = len(wait) // FRAME_SAMPLES
    triggered = None
    for index, frame in enumerate(frames_of(mic)):
        if index == quiet_frames:
            detector.play(to_wav(reply))
        if detector.feed(to_pcm(frame)):
            triggered = (index - quiet_frames + 1) * FRAME_SECONDS
            break
    return Result(triggered, None)


def false_trigger_table(folder, seeds):
    print("Echo only (Arty talking, nobody else): replies cut short by her own echo")
    print(f"  {'room':26s} first reply after a quiet wait   later replies")
    for room in ROOMS:
        first = later = later_runs = 0
        for seed in range(seeds):
            detector = make_detector()
            first += quiet_then_reply(folder, room, seed * 100, detector, ARTY_PHRASES[:3]).triggered_at is not None
            for turn in range(1, 4):
                names = ARTY_PHRASES[turn:turn + 3]
                later_runs += 1
                later += scenario(folder, room, seed * 100 + turn, detector=detector, reply_names=names).triggered_at is not None
        print(f"  {room:26s} {first}/{seeds:<31d} {later}/{later_runs}")


def latency_table(folder, seeds):
    print("The user talks over Arty 3 s into her answer: from their first sound to the trigger (ms)")
    print(f"  {'room':26s} {'user vs echo':>12s}  {'filter already learned':34s} first answer (cold)")
    for room in ROOMS:
        warm_detectors = []
        for seed in range(seeds):
            detector = make_detector()
            for turn in range(2):
                scenario(folder, room, seed * 100 + turn, detector=detector, reply_names=ARTY_PHRASES[turn:turn + 3])
            warm_detectors.append(detector)
        for over in (-6, 0, 6, 12):
            warm, cold = [], []
            for seed in range(seeds):
                for drew in DREW_PHRASES[:2]:
                    warm.append(scenario(folder, room, seed * 100 + 60, over, drew, 3.0, warm_detectors[seed]).latency)
                    cold.append(scenario(folder, room, seed * 100 + 60, over, drew, 3.0, make_detector()).latency)
            print(f"  {room:26s} {over:+9d} dB  {summary(warm):34s} {summary(cold)}")


def deployed_listening():
    models = os.environ.get("ARTY_TEST_MODELS", "")
    if not models:
        return None
    from turn_end import load_listening
    return load_listening({"SMART_TURN_MODEL": f"{models}/smart-turn-v3.2-cpu.onnx",
                           "SILERO_VAD_MODEL": f"{models}/silero_vad.onnx", "COMMAND_ENGINE": "whisper"})


def quietest_point(speech: numpy.ndarray) -> int:
    window = int(0.1 * RATE)
    first, last = int(len(speech) * 0.3), int(len(speech) * 0.75)
    starts = range(first, last - window, window // 4)
    energy = [float(numpy.mean(numpy.square(speech[start:start + window]))) for start in starts]
    return list(starts)[int(numpy.argmin(energy))] + window // 2


def requests_heard(collector, audio, wait_longer_ms=None) -> int:
    if wait_longer_ms:
        collector.wait_longer_before_judging(wait_longer_ms // 30)
    found = []
    for frame in frames_of(audio):
        found += collector.feed(to_pcm(frame))
    return len(found)


PAUSE_LENGTHS = (0.3, 0.5, 0.7, 1.0, 1.5)
FIRST_CHECKS_AFTER_BARGE_MS = (500, 700, 900, 1100)


def pause_table(folder):
    listening = deployed_listening()
    print("The user pauses in the middle of a request: requests heard (1 is right, 2 means they were cut off)")
    checks = "  ".join(f"{ms} ms" for ms in FIRST_CHECKS_AFTER_BARGE_MS)
    print(f"  {'pause':8s} {'silence rule':14s} {'Smart Turn, normal':20s} Smart Turn after a barge-in, first check at: {checks}")
    speech = load_phrase(folder / "playlist.wav")
    middle = quietest_point(speech)
    for pause in PAUSE_LENGTHS:
        gap = numpy.zeros(int(pause * RATE))
        audio = numpy.concatenate((numpy.zeros(int(0.3 * RATE)), speech[:middle], gap, speech[middle:], numpy.zeros(int(2.0 * RATE))))
        rule = requests_heard(UtteranceCollector(), audio)
        if listening is None:
            print(f"  {pause:.1f} s    {rule:<14d} not run (set ARTY_TEST_MODELS)")
            continue
        normal = requests_heard(listening.new_collector(), audio)
        after = "  ".join(f"{requests_heard(listening.new_collector(), audio, ms):<{len(str(ms)) + 3}d}" for ms in FIRST_CHECKS_AFTER_BARGE_MS)
        print(f"  {pause:.1f} s    {rule:<14d} {normal:<20d} {' ' * 44}{after}")


def speed_line(folder):
    detector = make_detector()
    reply = arty_reply(folder, ARTY_PHRASES[:3])
    detector.play(to_wav(reply))
    started = time.perf_counter()
    count = 0
    for frame in frames_of(reply * 0.1):
        detector.feed(to_pcm(frame))
        count += 1
    print(f"Cost: {(time.perf_counter() - started) / count * 1000:.2f} ms of CPU per 30 ms of audio")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=3)
    arguments = parser.parse_args()
    folder = phrase_folder()
    false_trigger_table(folder, arguments.seeds)
    latency_table(folder, arguments.seeds)
    pause_table(folder)
    speed_line(folder)


def summary(values) -> str:
    found = [v for v in values if v is not None]
    if not found:
        return f"0/{len(values)} heard"
    ms = sorted(int(v * 1000) for v in found)
    return f"{len(found)}/{len(values)} heard, median {ms[len(ms) // 2]}, max {ms[-1]}"


if __name__ == "__main__":
    main()
