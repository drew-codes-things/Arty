import asyncio
import io
import json
import logging
import os
import wave

import numpy

from listening import WHISPER_RATE
from speaking import plain_speech

SATELLITE_HOST = os.environ.get("SATELLITE_HOST", "0.0.0.0")
SATELLITE_PORT = int(os.environ.get("SATELLITE_PORT", "10300"))
MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_LISTEN_SECONDS = 30
SAMPLE_WIDTH = 2
LOWEST_RATE = 8000
HIGHEST_RATE = 48000
MOST_CHANNELS = 2
MAX_SPEAK_CHARS = 12000
COMMAND_BEAM_SIZE = 2
WYOMING_VERSION = "1.0.0"
ABOUT = {"name": "Arty", "url": "https://github.com/drew-codes-things/Arty"}

log = logging.getLogger("arty-satellite")


class HungUp(Exception):
    pass


def parse_allow(text: str) -> set[str]:
    return {address.strip() for address in text.split(",") if address.strip()}


def describe() -> dict:
    listed = {"attribution": ABOUT, "installed": True, "version": "1"}
    return {
        "asr": [{"name": "arty-whisper", "description": "Arty speech recognition (Faster-Whisper)", **listed,
                 "models": [{"name": "arty", "description": "Faster-Whisper", **listed, "languages": ["en"]}]}],
        "tts": [{"name": "arty-piper", "description": "Arty text to speech (Piper)", **listed,
                 "voices": [{"name": "arty", "description": "Arty", **listed, "languages": ["en"]}]}],
    }


def encode_event(kind: str, data: dict | None = None, payload: bytes = b"") -> bytes:
    body = json.dumps(data).encode() if data else b""
    header = {"type": kind, "version": WYOMING_VERSION, "data_length": len(body), "payload_length": len(payload)}
    return json.dumps(header).encode() + b"\n" + body + payload


async def read_exactly(reader: asyncio.StreamReader, size: int) -> bytes:
    try:
        return await reader.readexactly(size)
    except asyncio.IncompleteReadError:
        raise HungUp() from None


async def read_header(reader: asyncio.StreamReader) -> bytes:
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as error:
        if not error.partial:
            raise HungUp() from None
        raise ValueError("the event header ended early") from None
    except asyncio.LimitOverrunError:
        raise ValueError("the event header is too long") from None
    if len(line) > MAX_HEADER_BYTES:
        raise ValueError("the event header is too long")
    return line


def object_from(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"the {what} isn't a JSON object")
    return dict(value)


def length_from(value) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"an event length isn't a whole number: {value!r}")
    return int(value)


async def read_event(reader: asyncio.StreamReader) -> tuple[str, dict, bytes]:
    header = json.loads(await read_header(reader))
    if not isinstance(header, dict):
        raise ValueError("the event header isn't a JSON object")
    data = object_from(header.get("data") or {}, "event data")
    data_length = length_from(header.get("data_length"))
    payload_length = length_from(header.get("payload_length"))
    if not (0 <= data_length <= MAX_BODY_BYTES and 0 <= payload_length <= MAX_BODY_BYTES):
        raise ValueError("the event is too large")
    if data_length:
        data.update(object_from(json.loads(await read_exactly(reader, data_length)), "event body"))
    payload = await read_exactly(reader, payload_length) if payload_length else b""
    return str(header.get("type", "")), data, payload


def to_whisper_pcm(pcm: bytes, rate: int, width: int, channels: int) -> bytes:
    if width != SAMPLE_WIDTH or not pcm or channels < 1:
        return b""
    frame_bytes = SAMPLE_WIDTH * channels
    samples = numpy.frombuffer(pcm[:len(pcm) - len(pcm) % frame_bytes], dtype="<i2").astype(numpy.float32)
    mono = samples.reshape(-1, channels).mean(axis=1)
    if rate != WHISPER_RATE and len(mono):
        wanted = int(len(mono) * WHISPER_RATE / rate)
        mono = numpy.interp(numpy.linspace(0, len(mono) - 1, wanted), numpy.arange(len(mono)), mono)
    return mono.astype("<i2").tobytes()


def wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(wav_bytes)) as reader:
        if reader.getsampwidth() != SAMPLE_WIDTH:
            raise ValueError(f"the voice gave {reader.getsampwidth() * 8}-bit audio, not 16-bit")
        frames = reader.readframes(reader.getnframes())
        channels, rate = reader.getnchannels(), reader.getframerate()
    if channels > 1:
        samples = numpy.frombuffer(frames, dtype="<i2").reshape(-1, channels).mean(axis=1)
        frames = samples.astype("<i2").tobytes()
    return frames, rate


def whole_number(value, default: int) -> int:
    if value is None:
        return default
    not_whole = isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not value.is_integer())
    if not_whole:
        raise ValueError(f"the audio format isn't made of whole numbers: {value!r}")
    return int(value) or default


