import http.client
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-web-app-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import web_app  # noqa: E402


def make_app_folder() -> Path:
    root = Path(tempfile.mkdtemp(prefix="arty-ios-"))
    (root / "index.html").write_text("<!doctype html><title>Arty</title>")
    (root / "app.js").write_text("export const ready = true;")
    (root / "fonts").mkdir()
    (root / "fonts" / "plex.ttf").write_bytes(b"\x00\x01")
    (root / "tests").mkdir()
    (root / "tests" / "api.test.js").write_text("secret test")
    (root / ".env").write_text("KEY=1")
    (root / "notes.txt").write_text("not served")
    (root.parent / "outside.html").write_text("outside")
    return root


class FindingFiles(unittest.TestCase):
    def setUp(self):
        self.root = make_app_folder()

    def test_the_bare_prefix_is_the_index_page(self):
        self.assertEqual(web_app.find(["ios"], self.root), (self.root / "index.html").resolve())

    def test_files_and_subfolders_are_found(self):
        self.assertEqual(web_app.find(["ios", "app.js"], self.root), (self.root / "app.js").resolve())
        self.assertEqual(web_app.find(["ios", "fonts", "plex.ttf"], self.root), (self.root / "fonts" / "plex.ttf").resolve())

    def test_nothing_outside_the_folder_or_hidden_is_served(self):
        for segments in (["ios", "..", "outside.html"], ["ios", ".env"], ["ios", "tests", "api.test.js"],
                         ["ios", "notes.txt"], ["ios", "missing.js"], ["ios", "fonts"]):
            with self.subTest(segments=segments):
                self.assertIsNone(web_app.find(segments, self.root))

    def test_every_served_type_has_a_content_type(self):
        self.assertEqual(web_app.content_type(self.root / "app.js"), "text/javascript; charset=utf-8")
        self.assertEqual(web_app.content_type(self.root / "index.html"), "text/html; charset=utf-8")


class ServingOverHttp(unittest.TestCase):
    def setUp(self):
        self.root = make_app_folder()
        patch = unittest.mock.patch.object(web_app, "ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), brain.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response, response.read()

    def test_the_app_is_served_without_a_key_and_locked_down(self):
        response, body = self.get("/ios/")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"<!doctype html><title>Arty</title>")
        self.assertEqual(response.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertIn("connect-src 'self'", response.getheader("Content-Security-Policy"))
        self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")

    def test_the_prefix_without_a_slash_redirects_so_relative_links_work(self):
        response, _ = self.get("/ios")
        self.assertEqual(response.status, 301)
        self.assertEqual(response.getheader("Location"), "/ios/")

    def test_escaping_the_folder_is_not_found(self):
        response, body = self.get("/ios/../outside.html")
        self.assertEqual(response.status, 404)
        self.assertNotIn(b"outside", body)

    def test_the_api_still_needs_the_key(self):
        response, _ = self.get("/v1/models")
        self.assertEqual(response.status, 401)


if __name__ == "__main__":
    unittest.main()
