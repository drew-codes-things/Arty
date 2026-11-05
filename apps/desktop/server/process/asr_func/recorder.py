"""Microphone recorder for the bridge.

Pass a VAD to start() to get speech start/end events, used for barge-in.

Which mic: `asr.input_device` names one (any part of its name), or "auto" or
"default" for the system default. A Bluetooth headset switches from stereo
playback to call mode when its mic is opened, which cuts off whatever she is
saying through it, so name another input here if you wear one.
"""
import queue
import tempfile
import threading
from pathlib import Path

from process.config import load_config

SAMPLERATE = 16000
CHANNELS = 1

PREROLL_SECONDS = 0.35

INPUT_DEVICE = str((load_config().get("asr") or {}).get("input_device") or "auto").strip()


def _input_device(sd, refresh=True):
    """The input device to open: an index, or None for the system default."""
    # PortAudio lists devices once, when it starts. Ask again so headphones
    # connected since then are seen as they are now, not as a stale entry.
    # That restarts PortAudio, so only when no other stream is open.
    if refresh:
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
    if INPUT_DEVICE.lower() in ("", "auto", "default"):
        return None

    for i, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and INPUT_DEVICE.lower() in device["name"].lower():
            return i
    print(f"[mic] no input called {INPUT_DEVICE!r}, using the default", flush=True)
    return None


_open_lock = threading.Lock()
_open_count = 0


def open_input(callback):
    """Open and start a mono float32 input stream on the right mic. The
    wake listener and the recorder can overlap, so this keeps count."""
    global _open_count
    import sounddevice as sd

    with _open_lock:
        stream = sd.InputStream(
            device=_input_device(sd, refresh=_open_count == 0),
            samplerate=SAMPLERATE,
            channels=CHANNELS,
            dtype="float32",
            # 30 ms blocks. Left to itself PortAudio calls back a thousand
            # times a second, and starved of the GIL it drops audio.
            blocksize=int(SAMPLERATE * 0.03),
            callback=callback,
        )
        stream.start()
        _open_count += 1
    return stream


def close_input(stream):
    global _open_count
    try:
        stream.stop()
        stream.close()
    finally:
        with _open_lock:
            _open_count = max(0, _open_count - 1)


class Recorder:
    def __init__(self):
        self._lock = threading.Lock()
        self._stream = None
        self._frames = None
        self._vad = None
        self._pending = None
        self._samples_seen = 0
        self._onset_sample = None
        self.events = None

    @property
    def is_recording(self):
        return self._stream is not None

    @property
    def is_monitoring(self):
        return self._stream is not None and self._vad is not None

    def start(self, vad=None):
        import numpy as np

        with self._lock:
            if self._stream is not None:
                return False

            self._frames = queue.Queue()
            self._vad = vad
            self._pending = np.zeros(0, dtype="float32")
            self._samples_seen = 0
            self._onset_sample = None
            self.events = queue.Queue() if vad is not None else None

            def callback(indata, _frames, _time, status):
                if status:
                    print(f"[mic] {status}", flush=True)
                block = indata.copy()
                self._frames.put(block)
                if self._vad is not None:
                    self._analyse(block.reshape(-1))

            self._stream = open_input(callback)
            return True

    def _analyse(self, mono):
        import numpy as np

        self._pending = np.concatenate((self._pending, mono))
        size = self._vad.frame
        while len(self._pending) >= size:
            frame, self._pending = self._pending[:size], self._pending[size:]
            self._samples_seen += size
            event = self._vad.update(frame)
            if event == "start" and self._onset_sample is None:
                self._onset_sample = self._samples_seen - size
            if event:
                self.events.put(event)

    def stop(self, from_onset=False):
        """Return the WAV path, or None. `from_onset` drops audio from before
        the VAD heard speech."""
        import numpy as np
        import soundfile as sf

        with self._lock:
            if self._stream is None:
                return None

            close_input(self._stream)
            self._stream = None

            chunks = []
            while not self._frames.empty():
                chunks.append(self._frames.get())
            self._frames = None
            onset = self._onset_sample
            self._vad = None
            self._onset_sample = None

        if not chunks:
            return None

        audio = np.concatenate(chunks)
        if from_onset and onset is not None:
            start = max(0, int(onset - PREROLL_SECONDS * SAMPLERATE))
            audio = audio[start:]

        if len(audio) < SAMPLERATE * 0.25:
            return None

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        sf.write(tmp.name, audio, SAMPLERATE)
        return Path(tmp.name)

    def cancel(self):
        path = self.stop()
        if path:
            path.unlink(missing_ok=True)
