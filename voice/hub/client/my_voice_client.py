import asyncio
import io
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
WINDOWS = platform.system() == "Windows"
RETRY_SECONDS = (1, 2, 5, 10, 30)
LINUX_SOURCE = "my_voice_mic"
WINDOWS_CABLE = "CABLE Input"

log = logging.getLogger("my-voice")


def hub_socket_url(path: str) -> str:
    return CONFIG["hub"].replace("http://", "ws://", 1).replace("https://", "wss://", 1).rstrip("/") + path


def headers() -> dict:
    return {"Authorization": f"Bearer {CONFIG['token']}"}


def unload_leftover_sources() -> None:
    modules = subprocess.run(["pactl", "list", "short", "modules"], capture_output=True, text=True, check=True)
    for line in modules.stdout.splitlines():
        if "module-pipe-source" in line and f"source_name={LINUX_SOURCE}" in line:
            subprocess.run(["pactl", "unload-module", line.split()[0]], capture_output=True)


class LinuxMicOutput:

    def __init__(self, rate: int):
        runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self.fifo = os.path.join(runtime, "my-voice-mic.fifo")
        self.module = ""
        self.rate = rate
        self.pipe = None

    def open(self) -> None:
        self.close()
        unload_leftover_sources()
        result = subprocess.run(
            ["pactl", "load-module", "module-pipe-source", f"source_name={LINUX_SOURCE}", f"file={self.fifo}",
             "format=s16le", f"rate={self.rate}", "channels=1",
             'source_properties=device.description="Server mic (my-voice)"'],
            capture_output=True, text=True, check=True)
        self.module = result.stdout.strip()
        self.pipe = open(self.fifo, "wb", buffering=0)

    def write(self, chunk: bytes) -> None:
        self.pipe.write(chunk)

    def close(self) -> None:
        if self.pipe:
            self.pipe.close()
            self.pipe = None
        if self.module:
            subprocess.run(["pactl", "unload-module", self.module], capture_output=True)
            self.module = ""


class WindowsMicOutput:

    def __init__(self, rate: int):
        import sounddevice
        self.sounddevice = sounddevice
        self.rate = rate
        self.stream = None

    def open(self) -> None:
        device = next((i for i, d in enumerate(self.sounddevice.query_devices())
                       if d["name"].startswith(WINDOWS_CABLE) and d["max_output_channels"] > 0), None)
        if device is None:
            raise RuntimeError("VB-CABLE isn't installed - install it from https://vb-audio.com/Cable/ "
                               "and run the setup again")
        self.stream = self.sounddevice.RawOutputStream(samplerate=self.rate, channels=1, dtype="int16",
                                                       device=device, latency="low")
        self.stream.start()

    def write(self, chunk: bytes) -> None:
        self.stream.write(chunk)

    def close(self) -> None:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def play_wav(wav: bytes) -> None:
    if WINDOWS:
        import numpy
        import sounddevice
        with wave.open(io.BytesIO(wav)) as reader:
            frames = numpy.frombuffer(reader.readframes(reader.getnframes()), dtype=numpy.int16)
            channels, rate = reader.getnchannels(), reader.getframerate()
        sounddevice.play(frames.reshape(-1, channels), rate, blocking=True)
        return
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        f.write(wav)
        f.flush()
        subprocess.run(["paplay", f.name], check=False)


async def run_forever(name: str, session) -> None:
    attempt = 0
    while True:
        try:
            await session()
            attempt = 0
        except InvalidStatus as e:
            if e.response.status_code == 401:
                log.error("%s: this device's key was revoked or is wrong - download a new setup from %s",
                          name, CONFIG["hub"])
                raise SystemExit(1)
            log.warning("%s: hub said %s", name, e.response.status_code)
        except (OSError, ConnectionClosed, RuntimeError, subprocess.CalledProcessError) as e:
            log.warning("%s: %s", name, e)
        delay = RETRY_SECONDS[min(attempt, len(RETRY_SECONDS) - 1)]
        attempt += 1
        log.info("%s: reconnecting in %d s", name, delay)
        await asyncio.sleep(delay)


async def mic_session() -> None:
    async with websockets.connect(hub_socket_url("/ws/mic"), additional_headers=headers(),
                                  max_size=2 ** 20) as ws:
        settings = json.loads(await ws.recv())
        output = (WindowsMicOutput if WINDOWS else LinuxMicOutput)(settings["rate"])
        await asyncio.to_thread(output.open)
        log.info("mic: connected, the server mic is live on this PC")
        try:
            async for chunk in ws:
                if isinstance(chunk, bytes):
                    await asyncio.to_thread(output.write, chunk)
        finally:
            output.close()


async def speaker_session() -> None:
    async with websockets.connect(hub_socket_url("/ws/speaker"), additional_headers=headers(),
                                  max_size=32 * 2 ** 20) as ws:
        log.info("speaker: connected, Arty's voice will play here")
        async for message in ws:
            if isinstance(message, bytes):
                await asyncio.to_thread(play_wav, message)


async def main() -> None:
    await asyncio.gather(run_forever("mic", mic_session), run_forever("speaker", speaker_session))


if __name__ == "__main__":
    handlers = [logging.FileHandler(HERE / "my-voice.log", encoding="utf-8")]
    if sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=handlers)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
