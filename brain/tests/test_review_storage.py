import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_scratch = tempfile.mkdtemp(prefix="arty-review-storage-")
os.environ.setdefault("BRAIN_DB", os.path.join(_scratch, "arty.db"))
os.environ.setdefault("BRAIN_FILES", os.path.join(_scratch, "files"))
os.environ.setdefault("EXPIRY_STATE", os.path.join(_scratch, "expiry.json"))

import expiry  # noqa: E402
import lectures  # noqa: E402
import media  # noqa: E402
import models  # noqa: E402
import personal  # noqa: E402
import sharing  # noqa: E402
import store  # noqa: E402
import undo  # noqa: E402


class Media(unittest.TestCase):
    def test_a_made_up_type_cannot_carry_extra_headers(self):
        meta = media.save_bytes(b"picture " + os.urandom(8), "image.png", "image/png\r\nSet-Cookie: x=1", "chat")
        self.assertEqual(meta["mime"], "image/png")
        self.assertNotIn("\n", media.safe_mime("text/html\nX: y"))

    def test_attaching_the_same_file_again_keeps_it_for_another_day(self):
        data = b"same picture " + os.urandom(8)
        first = media.save_bytes(data, "a.png", "image/png", "chat")
        media._update(first["id"], created=time.time() - media.FILE_TTL_SECONDS + 60)
        again = media.save_bytes(data, "a.png", "image/png", "chat")
        self.assertGreater(again["created"], time.time() - 60)
        self.assertGreater(media.get(first["id"])["created"], time.time() - 60)

    def test_the_same_upload_finishing_twice_at_once_is_not_an_error(self):
        data = b"uploaded twice " + os.urandom(8)
        file_id = __import__("hashlib").sha256(data).hexdigest()[:24]
        real_rename = Path.rename

        def rename_after_the_other_upload(path, target):
            if Path(target).name == file_id and not Path(target).exists():
                (media.FILES_DIR / file_id).mkdir(parents=True)
                (media.FILES_DIR / file_id / "data").write_bytes(data)
                media._finish(media.FILES_DIR / file_id, "x.bin", "application/octet-stream", len(data), "upload")
            return real_rename(path, target)

        with mock.patch.object(Path, "rename", rename_after_the_other_upload):
            meta = media.save_stream(__import__("io").BytesIO(data), len(data), "x.bin", "", "upload")
        self.assertEqual(meta["id"], file_id)

    def test_a_non_ascii_download_token_is_just_wrong(self):
        meta = media.save_bytes(b"token test " + os.urandom(8), "t.txt", "text/plain", "chat")
        self.assertIsNone(media.for_download(meta["id"], "té"))

    def test_two_turns_analyse_a_new_file_once(self):
        meta = media.save_bytes(b"text to read " + os.urandom(8), "notes.txt", "text/plain", "chat")
        calls = []
        real_open = open

        def counting_open(path, *args, **kwargs):
            if str(path) == meta["path"]:
                calls.append(path)
                time.sleep(0.2)
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", counting_open):
            workers = [threading.Thread(target=media.analyse, args=(dict(meta), "", None)) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
        self.assertEqual(len(calls), 1)

    def test_scripted_image_formats_are_refused(self):
        svg = media.save_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///etc/passwd"/></svg>',
                               "a.png", "image/png", "chat")
        with mock.patch.object(media, "_run") as run, self.assertRaises(media.MediaError):
            media.image_preview(svg["path"])
        run.assert_not_called()

    def test_ffmpeg_only_reads_local_files(self):
        with mock.patch.object(media, "_run") as run:
            run.return_value = mock.Mock(stdout="{}")
            media.probe("/tmp/x.mp4")
        self.assertIn("-protocol_whitelist", run.call_args[0][0])


class Store(unittest.TestCase):
    def setUp(self):
        store.init()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM announcements")

    def test_connections_are_closed(self):
        with store.connect() as connection:
            connection.execute("SELECT 1")
        with self.assertRaises(Exception):
            connection.execute("SELECT 1")

    def test_an_app_far_behind_catches_up_in_order(self):
        after = store.add_announcement("message", "already seen", "")
        for number in range(120):
            store.add_announcement("message", f"n{number}", "")
        seen = []
        while page := store.list_announcements(after):
            seen += [item["title"] for item in page]
            after = page[-1]["id"]
        self.assertEqual(seen, [f"n{number}" for number in range(120)])

    def test_a_new_app_still_gets_the_newest(self):
        for number in range(60):
            store.add_announcement("message", f"n{number}", "")
        self.assertEqual(store.list_announcements(0)[-1]["title"], "n59")


