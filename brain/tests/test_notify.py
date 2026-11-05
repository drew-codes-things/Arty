"""Where notifications go: Updates in Arty's own apps, never Telegram or Discord.
Run from brain/: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify  # noqa: E402


class Notify(unittest.TestCase):
    def test_every_notification_goes_to_the_apps_feed(self):
        received = []
        with mock.patch.object(notify, "listeners", [lambda *item: received.append(item)]):
            result = notify.send("⏰ Reminder", "Stretch.", urgent=True)
        self.assertEqual(received, [("⏰ Reminder", "Stretch.", [], True)])
        self.assertEqual(result, "sent to Updates in the phone and desktop apps")

    def test_nothing_is_sent_anywhere_else(self):
        self.assertFalse(any(hasattr(notify, name) for name in ("TELEGRAM_TOKEN", "DISCORD_WEBHOOK", "_post")))

    def test_a_broken_feed_is_reported_not_raised(self):
        def broken(*item):
            raise RuntimeError("database locked")
        with mock.patch.object(notify, "listeners", [broken]):
            self.assertTrue(notify.send("Arty", "hi").startswith("notification failed"))


if __name__ == "__main__":
    unittest.main()
