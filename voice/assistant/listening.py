import logging
from collections import deque
from typing import Protocol

import numpy
import webrtcvad

HUB_RATE = 48000
WHISPER_RATE = 16000
DOWNSAMPLE = HUB_RATE // WHISPER_RATE
SAMPLE_BYTES = 2
FRAME_MS = 30
FRAME_BYTES = WHISPER_RATE * FRAME_MS // 1000 * 2
PRE_ROLL_FRAMES = 10
START_FRAMES = 3
END_SILENCE_FRAMES = 27
MIN_SPEECH_FRAMES = 10
MAX_FRAMES = 15 * 1000 // FRAME_MS
VAD_AGGRESSIVENESS = 2
PAUSE_FRAMES = 10
RECHECK_FRAMES = 10
TURN_WAIT_FRAMES = 1600 // FRAME_MS

log = logging.getLogger("arty-voice")


class SpeechDetector(Protocol):

    def is_speech(self, frame: bytes) -> bool: ...


class TurnJudge(Protocol):

    def finished(self, audio: bytes) -> bool: ...


class LiveText(Protocol):

    def add(self, audio: bytes) -> None: ...

    def finish(self) -> str: ...

    def cancel(self) -> None: ...


class LiveEar(Protocol):

    def start(self) -> LiveText: ...


class Utterance(bytes):

    live: LiveText | None

    def __new__(cls, audio: bytes, live: LiveText | None = None):
        utterance = super().__new__(cls, audio)
        utterance.live = live
        return utterance


class WebrtcSpeech:

    def __init__(self, aggressiveness: int = VAD_AGGRESSIVENESS):
        self.vad = webrtcvad.Vad(aggressiveness)

    def is_speech(self, frame: bytes) -> bool:
        return self.vad.is_speech(frame, WHISPER_RATE)


class Downsampler:

    def __init__(self):
        self.leftover = numpy.zeros(0, dtype=numpy.int16)
        self.split_byte = b""

    def feed(self, chunk: bytes) -> bytes:
        data = self.split_byte + chunk
        whole = len(data) - len(data) % SAMPLE_BYTES
        self.split_byte = data[whole:]
        samples = numpy.concatenate([self.leftover, numpy.frombuffer(data[:whole], dtype=numpy.int16)])
        usable = len(samples) - len(samples) % DOWNSAMPLE
        self.leftover = samples[usable:]
        averaged = samples[:usable].reshape(-1, DOWNSAMPLE).mean(axis=1)
        return averaged.astype(numpy.int16).tobytes()


class UtteranceCollector:

    def __init__(self, aggressiveness: int = VAD_AGGRESSIVENESS, speech: SpeechDetector | None = None,
                 turn: TurnJudge | None = None, turn_wait_frames: int = TURN_WAIT_FRAMES,
                 live: LiveEar | None = None):
        self.speech = speech or WebrtcSpeech(aggressiveness)
        self.turn = turn
        self.turn_wait_frames = turn_wait_frames
        self.live = live
        self.live_text: LiveText | None = None
        self.reset()

    def reset(self) -> None:
        self._drop_live_text()
        self.pending = b""
        self.pre_roll: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
        self.frames: list[bytes] = []
        self.speech_run = 0
        self.speech_frames = 0
        self.silence_run = 0
        self.skipping_rest = False
        self.judge_failed = False

    def feed(self, audio: bytes) -> list[Utterance]:
        self.pending += audio
        finished = []
        while len(self.pending) >= FRAME_BYTES:
            frame, self.pending = self.pending[:FRAME_BYTES], self.pending[FRAME_BYTES:]
            utterance = self._frame(frame, self.speech.is_speech(frame))
            if utterance:
                finished.append(utterance)
        return finished

    def _frame(self, frame: bytes, is_speech: bool) -> Utterance | None:
        if self.skipping_rest:
            return self._skip_until_quiet(is_speech)
        if not self.frames:
            return self._waiting(frame, is_speech)
        self.frames.append(frame)
        if self.live_text:
            self.live_text.add(frame)
        self.speech_frames += is_speech
        self.silence_run = 0 if is_speech else self.silence_run + 1
        quiet = self._turn_has_ended()
        if not quiet and len(self.frames) < MAX_FRAMES:
            return None
        utterance = None
        if self.speech_frames >= MIN_SPEECH_FRAMES:
            utterance, self.live_text = Utterance(b"".join(self.frames), self.live_text), None
        self._drop_live_text()
        self.skipping_rest = not quiet
        self.frames, self.speech_frames, self.silence_run, self.speech_run = [], 0, 0, 0
        self.judge_failed = False
        self.pre_roll.clear()
        return utterance

    def _turn_has_ended(self) -> bool:
        if self.turn is None or self.judge_failed or self.speech_frames < MIN_SPEECH_FRAMES:
            return self.silence_run >= END_SILENCE_FRAMES
        if self.silence_run >= self.turn_wait_frames:
            return True
        if self._is_judging_point():
            return self._judge_says_finished()
        return False

    def _is_judging_point(self) -> bool:
        since_pause = self.silence_run - PAUSE_FRAMES
        return since_pause >= 0 and since_pause % RECHECK_FRAMES == 0

    def _judge_says_finished(self) -> bool:
        try:
            return self.turn.finished(b"".join(self.frames))
        except Exception:
            log.exception("the end-of-turn model failed, using the silence rule for this turn")
            self.judge_failed = True
            return self.silence_run >= END_SILENCE_FRAMES

    def _skip_until_quiet(self, is_speech: bool) -> None:
        self.silence_run = 0 if is_speech else self.silence_run + 1
        if self.silence_run >= END_SILENCE_FRAMES:
            self.skipping_rest = False
            self.silence_run = 0
        return None

    def _waiting(self, frame: bytes, is_speech: bool) -> None:
        self.pre_roll.append(frame)
        self.speech_run = self.speech_run + 1 if is_speech else 0
        if self.speech_run >= START_FRAMES:
            self.frames = list(self.pre_roll)
            self.speech_frames = self.speech_run
            self._start_live_text()
        return None

    def _start_live_text(self) -> None:
        if self.live is None:
            return
        self.live_text = self.live.start()
        self.live_text.add(b"".join(self.frames))

    def _drop_live_text(self) -> None:
        if self.live_text is not None:
            self.live_text.cancel()
        self.live_text = None


def to_float(audio: bytes) -> numpy.ndarray:
    return numpy.frombuffer(audio, dtype=numpy.int16).astype(numpy.float32) / 32768.0
