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


async def read_event(reader: asyncio.StreamReader) -> tuple[str, dict, bytes]:
    header = json.loads(await read_header(reader))
    data = dict(header.get("data") or {})
    data_length = int(header.get("data_length") or 0)
    payload_length = int(header.get("payload_length") or 0)
    if not (0 <= data_length <= MAX_BODY_BYTES and 0 <= payload_length <= MAX_BODY_BYTES):
        raise ValueError("the event is too large")
    if data_length:
        data.update(json.loads(await read_exactly(reader, data_length)))
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
                await self.speak(plain_speech(str(data.get("text") or "")))

    def start_listening(self, kind: str, data: dict) -> None:
        if kind == "audio-start":
            self.audio_format = {key: int(data.get(key) or value) for key, value in self.audio_format.items()}
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

    async def speak(self, text: str) -> None:
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
