import json
import subprocess
import sys

import numpy as np

SAMPLE_RATE = 16000
MAX_SECONDS = 90
MIN_SECONDS = 0.5
FLOOR_DB = -120.0
SILENT_DB = -60.0
CLIPPED_LEVEL = 0.99
LOUDNESS_WINDOW_SECONDS = 0.5
LOUDNESS_POINTS = 16
STEADY_RANGE_DB = 3.0
TREND_DB_PER_SECOND = 0.5
SPECTRUM_FRAME = 4096
SPECTRUM_PEAKS = 5
PEAK_SEPARATION_BINS = 4
WEAKEST_PEAK_DB = -30.0
PROMINENT_PEAK_DB = 12.0
LOW_HZ = 20
HIGH_HZ = 8000
BANDS = ((20, 200), (200, 1000), (1000, 4000), (4000, 8000))
HARMONIC_TOLERANCE = 0.03
HARMONIC_MIN_MATCHES = 3
FUNDAMENTAL_RANGE_HZ = (15, 400)
ENVELOPE_HOP_SECONDS = 0.005
RHYTHM_MIN_PERIOD = 0.04
RHYTHM_MAX_PERIOD = 2.0
RHYTHM_MIN_STRENGTH = 0.3
RHYTHM_MIN_SECONDS = 2.0
RHYTHM_CANDIDATES = 3
SUBMULTIPLE_RATIO = 0.8
IMPULSE_HOP_SECONDS = 0.002
IMPULSE_BASELINE_SECONDS = 1.0
IMPULSE_RATIO = 4.0
IMPULSE_FLOOR_DB = -45.0
IMPULSE_REFRACTORY_SECONDS = 0.04
IMPULSE_WINDOW_SECONDS = 0.025
IMPULSE_LISTED = 8
LOW_CENTROID_HZ = 700
HIGH_CENTROID_HZ = 2500
TONE_FRAME = 2048
TONE_HOP = 1024
TONE_LOW_HZ = 300
TONE_HIGH_HZ = 7000
TONE_PROMINENCE_DB = 15.0
TONE_NEIGHBOURHOOD_BINS = 40
TONE_EXCLUDED_BINS = 3
TONE_STEP_RATIO = 0.06
TONE_MIN_SECONDS = 0.4
TONES_LISTED = 5
GLIDE_RATIO = 0.05


def decode(path: str, start: float) -> np.ndarray:
    command = ["ffmpeg", "-protocol_whitelist", "file", "-v", "error", "-ss", f"{start:.2f}", "-t", str(MAX_SECONDS), "-i", path,
               "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    result = subprocess.run(command, capture_output=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace")[-300:] or "ffmpeg failed")
    return np.frombuffer(result.stdout, dtype="<i2").astype(np.float64) / 32768.0


def db(value) -> np.ndarray:
    return 20 * np.log10(np.maximum(value, 10 ** (FLOOR_DB / 20)))


def block_rms(samples: np.ndarray, size: int) -> np.ndarray:
    blocks = len(samples) // size
    if blocks == 0:
        return np.zeros(0)
    return np.sqrt(np.mean(samples[:blocks * size].reshape(blocks, size) ** 2, axis=1))


def level_facts(samples: np.ndarray) -> dict:
    rms = float(db(np.sqrt(np.mean(samples ** 2))))
    window = block_rms(samples, int(SAMPLE_RATE * LOUDNESS_WINDOW_SECONDS))
    quiet = float(np.mean(db(window) < SILENT_DB)) if len(window) else 1.0
    return {"rms_db": round(rms, 1), "peak_db": round(float(db(np.max(np.abs(samples)))), 1),
            "clipped_percent": round(100 * float(np.mean(np.abs(samples) >= CLIPPED_LEVEL)), 2), "silent_percent": round(100 * quiet)}


