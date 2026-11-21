import numpy

FRAME_SAMPLES = 480
DEFAULT_PARTITIONS = 12
DEFAULT_STEP = 1.0
POWER_SMOOTHING = 0.8
REGULARISATION_SHARE = 3e-2
REGULARISATION_FLOOR = 0.1


class EchoCanceller:

    def __init__(self, block: int = FRAME_SAMPLES, partitions: int = DEFAULT_PARTITIONS,
                 step: float = DEFAULT_STEP):
        if block < 1 or partitions < 1:
            raise ValueError("block and partitions must be at least 1")
        if not 0 < step < 2:
            raise ValueError(f"step must be between 0 and 2, not {step}")
        self.block = block
        self.partitions = partitions
        self.step = step
        self.bins = block + 1
        self.reset()

    def reset(self) -> None:
        self._weights = numpy.zeros((self.partitions, self.bins), dtype=numpy.complex128)
        self._history = numpy.zeros((self.partitions, self.bins), dtype=numpy.complex128)
        self._head = 0
        self._previous_reference = numpy.zeros(self.block)
        self._power = numpy.zeros(self.bins)
        self._ordered = self._history
        self._error = numpy.zeros(self.block)

    def clear_history(self) -> None:
        self._history = numpy.zeros((self.partitions, self.bins), dtype=numpy.complex128)
        self._head = 0
        self._previous_reference = numpy.zeros(self.block)
        self._power = numpy.zeros(self.bins)
        self._ordered = self._history
        self._error = numpy.zeros(self.block)

    def strongest_partition(self) -> int:
        energy = (numpy.abs(self._weights) ** 2).sum(axis=1)
        return int(numpy.argmax(energy)) if energy.any() else 0

    def cancel(self, mic: numpy.ndarray, reference: numpy.ndarray) -> numpy.ndarray:
        if len(mic) != self.block or len(reference) != self.block:
            raise ValueError(f"frames must be {self.block} samples, got {len(mic)} and {len(reference)}")
        window = numpy.concatenate((self._previous_reference, reference))
        self._previous_reference = numpy.array(reference, dtype=numpy.float64)
        self._head = (self._head - 1) % self.partitions
        self._history[self._head] = numpy.fft.rfft(window)
        order = (self._head + numpy.arange(self.partitions)) % self.partitions
        self._ordered = self._history[order]
        echo_spectrum = (self._weights * self._ordered).sum(axis=0)
        echo = numpy.fft.irfft(echo_spectrum, n=2 * self.block)[self.block:]
        self._error = numpy.asarray(mic, dtype=numpy.float64) - echo
        return self._error

    def learn(self, rate: float = 1.0) -> None:
        if rate <= 0:
            return
        error_spectrum = numpy.fft.rfft(numpy.concatenate((numpy.zeros(self.block), self._error)))
        instant = (numpy.abs(self._ordered) ** 2).sum(axis=0)
        self._power = numpy.maximum(instant, POWER_SMOOTHING * self._power + (1 - POWER_SMOOTHING) * instant)
        regularisation = REGULARISATION_SHARE * float(self._power.mean()) + REGULARISATION_FLOOR
        gradient = numpy.conj(self._ordered) * error_spectrum / (self._power + regularisation)
        time_gradient = numpy.fft.irfft(gradient, n=2 * self.block, axis=1)
        time_gradient[:, self.block:] = 0.0
        self._weights += rate * self.step * numpy.fft.rfft(time_gradient, axis=1)


class DelayTracker:

    def __init__(self, block: int = FRAME_SAMPLES, window_samples: int = 16000, max_lag_samples: int = 12800,
                 interval_frames: int = 8, min_confidence: float = 15.0, tolerance_samples: int = 800,
                 margin_samples: int = 960, min_level: float = 1e-4):
        self.block = block
        self.window = window_samples
        self.max_lag = max_lag_samples
        self.interval_frames = interval_frames
        self.min_confidence = min_confidence
        self.tolerance = tolerance_samples
        self.margin = margin_samples
        self.min_level = min_level
        self.reset()

    def reset(self) -> None:
        self._reference = numpy.zeros(self.window + self.max_lag)
        self._mic = numpy.zeros(self.window)
        self._frames = 0
        self._candidate: int | None = None
        self.lag: int | None = None
        self.confidence = 0.0

    def clear_signals(self) -> None:
        self._reference = numpy.zeros(self.window + self.max_lag)
        self._mic = numpy.zeros(self.window)
        self._frames = 0

    @property
    def shift(self) -> int:
        return 0 if self.lag is None else max(0, self.lag - self.margin)

    def push(self, reference: numpy.ndarray, mic: numpy.ndarray) -> numpy.ndarray:
        self._reference = numpy.concatenate((self._reference[self.block:], reference))
        self._mic = numpy.concatenate((self._mic[self.block:], mic))
        self._frames += 1
        if self._frames % self.interval_frames == 0:
            self._estimate()
        if self.shift == 0:
            return numpy.asarray(reference, dtype=numpy.float64)
        end = len(self._reference) - self.shift
        return self._reference[end - self.block:end]

    def _estimate(self) -> None:
        if self._frames * self.block < self.window // 2:
            return
        recent = self._reference[-self.window:]
        if float(numpy.sqrt(numpy.mean(recent ** 2))) < self.min_level:
            return
        size = 1 << int(numpy.ceil(numpy.log2(self.window + self.max_lag)))
        reference_spectrum = numpy.fft.rfft(self._reference, size)
        mic_spectrum = numpy.fft.rfft(self._mic, size)
        cross = reference_spectrum * numpy.conj(mic_spectrum)
        cross /= numpy.abs(cross) + 1e-12
        correlation = numpy.abs(numpy.fft.irfft(cross, size)[:self.max_lag + 1])
        peak = int(numpy.argmax(correlation))
        self.confidence = float(correlation[peak] / (numpy.median(correlation) + 1e-12))
        if self.confidence < self.min_confidence:
            return
        self._accept(self.max_lag - peak)

    def _accept(self, lag: int) -> None:
        if self.lag is None:
            self.lag = lag
            return
        if abs(lag - self.lag) <= self.tolerance:
            self._candidate = None
            return
        if self._candidate is not None and abs(lag - self._candidate) <= self.tolerance:
            self.lag, self._candidate = lag, None
            return
        self._candidate = lag
