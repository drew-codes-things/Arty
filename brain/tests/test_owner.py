import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-owner-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import owner  # noqa: E402


def config_with(**settings) -> str:
    path = Path(tempfile.mkdtemp(prefix="arty-owner-config-")) / "brain.json"
    path.write_text(json.dumps(settings))
    return str(path)


class ConfiguredName(unittest.TestCase):
    def test_the_name_comes_from_user_name_in_brain_json(self):
        self.assertEqual(owner.configured_name(config_with(user_name=" Sam ")), "Sam")

    def test_a_blank_or_missing_name_or_config_falls_back_to_the_user(self):
        self.assertEqual(owner.configured_name(config_with(user_name="")), "the user")
        self.assertEqual(owner.configured_name(config_with()), "the user")
        self.assertEqual(owner.configured_name("/nonexistent/brain.json"), "the user")

    def test_a_broken_config_fails_loudly(self):
        path = Path(tempfile.mkdtemp()) / "brain.json"
        path.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            owner.configured_name(str(path))


class NameInPrompts(unittest.TestCase):
    def test_prompts_name_the_configured_owner(self):
        self.assertEqual(owner.fill("OWNER said hi; OWNER's dog", "Sam"), "Sam said hi; Sam's dog")
        self.assertNotIn(owner.OWNER_TOKEN, brain.EXTRACT_PROMPT)
        self.assertIn(f"{brain.USER_NAME} said:", brain.EXTRACT_PROMPT)
        self.assertNotIn("Drew", brain.EXTRACT_PROMPT.replace(brain.USER_NAME, ""))

    def test_greetings_by_the_configured_name_are_tidied(self):
        greeting, tail = owner.vocative_patterns("Sam")
        self.assertEqual(tail.sub("", greeting.sub(lambda m: m.group(1), "Goodnight Sam! All done, Sam.")), "Goodnight! All done.")
        self.assertEqual(greeting.sub(lambda m: m.group(1), "Samantha says hi"), "Samantha says hi")

    def test_a_name_with_regex_characters_is_matched_literally(self):
        greeting, _ = owner.vocative_patterns("A.J.")
        self.assertEqual(greeting.sub(lambda m: m.group(1), "Thanks AxJx"), "Thanks AxJx")


if __name__ == "__main__":
    unittest.main()
