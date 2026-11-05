import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain

CONFIG = {"think": "auto", "model": "qwen3:14b"}


class Thinking(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.object(brain.models, "find", lambda name: {"thinking": True})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plain_requests_and_reminders_skip_thinking(self):
        for request in ("what's the weather tomorrow", "[background task #4] Remind the user: take the bins out",
                        "[background task #13] Weekly homelab report: write the user's weekly homelab report"):
            self.assertFalse(brain.thinking_for(CONFIG, request), request)

    def test_hard_requests_think(self):
        self.assertTrue(brain.thinking_for(CONFIG, "can you work out the best way to split the rent"))

    def test_multi_step_and_build_requests_think(self):
        for request in ("open youtube and then play old town road and screen record it",
                        "can you make an app that orchestrates multiple discord bots to talk to each other",
                        "set up nginx on your desktop", "first find the cheapest flight, then email me the link",
                        "write me a python script that renames my photos"):
            self.assertTrue(brain.thinking_for(CONFIG, request), request)

    def test_simple_requests_still_skip_thinking(self):
        for request in ("play old town road", "make a note: buy milk", "what's on my calendar"):
            self.assertFalse(brain.thinking_for(CONFIG, request), request)

    def test_writing_jobs_think(self):
        self.assertTrue(brain.thinking_for(CONFIG, writing_job=True))

    def test_models_without_thinking_never_think(self):
        with unittest.mock.patch.object(brain.models, "find", lambda name: {"thinking": False}):
            self.assertFalse(brain.thinking_for(CONFIG, "work out the best way to do this"))
