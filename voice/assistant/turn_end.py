import logging
from pathlib import Path
from typing import Callable, Mapping

import numpy

from listening import (FRAME_MS, PAUSE_FRAMES, TURN_WAIT_FRAMES, WHISPER_RATE, LiveEar, SpeechDetector, TurnJudge,
                       UtteranceCollector, WebrtcSpeech, to_float)

SMART_TURN_SECONDS = 8
SMART_TURN_SAMPLES = SMART_TURN_SECONDS * WHISPER_RATE
SMART_TURN_MEL_BINS = 80
SMART_TURN_THRESHOLD = 0.5
NORMALISE_EPSILON = 1e-7
SILERO_CHUNK = 512
SILERO_CONTEXT = 64
SILERO_STATE_SHAPE = (2, 1, 128)
SILERO_THRESHOLD = 0.5
SILERO_RATE = numpy.array(WHISPER_RATE, dtype=numpy.int64)
DEFAULT_SMART_TURN_MODEL = "/opt/ai-arty/models/smart-turn-v3.2-cpu.onnx"
DEFAULT_SILERO_MODEL = "/opt/ai-arty/models/silero_vad.onnx"
DEFAULT_MOONSHINE_MODEL = "/opt/ai-arty/models/moonshine-tiny-streaming-en"
TURN_END_CHOICES = ("smart", "silence")
VAD_CHOICES = ("silero", "webrtc")
COMMAND_ENGINE_CHOICES = ("moonshine", "whisper")

log = logging.getLogger("arty-voice")


class SileroSpeech:

    def __init__(self, session, threshold: float = SILERO_THRESHOLD):
        self.session = session
        self.threshold = threshold
        self.state = numpy.zeros(SILERO_STATE_SHAPE, dtype=numpy.float32)
        self.context = numpy.zeros(SILERO_CONTEXT, dtype=numpy.float32)
        self.waiting = numpy.zeros(0, dtype=numpy.float32)
        self.probability = 0.0

    def is_speech(self, frame: bytes) -> bool:
        self.waiting = numpy.concatenate([self.waiting, to_float(frame)])
        while len(self.waiting) >= SILERO_CHUNK:
            chunk, self.waiting = self.waiting[:SILERO_CHUNK], self.waiting[SILERO_CHUNK:]
            self.probability = self._score(chunk)
        return self.probability >= self.threshold

    def _score(self, chunk: numpy.ndarray) -> float:
        window = numpy.concatenate([self.context, chunk])[numpy.newaxis, :]
        output, self.state = self.session.run(None, {"input": window, "state": self.state, "sr": SILERO_RATE})
        self.context = window[0, -SILERO_CONTEXT:]
        return float(numpy.asarray(output).reshape(-1)[0])


class SmartTurn:

    def __init__(self, session, features: Callable[[numpy.ndarray], numpy.ndarray],
                 threshold: float = SMART_TURN_THRESHOLD):
        self.session = session
        self.features = features
        self.threshold = threshold

    def finished(self, audio: bytes) -> bool:
        return self.probability(audio) > self.threshold

    def probability(self, audio: bytes) -> float:
        window = fit_to_window(to_float(audio))
        normalised = (window - window.mean()) / numpy.sqrt(window.var() + NORMALISE_EPSILON)
        mel = self.features(normalised.astype(numpy.float32))
        [scores] = self.session.run(None, {"input_features": mel[numpy.newaxis].astype(numpy.float32)})
        return float(numpy.asarray(scores).reshape(-1)[0])


def fit_to_window(samples: numpy.ndarray) -> numpy.ndarray:
    recent = samples[-SMART_TURN_SAMPLES:]
    padding = numpy.zeros(SMART_TURN_SAMPLES - len(recent), dtype=numpy.float32)
    return numpy.concatenate([padding, recent])


class Listening:

    def __init__(self, make_speech: Callable[[], SpeechDetector], turn: TurnJudge | None,
                 turn_wait_frames: int = TURN_WAIT_FRAMES, live: LiveEar | None = None):
        self.make_speech = make_speech
        self.turn = turn
        self.turn_wait_frames = turn_wait_frames
        self.live = live

    def new_collector(self) -> UtteranceCollector:
        return UtteranceCollector(speech=self.make_speech(), turn=self.turn, turn_wait_frames=self.turn_wait_frames,
                                  live=self.live)


def load_listening(settings: Mapping[str, str]) -> Listening:
    turn_end = choice(settings, "VOICE_TURN_END", TURN_END_CHOICES)
    vad = choice(settings, "VOICE_VAD", VAD_CHOICES)
    wait_frames = turn_wait_frames(settings)
    command_engine = choice(settings, "COMMAND_ENGINE", COMMAND_ENGINE_CHOICES)
    make_speech = WebrtcSpeech
    if vad == "silero":
        make_speech = silero_or_none(Path(settings.get("SILERO_VAD_MODEL", DEFAULT_SILERO_MODEL))) or WebrtcSpeech
    turn = None
    if turn_end == "smart":
        turn = smart_turn_or_none(Path(settings.get("SMART_TURN_MODEL", DEFAULT_SMART_TURN_MODEL)))
    live = None
    if command_engine == "moonshine":
        live = moonshine_or_none(Path(settings.get("MOONSHINE_MODEL", DEFAULT_MOONSHINE_MODEL)))
    log.info("listening with %s speech detection, %s end-of-turn and %s commands",
             "silero" if make_speech is not WebrtcSpeech else "webrtc", "smart" if turn else "silence",
             "moonshine" if live else "whisper")
    return Listening(make_speech, turn, wait_frames, live)


def choice(settings: Mapping[str, str], name: str, allowed: tuple[str, ...]) -> str:
    value = settings.get(name, allowed[0]).strip().lower()
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}, not {value!r}")
    return value


def turn_wait_frames(settings: Mapping[str, str]) -> int:
    raw = settings.get("VOICE_TURN_WAIT_MS", str(TURN_WAIT_FRAMES * FRAME_MS))
    try:
        frames = int(raw) // FRAME_MS
    except ValueError:
        raise ValueError(f"VOICE_TURN_WAIT_MS must be a whole number of milliseconds, not {raw!r}") from None
    if frames <= PAUSE_FRAMES:
        raise ValueError(f"VOICE_TURN_WAIT_MS must be more than {PAUSE_FRAMES * FRAME_MS}, not {raw!r}")
    return frames


def silero_or_none(path: Path) -> Callable[[], SileroSpeech] | None:
    if not path.is_file():
        log.warning("no Silero VAD model at %s, using webrtcvad", path)
        return None
    session = onnx_session(path)
    return lambda: SileroSpeech(session)


def smart_turn_or_none(path: Path) -> SmartTurn | None:
    if not path.is_file():
        log.warning("no Smart Turn model at %s, ending turns on silence", path)
        return None
    from faster_whisper.feature_extractor import FeatureExtractor
    extractor = FeatureExtractor(feature_size=SMART_TURN_MEL_BINS, chunk_length=SMART_TURN_SECONDS)
    return SmartTurn(onnx_session(path), lambda audio: extractor(audio, padding=0))


def moonshine_or_none(folder: Path) -> LiveEar | None:
    if not folder.is_dir():
        log.warning("no Moonshine model at %s, transcribing commands with whisper", folder)
        return None
    from live_text import MoonshineEar
    return MoonshineEar.load(folder)


def onnx_session(path: Path):
    import onnxruntime
    options = onnxruntime.SessionOptions()
    options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return onnxruntime.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
