import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision.sh"
CREATES_ALERT_TOKEN = "set_secret brain.env ALERT_TOKEN"
READS_ALERT_TOKEN = "sed -n 's/^ALERT_TOKEN=//p'"


class ProvisionOrderTest(unittest.TestCase):
    def test_the_alert_token_is_created_before_the_security_watch_reads_it(self):
        text = SCRIPT.read_text()
        self.assertIn(CREATES_ALERT_TOKEN, text)
        self.assertIn(READS_ALERT_TOKEN, text)
        self.assertLess(text.index(CREATES_ALERT_TOKEN), text.index(READS_ALERT_TOKEN))


if __name__ == "__main__":
    unittest.main()
