import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import connections  # noqa: E402
import policy  # noqa: E402

SHIPPED_RULES = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())["approval"]
OWN_APPS = {"drive": "https://drive.example.org", "calendar": "https://calendar.example.org",
            "starfire": "https://starfire.example.org"}


class OwnAppHostsAreSensitive(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp(prefix="own-app-hosts-"))
        self.addCleanup(lambda: [path.unlink() for path in folder.iterdir()] and folder.rmdir())
        prompt = folder / "system-prompt.md"
        prompt.write_text("prompt")
        self.config_file = folder / "brain.json"
        self.config_file.write_text(json.dumps({"system_prompt_file": str(prompt),
                                                "approval": SHIPPED_RULES}))
        self.listed = folder / "own-apps.json"
        self.listed.write_text(json.dumps(OWN_APPS))

    def loaded(self, starfire_url=""):
        with unittest.mock.patch.object(connections, "CONFIG_FILE", str(self.config_file)), \
                unittest.mock.patch.object(connections, "OWN_APPS_FILE", str(self.listed)), \
                unittest.mock.patch.object(connections, "STARFIRE_URL", starfire_url):
            return connections.load_config()

    def test_every_listed_own_app_host_joins_the_sensitive_domains(self):
        domains = self.loaded()["approval"]["domains"]
        self.assertEqual(domains, SHIPPED_RULES["domains"] + ["drive.example.org", "calendar.example.org", "starfire.example.org"])

    def test_the_starfire_address_the_brain_knows_counts_too(self):
        self.listed.write_text(json.dumps({"drive": "https://drive.example.org", "calendar": "", "starfire": ""}))
        domains = self.loaded("https://Chat.Example.org/")["approval"]["domains"]
        self.assertEqual(domains, SHIPPED_RULES["domains"] + ["drive.example.org", "chat.example.org"])

    def test_browser_steps_on_an_own_app_ask_first(self):
        rules = self.loaded()["approval"]
        on_drive = {"url": "https://drive.example.org/files/0", "elements": {"7": "Delete forever"}}
        self.assertIsNotNone(policy.approval_reason("workspace__browser_click", {"target": "Share"}, on_drive, rules))
        self.assertIsNotNone(policy.approval_reason("workspace__browser_click", {"target": "7"}, on_drive, rules))
        self.assertIsNotNone(policy.approval_reason("workspace__browser_press", {"keys": "Enter"}, on_drive, rules))
        self.assertIsNotNone(policy.approval_reason("workspace__browser_download", {"target": "plan.pdf"}, on_drive, rules))
        self.assertIsNotNone(policy.approval_reason("workspace__browser_type", {"target": "Name", "text": "x", "submit": True},
                                                    on_drive, rules))

    def test_a_missing_or_broken_list_leaves_the_domains_as_they_are(self):
        self.listed.unlink()
        self.assertEqual(self.loaded()["approval"]["domains"], SHIPPED_RULES["domains"])
        self.listed.write_text("{broken")
        with self.assertLogs("brain", "WARNING"):
            self.assertEqual(self.loaded()["approval"]["domains"], SHIPPED_RULES["domains"])


if __name__ == "__main__":
    unittest.main()