def loudness_facts(samples: np.ndarray) -> dict:
    levels = db(block_rms(samples, int(SAMPLE_RATE * LOUDNESS_WINDOW_SECONDS)))
    if len(levels) == 0:
        return {"points": [], "range_db": 0.0, "trend": "steady"}
    times = np.arange(len(levels)) * LOUDNESS_WINDOW_SECONDS
    slope = float(np.polyfit(times, levels, 1)[0]) if len(levels) > 2 else 0.0
    spread = float(np.max(levels) - np.min(levels))
    if spread < STEADY_RANGE_DB:
        trend = "steady"
    elif slope >= TREND_DB_PER_SECOND:
        trend = "rising"
    elif slope <= -TREND_DB_PER_SECOND:
        trend = "falling"
    else:
        trend = "varying"
    chosen = np.unique(np.linspace(0, len(levels) - 1, min(LOUDNESS_POINTS, len(levels))).round().astype(int))
    return {"points": [[round(float(times[index]), 1), round(float(levels[index]), 1)] for index in chosen],
            "range_db": round(spread, 1), "trend": trend}


def average_power_spectrum(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    window = np.hanning(SPECTRUM_FRAME)
    count = (len(samples) - SPECTRUM_FRAME) // (SPECTRUM_FRAME // 2) + 1
    frames = [samples[index * SPECTRUM_FRAME // 2: index * SPECTRUM_FRAME // 2 + SPECTRUM_FRAME] * window for index in range(max(count, 0))]
    frames = [frame for frame in frames if np.sqrt(np.mean(frame ** 2)) > 10 ** (SILENT_DB / 20)]
    frequencies = np.fft.rfftfreq(SPECTRUM_FRAME, 1 / SAMPLE_RATE)
    if not frames:
        return frequencies, np.zeros(len(frequencies))
    return frequencies, np.mean([np.abs(np.fft.rfft(frame)) ** 2 for frame in frames], axis=0)


def spectral_peaks(frequencies: np.ndarray, power: np.ndarray) -> list[tuple[float, float]]:
    inside = (frequencies >= LOW_HZ) & (frequencies <= HIGH_HZ)
    candidates = np.where(inside & (power > 0))[0]
    if len(candidates) == 0:
        return []
    strongest = float(np.max(power[candidates]))
    typical = float(np.median(power[candidates]))
    chosen: list[int] = []
    for index in candidates[np.argsort(-power[candidates])]:
        if index == 0 or index == len(power) - 1 or power[index] < power[index - 1] or power[index] < power[index + 1]:
            continue
        if power[index] < strongest * 10 ** (WEAKEST_PEAK_DB / 10) or power[index] < typical * 10 ** (PROMINENT_PEAK_DB / 10):
            break
        if all(abs(index - other) > PEAK_SEPARATION_BINS for other in chosen):
            chosen.append(int(index))
        if len(chosen) == SPECTRUM_PEAKS:
            break
    return [(float(frequencies[index]), float(10 * np.log10(power[index] / strongest))) for index in chosen]


def harmonic_fundamental(peaks: list[tuple[float, float]]) -> float | None:
    low, high = FUNDAMENTAL_RANGE_HZ
    for base, _ in sorted(peaks):
        if not low <= base <= high:
            continue
        matches = sum(1 for other, _ in peaks if other > base * 1.5 and abs(other / base - round(other / base)) <= HARMONIC_TOLERANCE * round(other / base))
        if matches >= HARMONIC_MIN_MATCHES - 1:
            return base
    return None


def spectrum_facts(samples: np.ndarray) -> dict:
    frequencies, power = average_power_spectrum(samples)
    total = float(np.sum(power[(frequencies >= LOW_HZ) & (frequencies <= HIGH_HZ)]))
    if total <= 0:
        return {"peaks": [], "bands": {}, "centroid_hz": 0, "fundamental_hz": None}
    bands = {f"{low}-{high}": round(float(np.sum(power[(frequencies >= low) & (frequencies < high)])) / total, 3) for low, high in BANDS}
    inside = (frequencies >= LOW_HZ) & (frequencies <= HIGH_HZ)
    peaks = spectral_peaks(frequencies, power)
    centroid = float(np.sum(frequencies[inside] * power[inside]) / total)
    fundamental = harmonic_fundamental(peaks)
    return {"peaks": [{"hz": round(hz, 1), "relative_db": round(level, 1)} for hz, level in peaks], "bands": bands,
            "centroid_hz": round(centroid), "fundamental_hz": round(fundamental, 1) if fundamental else None}


def autocorrelation(values: np.ndarray) -> np.ndarray:
    centred = values - np.mean(values)
    size = 1 << (2 * len(centred) - 1).bit_length()
    spectrum = np.fft.rfft(centred, size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum))[:len(centred)]
    return correlation / correlation[0] if correlation[0] > 0 else correlation


def refined_lag(correlation: np.ndarray, lag: int) -> float:
    if lag <= 0 or lag >= len(correlation) - 1:
        return float(lag)
    before, here, after = correlation[lag - 1], correlation[lag], correlation[lag + 1]
    bend = before - 2 * here + after
    return float(lag + 0.5 * (before - after) / bend) if bend else float(lag)


def rhythm_facts(samples: np.ndarray) -> dict | None:
    hop = int(SAMPLE_RATE * ENVELOPE_HOP_SECONDS)
    envelope = block_rms(samples, hop)
    if len(envelope) * ENVELOPE_HOP_SECONDS < RHYTHM_MIN_SECONDS or np.mean(envelope) <= 0:
        return None
    if np.std(envelope) / np.mean(envelope) < 0.05:
        return None
    correlation = autocorrelation(envelope)
    first = int(RHYTHM_MIN_PERIOD / ENVELOPE_HOP_SECONDS)
    last = min(int(RHYTHM_MAX_PERIOD / ENVELOPE_HOP_SECONDS), len(correlation) - 2)
    lags = [lag for lag in range(max(first, 1), last) if correlation[lag] > correlation[lag - 1] and correlation[lag] >= correlation[lag + 1]]
    if not lags:
        return None
    ranked = sorted(lags, key=lambda lag: -correlation[lag])
    best = ranked[0]
    for shorter in sorted(lags):
        if shorter < best and abs(best / shorter - round(best / shorter)) < 0.06 and correlation[shorter] >= SUBMULTIPLE_RATIO * correlation[best]:
            best = shorter
            break
    strength = float(correlation[best])
    if strength < RHYTHM_MIN_STRENGTH:
        return None
    period = refined_lag(correlation, best) * ENVELOPE_HOP_SECONDS
    others = [round(refined_lag(correlation, lag) * ENVELOPE_HOP_SECONDS, 3) for lag in ranked[:RHYTHM_CANDIDATES] if lag != best]
    return {"period_s": round(period, 4), "rate_hz": round(1 / period, 2), "strength": round(strength, 2), "other_periods_s": others,
            "repeats": int(len(samples) / SAMPLE_RATE / period)}


def sharpened(samples: np.ndarray) -> np.ndarray:
    return np.diff(samples, prepend=samples[:1])


def impulse_kind(window: np.ndarray) -> str:
    if len(window) < 32:
        return "tick"
    power = np.abs(np.fft.rfft(window * np.hanning(len(window)))) ** 2
    frequencies = np.fft.rfftfreq(len(window), 1 / SAMPLE_RATE)
    centroid = float(np.sum(frequencies * power) / np.sum(power)) if np.sum(power) > 0 else 0.0
    if centroid < LOW_CENTROID_HZ:
        return "thud"
    return "tick" if centroid < HIGH_CENTROID_HZ else "sharp click"


def impulse_onsets(samples: np.ndarray) -> list[tuple[float, float, int]]:
    hop = int(SAMPLE_RATE * IMPULSE_HOP_SECONDS)
    envelope = block_rms(sharpened(samples), hop)
    per_second = int(IMPULSE_BASELINE_SECONDS / IMPULSE_HOP_SECONDS)
    refractory = int(IMPULSE_REFRACTORY_SECONDS / IMPULSE_HOP_SECONDS)
    found: list[tuple[float, float, int]] = []
    last = -refractory
    for start in range(0, len(envelope), per_second):
        segment = envelope[start:start + per_second]
        baseline = max(float(np.median(segment)), 10 ** (SILENT_DB / 20))
        for offset in np.where((segment > baseline * IMPULSE_RATIO) & (db(segment) > IMPULSE_FLOOR_DB))[0]:
            index = start + int(offset)
            if index - last >= refractory:
                found.append((index * IMPULSE_HOP_SECONDS, float(db(envelope[index])), index * hop))
                last = index
    return found


def interval_spread(intervals: np.ndarray) -> str:
    spread = float(np.std(intervals) / np.mean(intervals))
    return "very regular" if spread < 0.15 else "fairly regular" if spread < 0.4 else "irregular"


def impulse_facts(samples: np.ndarray) -> dict:
    onsets = impulse_onsets(samples)
    seconds = len(samples) / SAMPLE_RATE
    window = int(SAMPLE_RATE * IMPULSE_WINDOW_SECONDS)
    events = [{"t": round(moment, 2), "db": round(level, 1), "kind": impulse_kind(samples[start:start + window])} for moment, level, start in onsets]
    kinds: dict[str, int] = {}
    for event in events:
        kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1
    facts = {"count": len(events), "per_second": round(len(events) / seconds, 2) if seconds else 0.0, "kinds": kinds,
             "listed": sorted(events, key=lambda event: -event["db"])[:IMPULSE_LISTED]}
    if len(events) >= 4:
        intervals = np.diff([event["t"] for event in events])
        if np.mean(intervals) > 0:
            facts["median_interval_s"] = round(float(np.median(intervals)), 3)
            facts["spread"] = interval_spread(intervals)
    facts["listed"] = sorted(facts["listed"], key=lambda event: event["t"])
    return facts


def local_mean(power: np.ndarray, centre: int) -> float:
    low, high = max(centre - TONE_NEIGHBOURHOOD_BINS, 0), min(centre + TONE_NEIGHBOURHOOD_BINS + 1, len(power))
    keep = [index for index in range(low, high) if abs(index - centre) > TONE_EXCLUDED_BINS]
    return float(np.mean(power[keep])) if keep else 0.0


def tone_in_frame(frame: np.ndarray, frequencies: np.ndarray, band: np.ndarray) -> float | None:
    power = np.abs(np.fft.rfft(frame * np.hanning(len(frame)))) ** 2
    if np.sqrt(np.mean(frame ** 2)) < 10 ** (SILENT_DB / 20):
        return None
    inside = np.where(band)[0]
    peak = int(inside[np.argmax(power[inside])])
    around = local_mean(power, peak)
    if around <= 0 or 10 * np.log10(power[peak] / around) < TONE_PROMINENCE_DB:
        return None
    return float(frequencies[peak])


def tone_runs(samples: np.ndarray) -> list[dict]:
    frequencies = np.fft.rfftfreq(TONE_FRAME, 1 / SAMPLE_RATE)
    band = (frequencies >= TONE_LOW_HZ) & (frequencies <= TONE_HIGH_HZ)
    step_seconds = TONE_HOP / SAMPLE_RATE
    runs: list[list[tuple[int, float]]] = []
    for index in range((len(samples) - TONE_FRAME) // TONE_HOP + 1):
        tone = tone_in_frame(samples[index * TONE_HOP: index * TONE_HOP + TONE_FRAME], frequencies, band)
        if tone is None:
            continue
        if runs and runs[-1][-1][0] == index - 1 and abs(tone - runs[-1][-1][1]) / runs[-1][-1][1] <= TONE_STEP_RATIO:
            runs[-1].append((index, tone))
        else:
            runs.append([(index, tone)])
    found = []
    for run in runs:
        seconds = (len(run) - 1) * step_seconds + TONE_FRAME / SAMPLE_RATE
        if seconds >= TONE_MIN_SECONDS:
            first, last = run[0][1], run[-1][1]
            found.append({"start_s": round(run[0][0] * step_seconds, 1), "seconds": round(seconds, 1),
                          "hz": round(float(np.mean([tone for _, tone in run]))), "glide_hz": round(last - first) if abs(last - first) / first > GLIDE_RATIO else 0})
    return sorted(found, key=lambda tone: -tone["seconds"])[:TONES_LISTED]


def analyse(samples: np.ndarray) -> dict:
    return {"seconds": round(len(samples) / SAMPLE_RATE, 1), "level": level_facts(samples), "loudness": loudness_facts(samples),
            "spectrum": spectrum_facts(samples), "rhythm": rhythm_facts(samples), "impulses": impulse_facts(samples),
            "tones": tone_runs(samples)}


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print("usage: sound_features.py <audio-or-video-file> [start-seconds]", file=sys.stderr)
        return 2
    start = float(sys.argv[2]) if len(sys.argv) == 3 else 0.0
    samples = decode(sys.argv[1], start)
    if len(samples) < SAMPLE_RATE * MIN_SECONDS:
        print(json.dumps({"error": "too short to analyse"}))
        return 0
    print(json.dumps(analyse(samples)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
