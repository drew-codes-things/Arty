import io
import logging
import time
import wave
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

import numpy

from echo_cancel import DEFAULT_PARTITIONS, DEFAULT_STEP, DelayTracker, EchoCanceller
from listening import WebrtcSpeech

SAMPLE_RATE = 16000
FRAME_SAMPLES = 480
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
FRAME_BYTES = FRAME_SAMPLES * 2
PCM_SCALE = 32768.0
SILENCE_DB = -120.0
REFERENCE_ACTIVE_DB = -62.0
IDLE_FRAMES_BEFORE_FORGETTING = 40
LOUD_LEARNING_RATE = 0.2
DIVERGED_MARGIN_DB = 6.0
DIVERGED_MIN_DB = -50.0
POWER_MEMORY = 0.8
LONGEST_GAP_SECONDS = 0.5
UNLOCATED_WARMUP_FACTOR = 3

log = logging.getLogger("arty-voice")


class SpeechDetector(Protocol):

    def is_speech(self, frame: bytes) -> bool: ...


@dataclass(frozen=True)
class BargeInSettings:
    margin_db: float = 9.0
    speech_ms: int = 180
    warmup_ms: int = 600
    pause_ms: int = 900
    min_level_db: float = -52.0
    floor_seconds: float = 2.5
    floor_percentile: int = 85
    miss_decay: float = 0.5
    preroll_ms: int = 450
    partitions: int = DEFAULT_PARTITIONS
    step: float = DEFAULT_STEP

    def __post_init__(self):
        if self.margin_db <= 0:
            raise ValueError(f"the barge-in margin must be above 0 dB, not {self.margin_db}")
        if self.speech_ms < int(FRAME_SECONDS * 1000):
            raise ValueError(f"the barge-in speech time must be at least one frame, not {self.speech_ms} ms")
        if self.pause_ms < int(FRAME_SECONDS * 1000):
            raise ValueError(f"the barge-in pause must be at least one frame, not {self.pause_ms} ms")
        if self.warmup_ms < 0:
            raise ValueError(f"the barge-in warm-up cannot be negative, not {self.warmup_ms} ms")


@dataclass(frozen=True)
class BargeIn:
    audio: bytes
    heard_frames: int


def level_db(samples: numpy.ndarray) -> float:
    if len(samples) == 0:
        return SILENCE_DB
    return 20.0 * float(numpy.log10(numpy.sqrt(numpy.mean(numpy.square(samples))) + 1e-9))


def to_float(frame: bytes) -> numpy.ndarray:
    return numpy.frombuffer(frame, dtype="<i2").astype(numpy.float64) / PCM_SCALE


def to_pcm(samples: numpy.ndarray) -> bytes:
    return (numpy.clip(samples, -1.0, 1.0) * (PCM_SCALE - 1)).astype("<i2").tobytes()


