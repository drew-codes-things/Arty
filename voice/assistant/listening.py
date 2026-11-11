from collections import deque

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
        self.skipping_rest = False

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
        if self.skipping_rest:
            return self._skip_until_quiet(is_speech)
        if not self.frames:
            return self._waiting(frame, is_speech)
        self.frames.append(frame)
        self.speech_frames += is_speech
        self.silence_run = 0 if is_speech else self.silence_run + 1
        if self.silence_run < END_SILENCE_FRAMES and len(self.frames) < MAX_FRAMES:
            return None
        utterance = b"".join(self.frames) if self.speech_frames >= MIN_SPEECH_FRAMES else None
        self.skipping_rest = self.silence_run < END_SILENCE_FRAMES
        self.frames, self.speech_frames, self.silence_run, self.speech_run = [], 0, 0, 0
        self.pre_roll.clear()
        return utterance

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
        return None


def to_float(audio: bytes) -> numpy.ndarray:
    return numpy.frombuffer(audio, dtype=numpy.int16).astype(numpy.float32) / 32768.0
