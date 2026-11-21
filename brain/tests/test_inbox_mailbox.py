import os
import sys
import tempfile
import time
import unittest
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-inbox-mailbox-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("APP_ACTIONS_STATE", "app-actions.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import app_actions  # noqa: E402
import inbox_tidy  # noqa: E402
import mail_box  # noqa: E402
from fake_app_servers import FakeImap  # noqa: E402
from job_types import MailboxError  # noqa: E402

GMAIL = b"IMAP4rev1 AUTH=PLAIN X-GM-EXT-1 MOVE"
WITH_MOVE = b"IMAP4rev1 AUTH=PLAIN MOVE"
CHANGING_VERBS = ("STORE", "MOVE", "COPY", "EXPUNGE", "DELETE", "APPEND")


def raw_message(sender: str, subject: str, body: str) -> bytes:
    return (f"From: {sender}\r\nSubject: {subject}\r\nDate: Thu, 19 Nov 2026 09:00:00 +0000\r\n"
            f"Message-ID: <{subject.replace(' ', '')}@example.org>\r\n\r\n{body}\r\n").encode()


INBOX = {
    7: (raw_message("Sam <sam@example.org>", "Dinner on Friday", "Are you free?"), "\\Seen"),
    8: (raw_message("Shop News <news@shop.example>", "Big sale", "New deals. Unsubscribe here."), ""),
    9: (raw_message("Bank <alerts@bank.example>", "Account notice", "Your December statement is ready to view."), ""),
}


class FakeImapCase(unittest.TestCase):
    def serve(self, capabilities: bytes = b"IMAP4rev1 AUTH=PLAIN", uid_validity: int = 1, archive_folder: str = ""):
        self.imap = FakeImap(INBOX, capabilities=capabilities, uid_validity=uid_validity)
        self.addCleanup(self.imap.stop)
        values = {"MAIL_ADDRESS": "drew@example.org", "MAIL_PASSWORD": "app-password", "MAIL_IMAP_HOST": "127.0.0.1",
                  "MAIL_IMAP_PORT": str(self.imap.port), "MAIL_IMAP_SECURITY": "none", "MAIL_SMTP_HOST": "127.0.0.1",
                  "MAIL_SMTP_PORT": "1", "MAIL_SMTP_SECURITY": "none", "MAIL_ARCHIVE_FOLDER": archive_folder}
        return mail_box.TidyMailbox(load=lambda: mail_box.load_settings(values))

    def changing_commands(self) -> list[str]:
        return [command for command in self.imap.commands
                if any(f" {verb} " in f" {command.upper()} " for verb in CHANGING_VERBS)]


class ReadingTheInbox(FakeImapCase):
    def test_messages_come_newest_first_with_numbers_tied_to_the_inbox_numbering(self):
        messages = self.serve(uid_validity=42).inbox(10)
        self.assertEqual(["42.9", "42.8", "42.7"], [message.id for message in messages])
        self.assertEqual("news@shop.example", messages[1].address)
        self.assertIn("Unsubscribe", messages[1].snippet)
        self.assertFalse(messages[2].unread)

    def test_reading_never_marks_anything_read_or_changes_anything(self):
        self.serve().inbox(10)
        commands = " ".join(self.imap.commands).upper()
        self.assertIn("EXAMINE", commands)
        self.assertIn("BODY.PEEK[]", commands)
        self.assertNotIn("SELECT", commands)
        self.assertEqual([], self.changing_commands())

    def test_very_large_emails_are_left_out_of_the_scan(self):
        self.serve().inbox(10)
        self.assertIn(f"UID SEARCH SMALLER {mail_box.LARGEST_SCANNED_BYTES}", self.imap.commands)

    def test_the_tidy_suggests_nothing_for_people_or_money_mail(self):
        changes = inbox_tidy.suggest(self.serve().inbox(10))
        self.assertEqual({"1.8"}, {change.message_id for change in changes})


class ChangingTheInbox(FakeImapCase):
    def test_a_label_is_a_keyword_on_an_ordinary_server(self):
        self.serve().add_label("1.8", "Newsletters")
        self.assertEqual(["UID STORE 8 +FLAGS.SILENT (Newsletters)"], self.changing_commands())

    def test_a_label_is_a_gmail_label_on_gmail(self):
        self.serve(GMAIL).add_label("1.8", "Shop mail")
        self.assertEqual(['UID STORE 8 +X-GM-LABELS ("Shop mail")'], self.changing_commands())

    def test_archive_on_gmail_takes_the_inbox_label_off(self):
        self.serve(GMAIL).archive("1.8")
        self.assertEqual(["UID STORE 8 -X-GM-LABELS (\\Inbox)"], self.changing_commands())

    def test_archive_elsewhere_moves_the_email_to_the_archive_folder(self):
        self.serve(WITH_MOVE).archive("1.8")
        self.assertEqual(['UID MOVE 8 "Archive"'], self.changing_commands())

    def test_archive_uses_the_folder_drew_set(self):
        self.serve(GMAIL, archive_folder="[Gmail]/All Mail").archive("1.8")
        self.assertEqual(['UID MOVE 8 "[Gmail]/All Mail"'], self.changing_commands())

    def test_a_server_that_cannot_move_mail_gets_nothing_deleted_instead(self):
        with self.assertRaises(MailboxError):
            self.serve().archive("1.8")
        self.assertEqual([], self.changing_commands())

    def test_a_renumbered_inbox_changes_nothing(self):
        with self.assertRaises(MailboxError) as raised:
            self.serve(WITH_MOVE, uid_validity=2).archive("1.8")
        self.assertIn("renumbered", str(raised.exception))
        self.assertEqual([], self.changing_commands())

    def test_odd_numbers_and_labels_are_refused_before_the_server_is_asked(self):
        mailbox = self.serve(WITH_MOVE)
        for message_id in ("8", "1.8 +FLAGS (\\Deleted)", "1.8)", ""):
            with self.assertRaises(MailboxError):
                mailbox.archive(message_id)
        for label in ('News") (\\Deleted', "Café", "", "x" * 41):
            with self.assertRaises(MailboxError):
                mailbox.add_label("1.8", label)
        self.assertEqual([], self.imap.commands)

    def test_an_allowed_card_applies_through_the_real_mail_server(self):
        mailbox = self.serve(WITH_MOVE)
        changes = [change.as_arguments() for change in inbox_tidy.suggest(mailbox.inbox(10))]
        self.assertEqual("Applied 2 of 2 inbox changes.", inbox_tidy.apply_approved(mailbox, {"changes": changes}))
        self.assertEqual(["UID STORE 8 +FLAGS.SILENT (Newsletters)", 'UID MOVE 8 "Archive"'], self.changing_commands())


HOSTILE_OPENINGS = 120_000
QUICK_SECONDS = 1.5


def html_only_message(html: str) -> bytes:
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = "someone@outside.example", "drew@example.org", "hello"
    message.set_content(html, subtype="html")
    return message.as_bytes()


class HostileHtml(FakeImapCase):
    def serve_one(self, html: str) -> mail_box.TidyMailbox:
        mailbox = self.serve()
        self.imap.messages = {5: (html_only_message(html), "")}
        return mailbox

    def timed(self, read) -> tuple[float, object]:
        started = time.perf_counter()
        found = read()
        return time.perf_counter() - started, found

    def test_a_flood_of_tag_openings_is_cleaned_quickly_by_the_tidy_and_by_email_read(self):
        mailbox = self.serve_one("<" * HOSTILE_OPENINGS)
        took, found = self.timed(lambda: mailbox.inbox(10))
        self.assertLess(took, QUICK_SECONDS)
        self.assertEqual(["1.5"], [message.id for message in found])
        took, item = self.timed(lambda: mail_box.message(mailbox._settings(), "5"))
        self.assertLess(took, QUICK_SECONDS)
        self.assertLessEqual(len(item["body"]), mail_box.LARGEST_BODY_CHARS)

    def test_ordinary_html_still_reads_as_plain_text(self):
        mailbox = self.serve_one("<p>Hello <b>there</b> 3 < 4</p><a href='x'>link</a>")
        self.assertEqual("Hello there 3 < 4link", mail_box.message(mailbox._settings(), "5")["body"])


class Connection(unittest.TestCase):
    def test_no_settings_is_a_plain_mailbox_error(self):
        with self.assertRaises(MailboxError) as raised:
            mail_box.TidyMailbox(load=lambda: None).inbox(10)
        self.assertIn("set up", str(raised.exception))

    def test_a_bad_archive_folder_setting_is_refused(self):
        with self.assertRaises(mail_box.MailError):
            mail_box.load_settings({"MAIL_ADDRESS": "drew@example.org", "MAIL_IMAP_HOST": "h", "MAIL_SMTP_HOST": "h",
                                    "MAIL_ARCHIVE_FOLDER": 'Archive" (\\Deleted'})

    def test_a_trailing_line_break_does_not_slip_past_the_mailbox_checks(self):
        settings = {"MAIL_ADDRESS": "drew@example.org", "MAIL_IMAP_HOST": "h", "MAIL_SMTP_HOST": "h"}
        with self.assertRaises(mail_box.MailError):
            mail_box.load_settings({**settings, "MAIL_ARCHIVE_FOLDER": "Archive\n"})
        self.assertEqual(mail_box.load_settings({**settings, "MAIL_ARCHIVE_FOLDER": "Archive"}).archive_folder, "Archive")
        with self.assertRaises(MailboxError):
            mail_box._tidy_id_parts("12.34\n")
        self.assertEqual(mail_box._tidy_id_parts("12.34"), ("12", "34"))
        with self.assertRaises(MailboxError):
            mail_box.TidyMailbox(load=lambda: None).add_label("12.34", "Receipts\n")

    def test_the_brain_connects_the_mailbox_at_start(self):
        import brain
        self.addCleanup(inbox_tidy.connect, inbox_tidy.connected())
        inbox_tidy.connect(None)
        brain.connect_inbox_tidy()
        self.assertIsInstance(inbox_tidy.connected(), mail_box.TidyMailbox)

    def test_inbox_tidy_tools_are_offered_only_once_email_is_set_up(self):
        for name in ("set_inbox_tidy", "always_allow_inbox_change"):
            self.assertFalse(app_actions.is_offered(name, set()))
            self.assertTrue(app_actions.is_offered(name, {"email"}))
        self.assertTrue(app_actions.is_offered("list_jobs", set()))


if __name__ == "__main__":
    unittest.main()
