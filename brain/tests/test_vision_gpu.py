import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain

CONFIG = {"model": "qwen3:14b", "keep_alive": -1, "options": {"num_ctx": 12288}}
VISION = {"name": "qwen2.5vl:7b", "vision": True}


class VisionGpu(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for name, value in (("load_config", lambda: CONFIG), ("vision_model", lambda wanted="": VISION)):
            patcher = unittest.mock.patch.object(brain, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_http(self, reply=None, error=None):
        def http_json(url, body=None, headers=None, timeout=600):
            self.calls.append((url, body))
            if error:
                raise error
            return reply
        return unittest.mock.patch.object(brain, "http_json", http_json)

    def test_look_unloads_the_vision_model_straight_away(self):
        with self.fake_http({"message": {"content": "a cat"}}):
            self.assertEqual(brain.look(["aW1n"], "what is it?"), "a cat")
        self.assertEqual(self.calls[0][1]["model"], "qwen2.5vl:7b")
        self.assertEqual(self.calls[0][1]["keep_alive"], 0)

    def test_reload_main_model_loads_the_brain_model_with_its_keep_alive(self):
        with self.fake_http({}):
            brain.reload_main_model(CONFIG).join(5)
        url, body = self.calls[0]
        self.assertTrue(url.endswith("/api/generate"))
        self.assertEqual(body, {"model": "qwen3:14b", "keep_alive": -1})

    def test_reload_failure_is_logged_not_raised(self):
        with self.fake_http(error=urllib.error.URLError("down")), self.assertLogs("brain", "WARNING") as logs:
            brain.reload_main_model(CONFIG).join(5)
        self.assertIn("qwen3:14b", logs.output[0])


if __name__ == "__main__":
    unittest.main()
