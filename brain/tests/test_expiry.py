"""Expiry watch: one warning per warning point, afresh after a renewal.
Run from brain/: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from expiry import DAY_SECONDS, warning_for  # noqa: E402

NOW = 1_800_000_000.0


class Warnings(unittest.TestCase):
    def test_nothing_while_it_is_far_off(self):
        self.assertIsNone(warning_for("The domain x", NOW + 90 * DAY_SECONDS, NOW, {}))

    def test_each_warning_point_is_sent_once(self):
        warned = {}
        expires = NOW + 29 * DAY_SECONDS
        self.assertIn("29 days left", warning_for("The domain x", expires, NOW, warned))
        self.assertIsNone(warning_for("The domain x", expires, NOW + 3600, warned))
        self.assertIn("13 days left", warning_for("The domain x", expires, NOW + 16 * DAY_SECONDS, warned))

    def test_a_renewal_starts_the_warnings_again(self):
        warned = {}
        warning_for("The certificate for x", NOW + 5 * DAY_SECONDS, NOW, warned)
        self.assertIsNotNone(warning_for("The certificate for x", NOW + 6 * DAY_SECONDS, NOW + DAY_SECONDS, warned))

    def test_already_expired_says_so(self):
        self.assertIn("expired on", warning_for("The domain x", NOW - DAY_SECONDS, NOW, {}))


if __name__ == "__main__":
    unittest.main()
