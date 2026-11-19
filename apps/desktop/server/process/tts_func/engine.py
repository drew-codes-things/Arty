"""Text-to-speech. `tts.provider` picks kokoro (local) or sovits (HTTP)."""
import contextlib
import ctypes
import gc
import io
import sys
import threading
import time

from process.config import load_config, resolve
from process.tts_func import server_voice

_config = load_config()
_tts = _config.get("tts") or {}

PROVIDER = (_tts.get("provider") or "kokoro").strip().lower()


class TTSError(RuntimeError):
    pass


IDLE_UNLOAD_SECONDS = 10 * 60
LIGHT_MODEL_NAME = "kokoro-v1.0.int8.onnx"
DEFAULT_MODEL_PATH = "models/kokoro/kokoro-v1.0.onnx"
DEFAULT_VOICES_PATH = "models/kokoro/voices-v1.0.bin"
MIN_RECHECK_SECONDS = 5.0

_kokoro = None
_kokoro_lock = threading.Lock()
_in_use = 0
_last_used = 0.0
_unload_timer = None
_clock = time.monotonic


def _kokoro_cfg():
    return _tts.get("kokoro") or {}


def model_file(cfg):
    configured = resolve(cfg.get("model_path", DEFAULT_MODEL_PATH))
    light = configured.with_name(LIGHT_MODEL_NAME)
    return light if light.exists() else configured


def _idle_unload_seconds():
    return float(_kokoro_cfg().get("idle_unload_seconds", IDLE_UNLOAD_SECONDS))


def _new_kokoro(model, voices):
    try:
        from kokoro_onnx import Kokoro
    except ImportError as e:
        raise TTSError(
            "kokoro-onnx isn't installed. Run the setup script (setup-linux.sh or setup-windows.ps1), or switch "
            "tts.provider to 'sovits'."
        ) from e
    return Kokoro(model, voices)


def _open_kokoro():
    cfg = _kokoro_cfg()
    model = model_file(cfg)
    voices = resolve(cfg.get("voices_path", DEFAULT_VOICES_PATH))

    for path, what in ((model, "model"), (voices, "voices")):
        if not path.exists():
            raise TTSError(
                f"Kokoro {what} file missing at {path}. "
                "Run ./download-kokoro.sh to fetch it."
            )

    print("Loading Kokoro...", flush=True)
    opened = _new_kokoro(str(model), str(voices))
    print("Kokoro ready.", flush=True)
    return opened


def _schedule_unload_locked(delay):
    global _unload_timer
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(delay, unload_if_idle)
    _unload_timer.daemon = True
    _unload_timer.start()


@contextlib.contextmanager
def local_voice_in_use():
    global _kokoro, _in_use, _last_used
    with _kokoro_lock:
        if _kokoro is None:
            _kokoro = _open_kokoro()
        _in_use += 1
        voice = _kokoro
    try:
        yield voice
    finally:
        with _kokoro_lock:
            _in_use -= 1
            _last_used = _clock()
            _schedule_unload_locked(_idle_unload_seconds())


def local_voice_loaded():
    with _kokoro_lock:
        return _kokoro is not None


def _return_memory_to_the_system():
    gc.collect()
    if not sys.platform.startswith("linux"):
        return
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError) as error:
        print(f"[tts] couldn't hand the freed voice memory back: {error}", flush=True)


def unload_if_idle():
    global _kokoro, _unload_timer
    with _kokoro_lock:
        _unload_timer = None
        if _kokoro is None:
            return False
        waiting = _idle_unload_seconds() - (_clock() - _last_used)
        if _in_use or waiting > 0:
            _schedule_unload_locked(max(waiting, MIN_RECHECK_SECONDS))
            return False
        _kokoro = None
    _return_memory_to_the_system()
    print("Kokoro unloaded after sitting idle.", flush=True)
    return True


def forget_kokoro():
    global _kokoro, _in_use, _unload_timer
    with _kokoro_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()
        _unload_timer = None
        _kokoro = None
        _in_use = 0


def _resolve_voice(kokoro, cfg):
    """A voice name, or a weighted average of several voice style vectors."""
    blend = cfg.get("blend")
    if not blend:
        return cfg.get("voice", "af_heart")

    total = sum(float(w) for w in blend.values())
    if total <= 0:
        raise TTSError("tts.kokoro.blend weights must add up to more than zero.")

    mixed = None
    for name, weight in blend.items():
        part = kokoro.get_voice_style(name) * (float(weight) / total)
        mixed = part if mixed is None else mixed + part
    return mixed