class Lectures(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        lectures.init()
        with store.write_lock, store.connect() as connection:
            for table in ("cards", "notes", "lectures"):
                connection.execute(f"DELETE FROM {table}")
        self.recording = media.save_bytes(b"lesson " + os.urandom(8), "lesson.m4a", "audio/mp4", "test")

    def model(self, prompt, json_mode=False):
        if json_mode:
            return '{"cards": [{"question": "Is UDP reliable?", "answer": "No"}]}'
        return "Notes " + "x" * 6000

    def run_lecture(self):
        lecture = lectures.add(self.recording["id"], "Networking")
        with mock.patch.object(media, "probe", return_value={"duration": 60.0}), \
                mock.patch.object(media, "transcribe", return_value="[00:00] TCP and UDP"):
            return lecture, lectures.process(lectures.get(lecture["id"]), self.model)

    def test_long_notes_are_saved_whole(self):
        self.run_lecture()
        with store.connect() as connection:
            longest = connection.execute("SELECT MAX(LENGTH(text)) FROM notes WHERE text LIKE '%Networking%'").fetchone()[0]
        self.assertGreater(longest, 6000)
        self.assertIn("the full note is", personal.note_search("lecture networking"))

    def test_running_a_lecture_again_after_a_restart_does_not_duplicate_cards_or_notes(self):
        lecture, _ = self.run_lecture()
        with mock.patch.object(media, "probe", return_value={"duration": 60.0}), \
                mock.patch.object(media, "transcribe", return_value="[00:00] TCP and UDP"):
            lectures.process(lectures.get(lecture["id"]), self.model)
        with store.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cards WHERE question = 'Is UDP reliable?'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)

    def test_a_queued_lecture_keeps_its_recording(self):
        lectures.add(self.recording["id"], "Waiting")
        media._update(self.recording["id"], created=time.time() - media.FILE_TTL_SECONDS + 60)
        lectures.keep_queued_recordings()
        self.assertGreater(media.get(self.recording["id"])["created"], time.time() - 60)

    def test_a_database_error_does_not_end_the_worker(self):
        self.assertIsNone(lectures.next_queued(lambda: (_ for _ in ()).throw(__import__("sqlite3").OperationalError("locked"))))


