import io
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import keepalive  # noqa: E402


class SlowPipe(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()

    def write(self, data):
        with self.lock:
            return super().write(data)

    def flush(self):
        return None


class ClosedPipe:
    def __init__(self, error=BrokenPipeError):
        self.error = error

    def write(self, data):
        raise self.error

    def flush(self):
        return None


class StreamKeepalive(unittest.TestCase):
    def test_a_working_note_goes_out_at_once(self):
        pipe = SlowPipe()
        with keepalive.Stream(pipe, every_seconds=60):
            self.assertEqual(pipe.getvalue(), keepalive.WORKING_NOTE)

    def test_working_notes_keep_coming_while_the_answer_is_slow(self):
        pipe = SlowPipe()
        with keepalive.Stream(pipe, every_seconds=0.05):
            time.sleep(0.3)
        self.assertGreaterEqual(pipe.getvalue().count(keepalive.WORKING_NOTE), 4)

    def test_notes_stop_once_the_stream_is_done(self):
        pipe = SlowPipe()
        with keepalive.Stream(pipe, every_seconds=0.05):
            pass
        after = pipe.getvalue()
        time.sleep(0.2)
        self.assertEqual(pipe.getvalue(), after)

    def test_notes_are_sse_comments_that_readers_skip(self):
        self.assertTrue(keepalive.WORKING_NOTE.startswith(b":"))
        self.assertTrue(keepalive.WORKING_NOTE.endswith(b"\n\n"))
        self.assertNotIn(b"data:", keepalive.WORKING_NOTE)

    def test_answer_text_and_notes_never_interleave_inside_a_line(self):
        pipe = SlowPipe()
        line = b"data: " + b"x" * 5000 + b"\n\n"
        with keepalive.Stream(pipe, every_seconds=0.001) as stream:
            for _ in range(50):
                stream.write(line)
        for chunk in pipe.getvalue().split(b"\n\n"):
            self.assertIn(chunk, (b"", b": working", line[:-2]))

    def test_a_client_that_hangs_up_is_noticed_not_raised(self):
        with keepalive.Stream(ClosedPipe(), every_seconds=60) as stream:
            self.assertFalse(stream.open)
            self.assertFalse(stream.write(b"data: hi\n\n"))

    def test_every_way_a_client_can_drop_the_connection_counts_as_hanging_up(self):
        for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            with self.subTest(error=error.__name__):
                with keepalive.Stream(ClosedPipe(error), every_seconds=60) as stream:
                    self.assertFalse(stream.open)
                    self.assertFalse(stream.write(b"data: hi\n\n"))

    def test_an_error_that_is_not_a_hang_up_is_raised(self):
        stream = keepalive.Stream(ClosedPipe(ValueError("bad chunk")), every_seconds=60)
        with self.assertRaises(ValueError):
            stream.write(b"data: hi\n\n")
        self.assertTrue(stream.open)

    def test_writes_report_success(self):
        pipe = SlowPipe()
        with keepalive.Stream(pipe, every_seconds=60) as stream:
            self.assertTrue(stream.write(b"data: hi\n\n"))
        self.assertTrue(pipe.getvalue().endswith(b"data: hi\n\n"))


if __name__ == "__main__":
    unittest.main()
