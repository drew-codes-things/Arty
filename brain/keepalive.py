import threading

WORKING_NOTE = b": working\n\n"
DEFAULT_EVERY_SECONDS = 15
HUNG_UP_ERRORS = (ConnectionError, TimeoutError)


class Stream:
    def __init__(self, pipe, every_seconds: float = DEFAULT_EVERY_SECONDS):
        self.pipe = pipe
        self.every_seconds = every_seconds
        self.open = True
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._beat = threading.Thread(target=self._keep_sending, daemon=True)

    def __enter__(self):
        self.write(WORKING_NOTE)
        self._beat.start()
        return self

    def __exit__(self, *_):
        self._done.set()
        self._beat.join()
        return False

    def write(self, data: bytes) -> bool:
        with self._lock:
            if not self.open:
                return False
            try:
                self.pipe.write(data)
                self.pipe.flush()
            except HUNG_UP_ERRORS:
                self.open = False
            return self.open

    def _keep_sending(self) -> None:
        while not self._done.wait(self.every_seconds):
            if not self.write(WORKING_NOTE):
                return
