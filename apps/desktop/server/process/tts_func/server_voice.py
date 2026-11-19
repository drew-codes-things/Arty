import io
import json
import socket
import time
import wave

from process.config import load_config

WYOMING_VERSION = "1.0.0"
DEFAULT_PORT = 10300
DEFAULT_TIMEOUT_SECONDS = 8.0
RETRY_AFTER_SECONDS = 60.0
MAX_HEADER_BYTES = 64 * 1024
MAX_EVENT_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 64 * 1024 * 1024
SAMPLE_WIDTH_BYTES = 2
MAX_CHANNELS = 2

_clock = time.monotonic
_down_until = 0.0


class ServerVoiceError(RuntimeError):
    pass


def _server_cfg():
    return (load_config().get("tts") or {}).get("server") or {}


def configured():
    return bool(str(_server_cfg().get("host") or "").strip())


def _encode(kind, data):
    body = json.dumps(data).encode()
    header = {"type": kind, "version": WYOMING_VERSION, "data_length": len(body), "payload_length": 0}
    return json.dumps(header).encode() + b"\n" + body


def _read_exactly(stream, size):
    chunk = stream.read(size)
    if len(chunk) != size:
        raise ServerVoiceError("The voice server's answer ended before the audio was complete.")
    return chunk


def _read_event(stream):
    line = stream.readline(MAX_HEADER_BYTES + 1)
    if not line:
        raise ServerVoiceError("The voice server's answer ended before the audio was complete.")
    if len(line) > MAX_HEADER_BYTES:
        raise ServerVoiceError("The voice server sent an event header that is too long.")
    try:
        header = json.loads(line)
        data = dict(header.get("data") or {})
        data_length = int(header.get("data_length") or 0)
        payload_length = int(header.get("payload_length") or 0)
    except (ValueError, TypeError, AttributeError) as error:
        raise ServerVoiceError(f"The voice server sent an event that isn't understood: {error}") from None
    if not (0 <= data_length <= MAX_EVENT_BYTES and 0 <= payload_length <= MAX_EVENT_BYTES):
        raise ServerVoiceError("The voice server sent an event that is too large.")
    if data_length:
        try:
            data.update(json.loads(_read_exactly(stream, data_length)))
        except ValueError as error:
            raise ServerVoiceError(f"The voice server sent event data that isn't understood: {error}") from None
    payload = _read_exactly(stream, payload_length) if payload_length else b""
    return str(header.get("type", "")), data, payload


def _audio_format(data):
    try:
        rate, width, channels = int(data["rate"]), int(data["width"]), int(data["channels"])
    except (KeyError, ValueError, TypeError):
        raise ServerVoiceError("The voice server didn't say what format its audio is in.") from None
    if width != SAMPLE_WIDTH_BYTES:
        raise ServerVoiceError(f"The voice server sent {width * 8}-bit audio, only 16-bit is understood.")
    if not 1 <= channels <= MAX_CHANNELS or rate <= 0:
        raise ServerVoiceError("The voice server sent audio in a format that isn't understood.")
    return rate, channels


def _wav(pcm, rate, channels):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(SAMPLE_WIDTH_BYTES)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return buffer.getvalue()


def _request(text, settings):
    host = str(settings["host"]).strip()
    port = int(settings.get("port") or DEFAULT_PORT)
    timeout = float(settings.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    pcm = bytearray()
    audio_format = None
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(_encode("synthesize", {"text": text}))
        stream = connection.makefile("rb")
        while True:
            kind, data, payload = _read_event(stream)
            if kind == "error":
                raise ServerVoiceError(str(data.get("text") or "The voice server couldn't speak that."))
            if kind == "audio-start":
                audio_format = _audio_format(data)
            elif kind == "audio-chunk":
                if audio_format is None:
                    raise ServerVoiceError("The voice server sent audio before saying what format it is in.")
                pcm.extend(payload)
                if len(pcm) > MAX_AUDIO_BYTES:
                    raise ServerVoiceError("The voice server sent more audio than is accepted.")
            elif kind == "audio-stop":
                break
    if not pcm or audio_format is None:
        raise ServerVoiceError("The voice server sent no audio.")
    return _wav(bytes(pcm), *audio_format)


def speak_or_raise(text):
    settings = _server_cfg()
    if not str(settings.get("host") or "").strip():
        raise ServerVoiceError("No voice server is set in tts.server.host.")
    return _request(text, settings)


def speak(text):
    global _down_until
    if not configured() or _clock() < _down_until:
        return None
    try:
        return speak_or_raise(text)
    except ServerVoiceError as error:
        print(f"[tts] the voice server couldn't speak that line, using the local voice: {error}", flush=True)
    except OSError as error:
        _down_until = _clock() + RETRY_AFTER_SECONDS
        print(f"[tts] the voice server isn't reachable ({error.__class__.__name__}), using the local voice for "
              f"{int(RETRY_AFTER_SECONDS)} s", flush=True)
    return None
