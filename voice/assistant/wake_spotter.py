import logging
from pathlib import Path
from typing import Callable, Mapping, Protocol

import numpy

from listening import to_float

EMBEDDING_SIZE = 96
FIRST_EMBEDDING_SAMPLES = 12400
EMBEDDING_HOP_SAMPLES = 1280
WINDOW_FRAMES = 16
WINDOW_SAMPLES = FIRST_EMBEDDING_SAMPLES + (WINDOW_FRAMES - 1) * EMBEDDING_HOP_SAMPLES
LEAD_SAMPLES = WINDOW_SAMPLES - FIRST_EMBEDDING_SAMPLES
WAKE_THRESHOLD = 0.5
EMBEDDING_FILE = "speech_embedding.onnx"
CLASSIFIER_FILE = "hey_arty.npz"
DEFAULT_SPOTTER_MODEL = "/opt/ai-arty/models/hey-arty"
WAKE_ENGINE_CHOICES = ("spotter", "whisper")

log = logging.getLogger("arty-voice")


class Classifier(Protocol):

    def probabilities(self, windows: numpy.ndarray) -> numpy.ndarray: ...


class WakeClassifier:

    def __init__(self, params: Mapping[str, numpy.ndarray]):
        self.mean = params["mean"]
        self.scale = params["scale"]
        self.hidden_weights = params["hidden_weights"]
        self.hidden_bias = params["hidden_bias"]
        self.output_weights = params["output_weights"]
        self.output_bias = params["output_bias"]

    def probabilities(self, windows: numpy.ndarray) -> numpy.ndarray:
        hidden = numpy.maximum(0, ((windows - self.mean) / self.scale) @ self.hidden_weights + self.hidden_bias)
        logits = (hidden @ self.output_weights + self.output_bias)[:, 0]
        return 1 / (1 + numpy.exp(-logits))


class WakeSpotter:

    def __init__(self, embed: Callable[[numpy.ndarray], numpy.ndarray], classifier: Classifier,
                 threshold: float = WAKE_THRESHOLD):
        self.embed = embed
        self.classifier = classifier
        self.threshold = threshold

    def heard(self, audio: bytes) -> bool:
        return self.score(audio) >= self.threshold

    def score(self, audio: bytes) -> float:
        samples = to_float(audio)
        lead = numpy.zeros(LEAD_SAMPLES + max(0, FIRST_EMBEDDING_SAMPLES - len(samples)), dtype=numpy.float32)
        frames = self.embed(numpy.concatenate([lead, samples]))
        windows = numpy.stack([frames[start:start + WINDOW_FRAMES].reshape(-1)
                               for start in range(len(frames) - WINDOW_FRAMES + 1)])
        return float(self.classifier.probabilities(windows).max())


def load_wake_spotter(folder: Path) -> WakeSpotter:
    import onnxruntime
    options = onnxruntime.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    session = onnxruntime.InferenceSession(str(folder / EMBEDDING_FILE), sess_options=options,
                                           providers=["CPUExecutionProvider"])
    input_name = session.get_inputs()[0].name

    def embed(samples: numpy.ndarray) -> numpy.ndarray:
        return session.run(None, {input_name: samples[numpy.newaxis]})[0][0, :, 0, :]

    with numpy.load(folder / CLASSIFIER_FILE) as params:
        classifier = WakeClassifier({name: params[name] for name in params.files})
    return WakeSpotter(embed, classifier)


def wake_spotter_from_settings(settings: Mapping[str, str]) -> WakeSpotter | None:
    engine = settings.get("WAKE_ENGINE", WAKE_ENGINE_CHOICES[0]).strip().lower()
    if engine not in WAKE_ENGINE_CHOICES:
        raise ValueError(f"WAKE_ENGINE must be one of {', '.join(WAKE_ENGINE_CHOICES)}, not {engine!r}")
    if engine == "whisper":
        return None
    folder = Path(settings.get("WAKE_SPOTTER_MODEL", DEFAULT_SPOTTER_MODEL))
    if not (folder / EMBEDDING_FILE).is_file() or not (folder / CLASSIFIER_FILE).is_file():
        log.warning("no Hey Arty spotter model in %s, checking the wake word with whisper only", folder)
        return None
    return load_wake_spotter(folder)
