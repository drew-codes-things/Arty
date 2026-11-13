import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-honest-code-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import connections  # noqa: E402

CODE = ("```python\ndef parse_duration(text: str) -> int:\n    if not text:\n        raise ValueError(text)\n"
        "    print(\"I saved the file\")\n    return 0\n```")
SANDBOX_LINE = "Sandbox check: the Python ran without errors (Python 3.13)."
ANSWER = f"Here's the function:\n\n{CODE}\n\nIt turns '1h30m' into seconds.\n\n{SANDBOX_LINE}"
REQUEST = "Write a Python function parse_duration(text: str) -> int."


class HonestCodeAnswers(unittest.TestCase):
    def setUp(self):
        config = unittest.mock.patch.object(connections, "load_config", return_value={"model": "qwen3:14b"})
        config.start()
        self.addCleanup(config.stop)

    def checked(self, answer, claims):
        def judge(url, body):
            prompt = body["messages"][0]["content"]
            verdict = {"kind": "A"} if prompt.startswith("The person said") else {"false_claims": claims}
            return {"message": {"content": json.dumps(verdict)}}
        with unittest.mock.patch.object(connections, "http_json", side_effect=judge):
            return brain.be_honest(answer, ["code_sandbox"], [], request=REQUEST)

    def test_the_sandbox_line_is_never_taken_for_a_false_claim(self):
        claim = {"text": SANDBOX_LINE, "correction": "The code was not actually run in a sandbox."}
        self.assertEqual(ANSWER, self.checked(ANSWER, [claim]))

    def test_code_keeps_its_indentation_when_a_claim_is_corrected(self):
        answer = ANSWER.replace("It turns", "I saved it to ~/parse.py.  It turns")
        reply = self.checked(answer, [{"text": "I saved it to ~/parse.py.", "correction": "I didn't save it anywhere."}])
        self.assertIn("I didn't save it anywhere.", reply)
        self.assertNotIn("~/parse.py", reply)
        self.assertIn(CODE, reply)
        self.assertTrue(reply.endswith(SANDBOX_LINE))

    def test_a_line_inside_the_code_is_never_rewritten(self):
        reply = self.checked(ANSWER, [{"text": "print(\"I saved the file\")", "correction": ""}])
        self.assertEqual(ANSWER, reply)


if __name__ == "__main__":
    unittest.main()