def resample(samples: numpy.ndarray, from_rate: int, to_rate: int = SAMPLE_RATE) -> numpy.ndarray:
    if from_rate == to_rate or len(samples) == 0:
        return samples
    wanted = int(round(len(samples) * to_rate / from_rate))
    spectrum = numpy.fft.rfft(samples)
    resized = numpy.zeros(wanted // 2 + 1, dtype=numpy.complex128)
    shared = min(len(resized), len(spectrum))
    resized[:shared] = spectrum[:shared]
    return numpy.fft.irfft(resized, n=wanted) * (wanted / len(samples))


def wav_to_reference(wav: bytes) -> numpy.ndarray:
    with wave.open(io.BytesIO(wav)) as reader:
        rate, channels, width = reader.getframerate(), reader.getnchannels(), reader.getsampwidth()
        if width != 2:
            raise ValueError(f"only 16-bit speech can be used as the echo reference, not {width * 8}-bit")
        raw = numpy.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(numpy.float64) / PCM_SCALE
    if channels > 1:
        raw = raw.reshape(-1, channels).mean(axis=1)
    return resample(raw, rate)


class BargeInDetector:

    def __init__(self, settings: BargeInSettings | None = None, speech: SpeechDetector | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.settings = settings or BargeInSettings()
        self.speech = speech
        self.clock = clock
        self._last_heard = clock()
        self.canceller = EchoCanceller(FRAME_SAMPLES, self.settings.partitions, self.settings.step)
        self.delay = DelayTracker(FRAME_SAMPLES)
        self.speech_frames = max(1, round(self.settings.speech_ms / 1000 / FRAME_SECONDS))
        self.warmup_frames = round(self.settings.warmup_ms / 1000 / FRAME_SECONDS)
        self._floor_window: deque[float] = deque(maxlen=max(4, round(self.settings.floor_seconds / FRAME_SECONDS)))
        self._preroll: deque[bytes] = deque(maxlen=max(1, round(self.settings.preroll_ms / 1000 / FRAME_SECONDS)))
        self.reset_turn()

    def reset_turn(self) -> None:
        self._pending = numpy.zeros(0)
        self._pcm_rest = b""
        self._playing_frames = 0
        self._idle_frames = 0
        self._score = 0.0
        self._mic_power = 0.0
        self._residual_power = 0.0
        self._preroll.clear()
        self.canceller.clear_history()
        self.delay.clear_signals()

    def forget_room(self) -> None:
        self.reset_turn()
        self.canceller.reset()
        self.delay.reset()
        self._floor_window.clear()

    def _forget_stale_signals(self) -> None:
        now = self.clock()
        if now - self._last_heard > LONGEST_GAP_SECONDS:
            self.reset_turn()
        self._last_heard = now

    def play(self, wav: bytes) -> None:
        self._forget_stale_signals()
        try:
            reference = wav_to_reference(wav)
        except (wave.Error, EOFError, ValueError) as error:
            log.warning("can't use this speech as the echo reference, barge-in stays off for it: %s", error)
            return
        self._pending = numpy.concatenate((self._pending, reference))

    @property
    def pause_frames(self) -> int:
        return round(self.settings.pause_ms / 1000 / FRAME_SECONDS)

    @property
    def armed(self) -> bool:
        if self._playing_frames < self.warmup_frames:
            return False
        return self.delay.lag is not None or self._playing_frames >= UNLOCATED_WARMUP_FACTOR * self.warmup_frames

    @property
    def echo_delay_ms(self) -> float:
        return (self.delay.shift + self.canceller.strongest_partition() * FRAME_SAMPLES) * 1000 / SAMPLE_RATE

    def feed(self, audio: bytes) -> BargeIn | None:
        self._forget_stale_signals()
        data = self._pcm_rest + audio
        whole = len(data) - len(data) % FRAME_BYTES
        self._pcm_rest = data[whole:]
        for start in range(0, whole, FRAME_BYTES):
            found = self._frame(data[start:start + FRAME_BYTES])
            if found:
                self._pcm_rest = b""
                return found
        return None

    def _next_reference(self) -> numpy.ndarray:
        reference, self._pending = self._pending[:FRAME_SAMPLES], self._pending[FRAME_SAMPLES:]
        if len(reference) < FRAME_SAMPLES:
            reference = numpy.concatenate((reference, numpy.zeros(FRAME_SAMPLES - len(reference))))
        return reference

    def _track_playback(self, reference_db: float) -> None:
        if reference_db > REFERENCE_ACTIVE_DB:
            if self._playing_frames == 0:
                self._floor_window.clear()
            self._playing_frames += 1
            self._idle_frames = 0
            return
        self._idle_frames += 1
        if self._idle_frames > IDLE_FRAMES_BEFORE_FORGETTING:
            self._playing_frames = 0

    def _has_diverged(self, mic_db: float, residual_db: float) -> bool:
        self._mic_power = POWER_MEMORY * self._mic_power + (1 - POWER_MEMORY) * 10 ** (mic_db / 10)
        self._residual_power = POWER_MEMORY * self._residual_power + (1 - POWER_MEMORY) * 10 ** (residual_db / 10)
        residual_smooth = 10 * numpy.log10(self._residual_power + 1e-12)
        mic_smooth = 10 * numpy.log10(self._mic_power + 1e-12)
        return residual_smooth > DIVERGED_MIN_DB and residual_smooth > mic_smooth + DIVERGED_MARGIN_DB

    def _floor(self) -> float | None:
        if len(self._floor_window) < self.warmup_frames // 2 + 1:
            return None
        ordered = sorted(self._floor_window)
        return ordered[min(len(ordered) - 1, int(len(ordered) * self.settings.floor_percentile / 100))]

    def _frame(self, frame: bytes) -> BargeIn | None:
        mic = to_float(frame)
        reference = self._next_reference()
        residual = self.canceller.cancel(mic, self.delay.push(reference, mic))
        mic_db, residual_db = level_db(mic), level_db(residual)
        self._track_playback(level_db(reference))
        if self._has_diverged(mic_db, residual_db):
            self.canceller.reset()
            residual, residual_db = mic, mic_db
        residual_pcm = to_pcm(residual)
        self._preroll.append(residual_pcm)
        floor = self._floor()
        loud = (floor is not None and residual_db > floor + self.settings.margin_db
                and residual_db > self.settings.min_level_db)
        self.canceller.learn(LOUD_LEARNING_RATE if loud else 1.0)
        self._floor_window.append(residual_db)
        voiced = loud and (self.speech is None or self.speech.is_speech(residual_pcm))
        self._score = self._score + 1.0 if voiced else max(0.0, self._score - self.settings.miss_decay)
        if self.armed and self._score >= self.speech_frames:
            found = BargeIn(b"".join(self._preroll), len(self._preroll))
            self.reset_turn()
            return found
        return None


def barge_in_from_settings(settings: Mapping[str, str]) -> BargeInDetector | None:
    switch = settings.get("VOICE_BARGE_IN", "on").strip().lower()
    if switch not in ("on", "off"):
        raise ValueError(f"VOICE_BARGE_IN must be on or off, not {switch!r}")
    if switch == "off":
        log.info("barge-in is off, Arty will not stop when talked over")
        return None
    defaults = BargeInSettings()
    chosen = BargeInSettings(
        margin_db=number(settings, "VOICE_BARGE_MARGIN_DB", defaults.margin_db, float),
        speech_ms=number(settings, "VOICE_BARGE_SPEECH_MS", defaults.speech_ms, int),
        warmup_ms=number(settings, "VOICE_BARGE_WARMUP_MS", defaults.warmup_ms, int),
        pause_ms=number(settings, "VOICE_BARGE_PAUSE_MS", defaults.pause_ms, int))
    log.info("barge-in is on: %.0f dB above her echo for %d ms", chosen.margin_db, chosen.speech_ms)
    return BargeInDetector(chosen, WebrtcSpeech())


def number(settings: Mapping[str, str], name: str, default, kind):
    raw = settings.get(name)
    if raw is None:
        return default
    try:
        return kind(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, not {raw!r}") from None
