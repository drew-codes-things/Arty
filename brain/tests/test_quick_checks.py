import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402

THINKING_MODEL = {"name": "qwen3:14b", "thinking": True}


class QuickChecks(unittest.TestCase):
    def sent_body(self, call):
        with unittest.mock.patch.object(brain, "load_config", return_value={"model": "qwen3:14b", "think": "auto"}), \
                unittest.mock.patch.object(brain.models, "find", return_value=THINKING_MODEL), \
                unittest.mock.patch.object(brain, "http_json", return_value={"message": {"content": "FINE"}}) as sent:
            call()
        return sent.call_args.kwargs["body"]

    def test_a_quick_check_never_thinks(self):
        self.assertIs(self.sent_body(lambda: brain.classify_text("Reply with FINE or MISSING."))["think"], False)

    def test_a_writing_job_still_thinks(self):
        self.assertIs(self.sent_body(lambda: brain.complete_text("Write the answer."))["think"], True)

    def test_web_research_hands_its_checks_to_the_quick_call(self):
        with unittest.mock.patch.object(brain.research, "run", return_value=("answer", [])) as run:
            brain.researched_answer("search the web for the latest debian release", quick=True)
        self.assertIs(run.call_args.kwargs["classify"], brain.classify_text)
        self.assertIs(run.call_args.args[2], brain.complete_text)


if __name__ == "__main__":
    unittest.main()
