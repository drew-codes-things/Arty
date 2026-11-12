import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BRAIN_FILES", tempfile.mkdtemp(prefix="arty-sharing-"))

import media
import sharing

PNG = b"\x89PNG\r\n\x1a\nfake picture"


class FakeResponse(io.BytesIO):
    def __init__(self, data, mime):
        super().__init__(data)
        self.headers = unittest.mock.Mock(get_content_type=lambda: mime)

    def __enter__(self):
        return self

    def __exit__(self, *details):
        self.close()


class Screenshots(unittest.TestCase):
    def test_a_screenshot_is_stored_and_shown_as_a_picture(self):
        with unittest.mock.patch.object(sharing.remote, "ssh", return_value=(0, PNG + b"screen")):
            reply = sharing.send_screenshot()
        self.assertIn("![arty-desktop-", reply)
        self.assertIn(f"/{media.DOWNLOAD_PREFIX}/", reply)
        self.assertIn(sharing.SENT_NOTE, reply)

    def test_a_failed_screenshot_says_why(self):
        with unittest.mock.patch.object(sharing.remote, "ssh", return_value=(1, b"cannot open display")):
            self.assertIn("cannot open display", sharing.send_screenshot())


class Recordings(unittest.TestCase):
    def run_recording(self, seconds):
        commands = []

        def text(command, stdin=None, timeout=None):
            commands.append(command)
            if command == "command -v ffmpeg":
                return 0, "/usr/bin/ffmpeg\n"
            if "getdisplaygeometry" in command:
                return 0, "1920 1080\n"
            return 0, ""

        with unittest.mock.patch.object(sharing.remote, "text", side_effect=text), \
                unittest.mock.patch.object(sharing.remote, "ssh", return_value=(0, b"fake mp4")):
            reply = sharing.send_screen_recording(seconds)
        return reply, commands[-1]

    def test_a_recording_is_sent_as_a_video_link(self):
        reply, command = self.run_recording(5)
        self.assertIn("-t 5 ", command)
        self.assertIn("-video_size 1920x1080", command)
        self.assertIn(".mp4](", reply)
        self.assertNotIn("![", reply)

    def test_the_length_is_kept_within_limits(self):
        self.assertIn(f"-t {sharing.MAX_RECORDING_SECONDS} ", self.run_recording(600)[1])
        self.assertIn("-t 1 ", self.run_recording(-4)[1])
        self.assertIn(f"-t {sharing.DEFAULT_RECORDING_SECONDS} ", self.run_recording("soon")[1])

    def test_no_ffmpeg_is_explained(self):
        with unittest.mock.patch.object(sharing.remote, "text", return_value=(1, "")):
            self.assertIn("apt install ffmpeg", sharing.send_screen_recording(5))


class LiveRecordings(unittest.TestCase):
    def desktop(self, state, stopped_after="7"):
        commands = []

        def text(command, stdin=None, timeout=None):
            commands.append(command)
            if command == "command -v ffmpeg":
                return 0, "/usr/bin/ffmpeg\n"
            if "getdisplaygeometry" in command:
                return 0, "1920 1080\n"
            if command.startswith(f"cat {sharing.LIVE_RECORDING_STATE}"):
                return (0, state) if state else (1, "")
            if "kill -INT" in command:
                return 0, stopped_after
            return 0, ""

        return commands, text

    def test_starting_runs_ffmpeg_in_the_background_with_a_time_limit(self):
        commands, text = self.desktop(None)
        with unittest.mock.patch.object(sharing.remote, "text", side_effect=text):
            reply = sharing.start_screen_recording()
        self.assertIn("stop_screen_recording", reply)
        self.assertIn("browser", reply)
        launch = commands[-1]
        self.assertIn(f"{sharing.remote.GUI_ENV} nohup ffmpeg", launch)
        self.assertIn(f"-t {sharing.MAX_RECORDING_SECONDS} ", launch)
        self.assertIn(sharing.LIVE_RECORDING_FILE, launch)
        self.assertIn("&", launch)

    def test_a_second_start_while_recording_is_refused(self):
        commands, text = self.desktop("4242 1700000000\nrunning\n")
        with unittest.mock.patch.object(sharing.remote, "text", side_effect=text):
            self.assertIn("already recording", sharing.start_screen_recording())
        self.assertFalse(any("nohup" in command for command in commands))

    def test_a_finished_recording_does_not_block_a_new_one(self):
        commands, text = self.desktop("4242 1700000000\nfinished\n")
        with unittest.mock.patch.object(sharing.remote, "text", side_effect=text):
            sharing.start_screen_recording()
        self.assertTrue(any("nohup" in command for command in commands))

    def test_stopping_ends_ffmpeg_cleanly_and_sends_the_video(self):
        commands, text = self.desktop("4242 1700000000\nrunning\n")
        with unittest.mock.patch.object(sharing.remote, "text", side_effect=text), \
                unittest.mock.patch.object(sharing.remote, "ssh", return_value=(0, b"live mp4")):
            reply = sharing.stop_screen_recording()
        self.assertTrue(any("kill -INT 4242" in command for command in commands))
        self.assertIn("a 7-second recording", reply)
        self.assertIn(".mp4](", reply)

    def test_stopping_without_a_recording_says_so(self):
        _, text = self.desktop(None)
        with unittest.mock.patch.object(sharing.remote, "text", side_effect=text):
            self.assertIn("start_screen_recording first", sharing.stop_screen_recording())

    def test_an_empty_recording_is_not_sent(self):
        _, text = self.desktop("4242 1700000000\nrunning\n", stopped_after="")
        with unittest.mock.patch.object(sharing.remote, "text", side_effect=text):
            self.assertIn("didn't save anything", sharing.stop_screen_recording())