def _pitch_shift(audio, semitones):
    if abs(semitones) < 0.01:
        return audio

    ratio = 2.0 ** (semitones / 12.0)
    try:
        import soxr
        return soxr.resample(audio, ratio, 1.0)
    except ImportError:
        import numpy as np
        n = int(len(audio) / ratio)
        idx = np.linspace(0, len(audio) - 1, n)
        return np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)


def _save_audio(audio, sample_rate):
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _kokoro_gen(text):
    cfg = _kokoro_cfg()
    semitones = float(cfg.get("pitch", 0.0))
    ratio = 2.0 ** (semitones / 12.0)
    speed = float(cfg.get("speed", 1.0)) / ratio

    with local_voice_in_use() as kokoro:
        voice = _resolve_voice(kokoro, cfg)
        try:
            audio, sample_rate, timings = kokoro.create_timed(
                text,
                voice=voice,
                speed=speed,
                lang=cfg.get("lang", "en-us"),
            )
            audio = _pitch_shift(audio, semitones)
        except Exception as e:
            available = ""
            try:
                available = ", ".join(sorted(kokoro.get_voices())[:8]) + ", ..."
            except Exception:
                pass
            label = voice if isinstance(voice, str) else "blend"
            raise TTSError(
                f"Kokoro failed on voice '{label}': {e}"
                + (f" Available voices: {available}" if available else "")
            ) from e

    return _save_audio(audio, sample_rate), visemes_from(timings, 1.0 / ratio)


_VISEME_BY_PHONEME = {
    "ɑ": "aa", "a": "aa", "ʌ": "aa", "æ": "aa", "ɐ": "aa", "ɒ": "aa",
    "i": "ee", "ɪ": "ee", "e": "ee", "ɛ": "ee", "eɪ": "ee", "ᵻ": "ee",
    "ə": "ih", "ɚ": "ih", "ɜ": "ih", "ɝ": "ih", "ɹ": "ih",
    "ɔ": "oh", "o": "oh", "oʊ": "oh", "aʊ": "oh", "ɔɪ": "oh", "aɪ": "oh",
    "u": "ou", "ʊ": "ou", "w": "ou", "uː": "ou",
}

_CLOSED = set("mbp")


def visemes_from(timings, scale=1.0):
    """Phoneme timings to [{t, v, w}]: time, viseme, weight."""
    track = []
    for timing in timings or []:
        raw = (timing.phoneme or "").strip()
        if not raw:
            continue
        base = raw[0]
        if base in _CLOSED:
            track.append({"t": round(timing.start * scale, 4), "v": "aa", "w": 0.0})
            continue
        viseme = _VISEME_BY_PHONEME.get(raw[:2]) or _VISEME_BY_PHONEME.get(base)
        if not viseme:
            continue
        track.append({
            "t": round(timing.start * scale, 4),
            "v": viseme,
            "w": round(min(1.0, max(0.25, (timing.end - timing.start) * 12)), 3),
        })
    return track


def kokoro_voices():
    try:
        with local_voice_in_use() as kokoro:
            return sorted(kokoro.get_voices())
    except TTSError:
        return []


def describe():
    if PROVIDER == "kokoro":
        cfg = _kokoro_cfg()
        if cfg.get("blend"):
            mix = "+".join(f"{n}:{w}" for n, w in cfg["blend"].items())
            voice = f"blend={mix}"
        else:
            voice = f"voice={cfg.get('voice', 'af_heart')}"
        pitch = float(cfg.get("pitch", 0.0))
        suffix = f" · pitch{pitch:+.1f}st" if pitch else ""
        local = f"kokoro (local) · {voice}{suffix}"
        return f"the server's voice, else {local}" if server_voice.configured() else local
    if PROVIDER == "sovits":
        from process.tts_func.sovits_ping import API_URL

        return f"gpt-sovits · {API_URL}"
    return PROVIDER


def synthesize(text):
    """Return (wav_bytes, visemes). visemes is empty for sovits."""
    if not text or not text.strip():
        raise TTSError("Refusing to synthesize empty text.")

    if PROVIDER == "kokoro":
        spoken = server_voice.speak(text)
        return (spoken, []) if spoken is not None else _kokoro_gen(text)

    if PROVIDER == "sovits":
        from process.tts_func.sovits_ping import SovitsError, sovits_gen_bytes

        try:
            return sovits_gen_bytes(text), []
        except SovitsError as e:
            raise TTSError(str(e)) from e

    raise TTSError(f"Unknown tts.provider '{PROVIDER}'. Use 'kokoro' or 'sovits'.")


def warmup():
    if PROVIDER == "kokoro":
        if server_voice.speak("Ready.") is not None:
            return
        with local_voice_in_use():
            pass
        try:
            _kokoro_gen("Ready.")
        except Exception as e:
            print(f"[tts] warmup inference failed: {e}", flush=True)
