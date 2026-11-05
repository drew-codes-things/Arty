"""Voice activity detection for barge-in.

Her own voice comes back through the mic, so a fixed threshold doesn't work.
The noise floor is a percentile of the last few seconds, which rises to meet
her voice, and speech has to stay `margin_db` above it for `speech_ms`.
"""
import math
from collections import deque


class VAD:
    """Feed frames to `update`, which returns 'start', 'end' or None.

    Talking over her measured about +10 dB at normal speaker volume and
    +3.6 dB at very loud, hence the default margin of 8.
    """

    def __init__(self, samplerate, frame_seconds=0.03, margin_db=8.0,
                 speech_ms=300, silence_ms=800, calibrate_ms=900,
                 window_seconds=2.5, percentile=75):
        self.frame = max(1, int(samplerate * frame_seconds))
        self.margin_db = float(margin_db)
        self.speech_frames = max(1, int((speech_ms / 1000.0) / frame_seconds))
        self.silence_frames = max(1, int((silence_ms / 1000.0) / frame_seconds))
        self.calibrate_frames = max(1, int((calibrate_ms / 1000.0) / frame_seconds))
        self.percentile = percentile

        self._window = deque(maxlen=max(4, int(window_seconds / frame_seconds)))

        self.floor = None
        self.speaking = False
        self._seen = 0
        self._score = 0.0
        self._below = 0

    @staticmethod
    def level_db(frame):
        import numpy as np

        rms = float(np.sqrt(np.mean(np.square(frame, dtype="float64")) + 1e-12))
        return 20.0 * math.log10(rms + 1e-9)

    def _refloor(self):
        ordered = sorted(self._window)
        idx = min(len(ordered) - 1,
                  int(len(ordered) * self.percentile / 100.0))
        self.floor = ordered[idx]

    def update(self, frame):
        db = self.level_db(frame)
        self._seen += 1

        calibrating = self._seen <= self.calibrate_frames
        loud = (not calibrating
                and self.floor is not None
                and db > self.floor + self.margin_db)

        if calibrating or (not loud and not self.speaking):
            self._window.append(db)
            self._refloor()

        if calibrating:
            return None

        self._score = max(0.0, self._score + (1.0 if loud else -0.6))

        if loud:
            self._below = 0
        else:
            self._below += 1

        if not self.speaking and self._score >= self.speech_frames:
            self.speaking = True
            self._score = 0.0
            return "start"

        if self.speaking and self._below >= self.silence_frames:
            self.speaking = False
            self._score = 0.0
            return "end"

        return None
