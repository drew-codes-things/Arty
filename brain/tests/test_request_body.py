import http.client
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-request-body-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402

ANSWER_WITHIN_SECONDS = 5


class BrokenBodyLengths(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), brain.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post_with_length(self, length):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=ANSWER_WITHIN_SECONDS)
        self.addCleanup(connection.close)
        connection.putrequest("POST", "/v1/tasks")
        connection.putheader("Authorization", f"Bearer {os.environ['BRAIN_API_KEY']}")
        connection.putheader("Content-Length", length)
        connection.endheaders()
        return connection.getresponse()

    def test_a_negative_length_is_refused_without_waiting_for_the_client(self):
        response = self.post_with_length("-1")
        self.assertEqual(response.status, 400)
        self.assertIn(b"Content-Length", response.read())

    def test_a_length_that_is_not_a_number_is_refused(self):
        response = self.post_with_length("lots")
        self.assertEqual(response.status, 400)
        self.assertIn(b"Content-Length", response.read())


if __name__ == "__main__":
    unittest.main()
