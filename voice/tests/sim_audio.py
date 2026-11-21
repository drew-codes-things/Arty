import wave
from pathlib import Path

import numpy

RATE = 16000
FRAME = 480


def resample(samples: numpy.ndarray, from_rate: int, to_rate: int = RATE) -> numpy.ndarray:
    if from_rate == to_rate:
        return samples.astype(numpy.float64)
    spectrum = numpy.fft.rfft(samples)
    wanted = int(round(len(samples) * to_rate / from_rate))
    resized = numpy.zeros(wanted // 2 + 1, dtype=numpy.complex128)
    shared = min(len(resized), len(spectrum))
    resized[:shared] = spectrum[:shared]
    return numpy.fft.irfft(resized, n=wanted) * (wanted / len(samples))


def fft_convolve(signal: numpy.ndarray, response: numpy.ndarray) -> numpy.ndarray:
    size = 1 << int(numpy.ceil(numpy.log2(len(signal) + len(response))))
    return numpy.fft.irfft(numpy.fft.rfft(signal, size) * numpy.fft.rfft(response, size), size)[:len(signal) + len(response) - 1]


_PHRASES: dict[Path, numpy.ndarray] = {}


def load_phrase(path: Path) -> numpy.ndarray:
    if path not in _PHRASES:
        _PHRASES[path] = read_phrase(path)
    return _PHRASES[path].copy()


def read_phrase(path: Path) -> numpy.ndarray:
    with wave.open(str(path)) as reader:
        rate, channels = reader.getframerate(), reader.getnchannels()
        raw = numpy.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2").astype(numpy.float64) / 32768.0
    if channels > 1:
        raw = raw.reshape(-1, channels).mean(axis=1)
    return resample(raw, rate)


def synthetic_speech(seconds: float, seed: int) -> numpy.ndarray:
    rng = numpy.random.default_rng(seed)
    count = int(seconds * RATE)
    time = numpy.arange(count) / RATE
    pitch = 110 + 40 * numpy.sin(2 * numpy.pi * 0.7 * time + rng.uniform(0, 6)) + 15 * numpy.sin(2 * numpy.pi * 2.1 * time)
    phase = 2 * numpy.pi * numpy.cumsum(pitch) / RATE
    voiced = sum(numpy.sin(phase * harmonic) / harmonic ** 0.8 for harmonic in range(1, 30))
    formants = (300 + 250 * numpy.sin(2 * numpy.pi * 1.3 * time), 1500 + 600 * numpy.sin(2 * numpy.pi * 0.9 * time))
    spectrum = numpy.fft.rfft(voiced)
    freqs = numpy.fft.rfftfreq(count, 1 / RATE)
    shaped = numpy.zeros_like(spectrum)
    for centre in (formants[0].mean(), formants[1].mean()):
        shaped += spectrum * numpy.exp(-0.5 * ((freqs - centre) / 220.0) ** 2)
    voiced = numpy.fft.irfft(shaped, n=count)
    syllables = 0.2 + 0.8 * numpy.clip(numpy.sin(2 * numpy.pi * 3.2 * time + rng.uniform(0, 6)) + 0.35, 0, 1)
    words = 0.15 + 0.85 * (numpy.sin(2 * numpy.pi * 0.8 * time + rng.uniform(0, 6)) > -0.8)
    envelope = numpy.convolve(syllables * words, numpy.ones(240) / 240, mode="same")
    hiss = rng.standard_normal(count) * 0.05 * envelope
    signal = voiced * envelope + hiss
    return signal / (numpy.abs(signal).max() + 1e-9)


def level_db(samples: numpy.ndarray) -> float:
    return 20 * numpy.log10(numpy.sqrt(numpy.mean(numpy.square(samples))) + 1e-9)


def scale_to_db(samples: numpy.ndarray, target_db: float) -> numpy.ndarray:
    active = samples[numpy.abs(samples) > 0.02 * numpy.abs(samples).max()]
    return samples * 10 ** ((target_db - level_db(active)) / 20)


def room_response(rng: numpy.random.Generator, delay_seconds: float, reverb_seconds: float) -> numpy.ndarray:
    delay = int(delay_seconds * RATE)
    tail = int(reverb_seconds * RATE)
    decay = numpy.exp(-6.9 * numpy.arange(tail) / max(1, tail))
    response = numpy.zeros(delay + tail + 1)
    response[delay] = 1.0
    response[delay + 1:] = rng.standard_normal(tail) * decay * 0.25
    early = rng.integers(delay + 20, delay + max(40, tail // 3), size=4)
    response[early] += rng.uniform(-0.4, 0.4, size=4)
    return response


def speaker_to_mic(reference: numpy.ndarray, rng: numpy.random.Generator, delay_seconds: float,
                   reverb_seconds: float, level: float, distortion: float = 0.0) -> numpy.ndarray:
    played = reference
    if distortion > 0:
        played = numpy.tanh(reference * (1 + distortion * 4)) / (1 + distortion * 1.5)
    echoed = fft_convolve(played, room_response(rng, delay_seconds, reverb_seconds))[:len(reference)]
    return echoed * level


def ambient(rng: numpy.random.Generator, samples: int, level_db_value: float = -58.0) -> numpy.ndarray:
    white = rng.standard_normal(samples)
    smooth = numpy.convolve(white, numpy.ones(8) / 8, mode="same")
    return (white * 0.5 + smooth * 0.5) * 10 ** (level_db_value / 20)


def frames_of(signal: numpy.ndarray):
    for start in range(0, len(signal) - FRAME + 1, FRAME):
        yield signal[start:start + FRAME]


def to_pcm(frame: numpy.ndarray) -> bytes:
    return (numpy.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