class Expiry(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(expiry, "STATE_FILE", Path(_scratch) / "expiry-warned.json")
        patch.start()
        self.addCleanup(patch.stop)

    def test_an_expired_certificate_is_reported(self):
        error = ssl.SSLCertVerificationError("certificate verify failed: certificate has expired")
        error.verify_code, error.verify_message = 10, "certificate has expired"
        expiry._last_check["time"] = 0
        with mock.patch.object(expiry, "certificate_expiry", side_effect=expiry.CertificateProblem("it has expired")):
            messages = expiry.due_warnings({"certificates": ["old.example"]})
        self.assertEqual(len(messages), 1)
        self.assertIn("expired", messages[0])
        expiry._last_check["time"] = 0
        with mock.patch.object(expiry, "certificate_expiry", side_effect=expiry.CertificateProblem("it has expired")):
            self.assertEqual(expiry.due_warnings({"certificates": ["old.example"]}), [])

    def test_a_verification_failure_becomes_a_certificate_problem(self):
        error = ssl.SSLCertVerificationError("certificate verify failed")
        error.verify_code, error.verify_message = 10, "certificate has expired"
        with mock.patch.object(socket, "create_connection"), \
                mock.patch.object(ssl.SSLContext, "wrap_socket", side_effect=error):
            with self.assertRaises(expiry.CertificateProblem):
                expiry.certificate_expiry("old.example")


class Undo(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        with store.write_lock, store.connect() as connection:
            for table in ("schedules", "events", "list_items", "notes", "deadlines", "cards"):
                connection.execute(f"DELETE FROM {table}")

    def test_only_things_made_in_the_turn_being_undone_are_removed(self):
        personal.note_save("made long before the last turn")
        with store.write_lock, store.connect() as connection:
            connection.execute("UPDATE notes SET created = ?", (time.time() - 3600,))
        now = time.time()
        reply = undo.undo_last(now, made_between=(now - 30, now))
        self.assertIn("nothing", reply.lower())
        self.assertIn("made long before", personal.note_search(""))


class Sharing(unittest.TestCase):
    def test_a_spreadsheet_title_with_symbols_still_works(self):
        sent = []
        with mock.patch.object(sharing, "_desktop", lambda command, stdin=None: sent.append(stdin)):
            sharing._build_document("| a | b |\n|---|---|\n| 1 | 2 |", "Q3: costs [draft] / v2?", "xlsx", "~/x.xlsx")
        sheet = json.loads(sent[-1])["sheet"]
        self.assertFalse(set(sheet) & set("/\\?*[]:"))
        self.assertTrue(sheet)

    def test_documents_are_made_without_raw_html_or_scripts(self):
        commands = []
        with mock.patch.object(sharing, "_desktop", lambda command, stdin=None: commands.append(command)), \
                mock.patch.object(sharing.remote, "text", return_value=(0, "")):
            sharing._build_document("# Hi\n<script>x</script>", "Hi", "pdf", "~/x.pdf")
            sharing._build_document("# Hi", "Hi", "docx", "~/x.docx")
        pandoc = [command for command in commands if "pandoc" in command]
        self.assertEqual(len(pandoc), 2)
        self.assertTrue(all("markdown-raw_html-raw_attribute" in command and "--lua-filter=" in command
                            for command in pandoc))

    def test_debian_pandoc_can_still_read_its_templates(self):
        commands = []
        with mock.patch.object(sharing, "_desktop", lambda command, stdin=None: commands.append(command)), \
                mock.patch.object(sharing.remote, "text", return_value=(0, "")):
            for extension in ("docx", "pptx", "epub", "pdf"):
                sharing._build_document("# Hi", "Hi", extension, f"~/x.{extension}")
        self.assertFalse([command for command in commands if "--sandbox" in command])

    def test_chrome_prints_with_scripts_blocked_by_the_page_not_a_flag(self):
        commands = []
        with mock.patch.object(sharing, "_desktop", lambda command, stdin=None: commands.append(command)), \
                mock.patch.object(sharing.remote, "text", return_value=(0, "")):
            sharing._build_document("# Hi", "Hi", "pdf", "~/x.pdf")
        printing = next(command for command in commands if "google-chrome" in command)
        self.assertNotIn("scriptEnabled", printing)
        self.assertIn("script-src 'none'", sharing.PDF_STYLE)

    def test_only_web_images_are_kept_in_documents(self):
        self.assertIn('image.src:match("^https?://")', sharing.WEB_IMAGES_ONLY)
        self.assertIn("return {}", sharing.WEB_IMAGES_ONLY)

    def test_the_address_checked_is_the_address_connected_to(self):
        lookups = iter([[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]])
        with mock.patch.object(socket, "getaddrinfo", lambda *a, **k: next(lookups)), \
                mock.patch.object(socket, "create_connection") as connect:
            connection = sharing.PinnedHTTPConnection("rebind.example", 80, timeout=5)
            connection.connect()
        self.assertEqual(connect.call_args[0][0][0], "93.184.216.34")


class ModelCache(unittest.TestCase):
    def test_a_slow_refresh_does_not_hold_up_readers_of_the_cache(self):
        models._cache.update(at=time.time() - models.CACHE_SECONDS - 1, models=[{"name": "cached"}])
        started = threading.Event()

        def slow(path, body=None, timeout=30):
            started.set()
            time.sleep(1)
            return {"models": []}

        with mock.patch.object(models, "_ollama", slow):
            refresher = threading.Thread(target=models.installed)
            refresher.start()
            started.wait(2)
            began = time.time()
            self.assertEqual(models.installed(), [{"name": "cached"}])
            self.assertLess(time.time() - began, 0.5)
            refresher.join()


class Deadlines(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM deadlines")

    def test_nothing_is_written_when_no_reminder_is_due(self):
        personal.deadline_add("Essay", "2099-01-01 17:00")
        statements = []
        real_connect = store.connect

        def tracing_connect():
            connection = real_connect()
            connection.set_trace_callback(statements.append)
            return connection

        with mock.patch.object(store, "connect", tracing_connect):
            personal.due_deadline_reminders()
        self.assertFalse([sql for sql in statements if sql.lstrip().upper().startswith("UPDATE")])


if __name__ == "__main__":
    unittest.main()