def checked_audio_format(data: dict, current: dict) -> dict:
    audio_format = {key: whole_number(data.get(key), value) for key, value in current.items()}
    if not LOWEST_RATE <= audio_format["rate"] <= HIGHEST_RATE:
        raise ValueError(f"the audio rate {audio_format['rate']} is outside {LOWEST_RATE} to {HIGHEST_RATE}")
    if audio_format["width"] != SAMPLE_WIDTH:
        raise ValueError(f"the audio is {audio_format['width'] * 8}-bit, only 16-bit is understood")
    if not 1 <= audio_format["channels"] <= MOST_CHANNELS:
        raise ValueError(f"the audio has {audio_format['channels']} channels, at most {MOST_CHANNELS} are understood")
    return audio_format


class Session:

    def __init__(self, senses, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client: str):
        self.senses = senses
        self.reader = reader
        self.writer = writer
        self.client = client
        self.heard = bytearray()
        self.audio_format = {"rate": WHISPER_RATE, "width": SAMPLE_WIDTH, "channels": 1}

    async def send(self, kind: str, data: dict | None = None, payload: bytes = b"") -> None:
        self.writer.write(encode_event(kind, data, payload))
        await self.writer.drain()

    def listen_limit(self) -> int:
        return MAX_LISTEN_SECONDS * self.audio_format["rate"] * self.audio_format["width"] * self.audio_format["channels"]

    async def run(self) -> None:
        while True:
            kind, data, payload = await read_event(self.reader)
            if kind == "describe":
                await self.send("info", describe())
            elif kind in ("transcribe", "audio-start"):
                self.start_listening(kind, data)
            elif kind == "audio-chunk" and len(self.heard) + len(payload) <= self.listen_limit():
                self.heard.extend(payload)
            elif kind == "audio-stop":
                await self.transcribe()
            elif kind == "synthesize":
                await self.speak(str(data.get("text") or ""))

    def start_listening(self, kind: str, data: dict) -> None:
        if kind == "audio-start":
            self.audio_format = checked_audio_format(data, self.audio_format)
        self.heard.clear()

    async def transcribe(self) -> None:
        audio = to_whisper_pcm(bytes(self.heard), **self.audio_format)
        self.heard.clear()
        try:
            text = await asyncio.to_thread(self.senses.transcribe, self.senses.command_model, audio, COMMAND_BEAM_SIZE)
        except Exception as error:
            log.error("transcribing for %s failed: %s", self.client, error)
            await self.send("error", {"text": "Arty couldn't transcribe that audio."})
            return
        log.info("satellite %s heard something (%d chars)", self.client, len(text))
        await self.send("transcript", {"text": text})

    async def speak(self, raw_text: str) -> None:
        if len(raw_text) > MAX_SPEAK_CHARS:
            await self.send("error", {"text": f"That's too much to speak at once (over {MAX_SPEAK_CHARS} characters)."})
            return
        text = plain_speech(raw_text)
        if not text:
            await self.send("error", {"text": "There was no text to speak."})
            return
        try:
            pcm, rate = wav_to_pcm(await asyncio.to_thread(self.senses.wav, text))
        except Exception as error:
            log.error("speaking for %s failed: %s", self.client, error)
            await self.send("error", {"text": "Arty couldn't speak that text."})
            return
        audio_format = {"rate": rate, "width": SAMPLE_WIDTH, "channels": 1}
        await self.send("audio-start", audio_format)
        one_second = rate * SAMPLE_WIDTH
        for start in range(0, len(pcm), one_second):
            await self.send("audio-chunk", audio_format, pcm[start:start + one_second])
        await self.send("audio-stop")


def connection_handler(senses, allowed: set[str]):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = (writer.get_extra_info("peername") or ("?",))[0]
        try:
            if client not in allowed:
                log.warning("satellite refused %s: not in SATELLITE_ALLOW", client)
                return
            await Session(senses, reader, writer, client).run()
        except HungUp:
            pass
        except (ValueError, ConnectionError) as error:
            log.warning("satellite %s: %s", client, error)
        finally:
            writer.close()
    return handle


async def start_server(senses, allowed: set[str], host: str, port: int) -> asyncio.Server:
    return await asyncio.start_server(connection_handler(senses, allowed), host, port, limit=MAX_HEADER_BYTES)


async def serve(senses, allowed: set[str] | None = None, host: str = SATELLITE_HOST, port: int = SATELLITE_PORT) -> None:
    allowed = parse_allow(os.environ.get("SATELLITE_ALLOW", "")) if allowed is None else allowed
    if not allowed:
        log.info("SATELLITE_ALLOW isn't set, so no speaker can use Arty's Wyoming speech server")
        return
    server = await start_server(senses, allowed, host, port)
    log.info("Wyoming speech server on %s:%d for %s", host, port, ", ".join(sorted(allowed)))
    async with server:
        await server.serve_forever()
