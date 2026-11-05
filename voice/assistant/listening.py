from collections import deque

import numpy
import webrtcvad

HUB_RATE = 48000
WHISPER_RATE = 16000
DOWNSAMPLE = HUB_RATE // WHISPER_RATE
FRAME_MS = 30
FRAME_BYTES = WHISPER_RATE * FRAME_MS // 1000 * 2
PRE_ROLL_FRAMES = 10
START_FRAMES = 3
END_SILENCE_FRAMES = 27
MIN_SPEECH_FRAMES = 10
MAX_FRAMES = 15 * 1000 // FRAME_MS
VAD_AGGRESSIVENESS = 2


class Downsampler:

    def __init__(self):
        self.leftover = numpy.zeros(0, dtype=numpy.int16)

    def feed(self, chunk: bytes) -> bytes:
        samples = numpy.concatenate([self.leftover, numpy.frombuffer(chunk, dtype=numpy.int16)])
        usable = len(samples) - len(samples) % DOWNSAMPLE
        self.leftover = samples[usable:]
        averaged = samples[:usable].reshape(-1, DOWNSAMPLE).mean(axis=1)
        return averaged.astype(numpy.int16).tobytes()


class UtteranceCollector:

    def __init__(self, aggressiveness: int = VAD_AGGRESSIVENESS):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.reset()

    def reset(self) -> None:
        self.pending = b""
        self.pre_roll: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
        self.frames: list[bytes] = []
        self.speech_run = 0
        self.speech_frames = 0
        self.silence_run = 0

    def feed(self, audio: bytes) -> list[bytes]:
        self.pending += audio
        finished = []
        while len(self.pending) >= FRAME_BYTES:
            frame, self.pending = self.pending[:FRAME_BYTES], self.pending[FRAME_BYTES:]
            utterance = self._frame(frame, self.vad.is_speech(frame, WHISPER_RATE))
            if utterance:
                finished.append(utterance)
        return finished

    def _frame(self, frame: bytes, is_speech: bool) -> bytes | None:
        if not self.frames:
            return self._waiting(frame, is_speech)
        self.frames.append(frame)
        self.speech_frames += is_speech
        self.silence_run = 0 if is_speech else self.silence_run + 1
        if self.silence_run < END_SILENCE_FRAMES and len(self.frames) < MAX_FRAMES:
            return None
        utterance = b"".join(self.frames) if self.speech_frames >= MIN_SPEECH_FRAMES else None
        self.frames, self.speech_frames, self.silence_run, self.speech_run = [], 0, 0, 0
        self.pre_roll.clear()
        return utterance

    def _waiting(self, frame: bytes, is_speech: bool) -> None:
        self.pre_roll.append(frame)
        self.speech_run = self.speech_run + 1 if is_speech else 0
        if self.speech_run >= START_FRAMES:
            self.frames = list(self.pre_roll)
            self.speech_frames = self.speech_run
        return None


def to_float(audio: bytes) -> numpy.ndarray:
    return numpy.frombuffer(audio, dtype=numpy.int16).astype(numpy.float32) / 32768.0