class Files(unittest.TestCase):
    def desktop(self, real_path, size="12"):
        def text(command, stdin=None, timeout=None):
            if command.startswith("realpath"):
                return (0, real_path + "\n") if real_path else (1, "")
            return 0, size + "\n"
        return unittest.mock.patch.object(sharing.remote, "text", side_effect=text)

    def test_a_file_from_the_desktop_is_sent(self):
        with self.desktop("/home/ai/Pictures/cat.png"), \
                unittest.mock.patch.object(sharing.remote, "ssh", return_value=(0, PNG + b"cat")):
            reply = sharing.send_file("~/Pictures/cat.png")
        self.assertIn("![cat.png](", reply)

    def test_private_files_are_never_sent(self):
        for real in ("/home/ai/.ssh/id_ed25519", "/home/ai/.config/chromium/Default/Cookies", "/home/ai/repo/.env",
                     "/home/ai/.local/share/keyrings/login.keyring", "/home/ai/notes/passwords.txt"):
            with self.desktop(real), unittest.mock.patch.object(sharing.remote, "ssh") as ssh:
                self.assertIn("private", sharing.send_file("~/innocent-link.png"), real)
            ssh.assert_not_called()

    def test_a_file_too_big_is_refused(self):
        with self.desktop("/home/ai/big.mkv", str(media.MAX_UPLOAD_BYTES + 1)), \
                unittest.mock.patch.object(sharing.remote, "ssh") as ssh:
            self.assertIn("too big", sharing.send_file("~/big.mkv"))
        ssh.assert_not_called()

    def test_a_missing_file_is_reported(self):
        with self.desktop(""):
            self.assertIn("no file", sharing.send_file("~/nope.png"))

    def test_a_missing_path_is_asked_for(self):
        self.assertIn("Say which file", sharing.send_file(""))


class PictureSize(unittest.TestCase):
    def test_small_pictures_videos_and_gifs_are_left_alone(self):
        big = b"x" * (sharing.MAX_PICTURE_BYTES + 1)
        self.assertEqual(sharing.phone_sized(b"small", "image/png", "a.png"), (b"small", "image/png", "a.png"))
        self.assertEqual(sharing.phone_sized(big, "video/mp4", "a.mp4")[1], "video/mp4")
        self.assertEqual(sharing.phone_sized(big, "image/gif", "a.gif")[2], "a.gif")

    def test_big_pictures_are_shrunk_to_a_jpeg(self):
        def magick(command, timeout, what):
            Path(command[-1]).write_bytes(b"small jpeg")
        with unittest.mock.patch.object(sharing.media, "_run", side_effect=magick):
            data, mime, name = sharing.phone_sized(b"x" * (sharing.MAX_PICTURE_BYTES + 1), "image/png", "huge.png")
        self.assertEqual((data, mime, name), (b"small jpeg", "image/jpeg", "huge.jpg"))


class WebPictures(unittest.TestCase):
    def test_private_addresses_are_refused(self):
        for url in ("http://127.0.0.1/a.png", "http://192.168.1.50:8443/x.png", "file:///etc/passwd", "http://localhost/a.png"):
            with self.assertRaises(sharing.ShareError, msg=url):
                sharing.check_public_url(url)

    def test_a_search_sends_the_first_picture_that_downloads(self):
        results = [{"img_src": "https://bad.example/a.jpg", "url": "https://bad.example"},
                   {"img_src": "https://good.example/red-panda.jpg", "url": "https://good.example/page", "title": "Red panda"}]
        search = FakeResponse(json.dumps({"results": results}).encode(), "application/json")

        def fetch(url):
            if "bad" in url:
                raise sharing.ShareError("404")
            return b"\xff\xd8jpeg", "image/jpeg"

        with unittest.mock.patch.object(sharing.urllib.request, "urlopen", return_value=search), \
                unittest.mock.patch.object(sharing, "fetch_media", side_effect=fetch):
            reply = sharing.send_picture(query="red panda")
        self.assertIn("![red-panda.jpg](", reply)
        self.assertIn("From: https://good.example/page", reply)

    def test_only_pictures_and_videos_are_fetched(self):
        page = FakeResponse(b"<html>", "text/html")
        with unittest.mock.patch.object(sharing, "is_public_host", return_value=True), \
                unittest.mock.patch.object(sharing._opener, "open", return_value=page):
            with self.assertRaises(sharing.ShareError):
                sharing.fetch_media("https://example.com/page")

    def test_nothing_to_find_is_asked_for(self):
        self.assertIn("Say what picture", sharing.send_picture())


if __name__ == "__main__":
    unittest.main()
