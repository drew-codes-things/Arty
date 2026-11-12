import concurrent.futures
import logging
import queue
import threading
from pathlib import Path
from typing import Callable

from listening import WHISPER_RATE, to_float

LIVE_IDLE_SECONDS = 30.0
FINISH_TIMEOUT_SECONDS = 10.0
UPDATE_SECONDS = 0.5
WAKE_KEYTERMS = ("Arty",)
FINISH = object()
CANCEL = object()

log = logging.getLogger("arty-voice")


class LiveTextFailed(Exception):
    pass


class LiveTranscript:

    def __init__(self, open_stream: Callable[[], object], lock: threading.Lock,
                 idle_seconds: float = LIVE_IDLE_SECONDS):
        self.open_stream = open_stream
        self.lock = lock
        self.idle_seconds = idle_seconds
        self.inbox: queue.SimpleQueue = queue.SimpleQueue()
        self.text: concurrent.futures.Future[str] = concurrent.futures.Future()
        threading.Thread(target=self._run, name="live-transcript", daemon=True).start()

    def add(self, audio: bytes) -> None:
        self.inbox.put(audio)

    def cancel(self) -> None:
        self.inbox.put(CANCEL)

    def finish(self, timeout: float = FINISH_TIMEOUT_SECONDS) -> str:
        self.inbox.put(FINISH)
        try:
            return self.text.result(timeout)
        except concurrent.futures.TimeoutError:
            raise LiveTextFailed(f"no text after {timeout} s") from None

    def _run(self) -> None:
        try:
            with self.lock:
                stream = self.open_stream()
                stream.start()
        except Exception as error:
            self._fail(error)
            return
        try:
            self._listen(stream)
        except Exception as error:
            self._fail(error)
        finally:
            with self.lock:
                stream.close()

    def _listen(self, stream) -> None:
        while True:
            try:
                item = self.inbox.get(timeout=self.idle_seconds)
            except queue.Empty:
                raise LiveTextFailed(f"nothing arrived for {self.idle_seconds} s, gave up") from None
            if item is CANCEL:
                raise LiveTextFailed("cancelled")
            if item is FINISH:
                with self.lock:
                    transcript = stream.stop()
                self.text.set_result(" ".join(line.text.strip() for line in transcript.lines).strip())
                return
            with self.lock:
                stream.add_audio(to_float(item).tolist(), WHISPER_RATE)

    def _fail(self, error: Exception) -> None:
        if not isinstance(error, LiveTextFailed):
            log.warning("the live transcript failed: %r", error)
            error = LiveTextFailed(str(error))
        if not self.text.done():
            self.text.set_exception(error)


class MoonshineEar:

    def __init__(self, transcriber):
        self.transcriber = transcriber
        self.lock = threading.Lock()

    @classmethod
    def load(cls, folder: Path) -> "MoonshineEar":
        from moonshine_voice import ModelArch
        from moonshine_voice.transcriber import Transcriber
        transcriber = Transcriber(str(folder), ModelArch.TINY_STREAMING)
        transcriber.set_keyterms(list(WAKE_KEYTERMS))
        return cls(transcriber)

    def start(self) -> LiveTranscript:
        return LiveTranscript(lambda: self.transcriber.create_stream(update_interval=UPDATE_SECONDS), self.lock)
