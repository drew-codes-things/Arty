import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_any_model import downloaded  # noqa: E402
from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import connections  # noqa: E402
import models  # noqa: E402

SHIPPED = Path(__file__).resolve().parents[1] / "brain.json"
MIMO = "mimo-v2.6:9b"
QWEN = "qwen3:14b"
CHAT_WINDOW = 24576
MIMO_WINDOW = 49152


def windows(enabled=True, **sizes):
    return {"model": QWEN, "options": {"num_ctx": CHAT_WINDOW},
            "model_windows": {"enabled": enabled, "num_ctx": sizes or {MIMO: MIMO_WINDOW}}}


class PerModelWindow(unittest.TestCase):
    def test_the_shipped_brain_has_it_ready_but_off(self):
        shipped = json.loads(SHIPPED.read_text())
        self.assertIs(shipped["model_windows"]["enabled"], False)
        self.assertEqual(shipped["model_windows"]["num_ctx"], {MIMO: MIMO_WINDOW})
        self.assertEqual(models.context_tokens(shipped, MIMO), shipped["options"]["num_ctx"])

    def test_switched_on_a_listed_model_gets_its_own_window(self):
        self.assertEqual(models.context_tokens(windows(), MIMO), MIMO_WINDOW)
        self.assertEqual(models.context_tokens(windows(), f"{MIMO.split(':')[0]}:latest"), CHAT_WINDOW)
        self.assertEqual(models.context_tokens(windows(), QWEN), CHAT_WINDOW)
        self.assertEqual(models.context_tokens(windows()), CHAT_WINDOW)

    def test_a_latest_tag_finds_the_bare_name(self):
        self.assertEqual(models.context_tokens(windows(coder=32768), "coder:latest"), 32768)

    def test_switched_off_every_model_gets_the_chat_window(self):
        self.assertEqual(models.context_tokens(windows(enabled=False), MIMO), CHAT_WINDOW)

    def test_a_bad_size_falls_back_to_the_chat_window(self):
        for size in ("lots", 0, -5, True, 1.5):
            with self.subTest(size=size):
                self.assertEqual(models.context_tokens(windows(**{"m:1b": size}), "m:1b"), CHAT_WINDOW)

    def test_every_call_to_a_model_loads_it_at_that_models_window(self):
        self.assertEqual(models.for_runner(windows(), {"model": MIMO})["options"]["num_ctx"], MIMO_WINDOW)
        self.assertEqual(models.for_runner(windows(), {"model": QWEN})["options"]["num_ctx"], CHAT_WINDOW)

    def test_the_overflow_error_names_the_models_own_window(self):
        overflow = brain.WindowOverflow(MIMO, models.context_tokens(windows(), MIMO))
        self.assertEqual(overflow.details()["context_tokens"], MIMO_WINDOW)
        self.assertIn(str(MIMO_WINDOW), str(overflow))


class PerModelWindowInTheList(Server):
    def setUp(self):
        super().setUp()
        for patch in downloaded():
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def listed_windows(self, config):
        with unittest.mock.patch.object(connections, "load_config", return_value=config):
            status, body = self.request("GET", "/v1/models?all=1")
        self.assertEqual(status, 200)
        return {entry["model"]: entry["context_tokens"] for entry in json.loads(body)["data"]}

    def test_each_model_reports_the_window_it_will_get(self):
        self.assertEqual(self.listed_windows(windows(**{"qwen2.5:72b": 8192})), {QWEN: CHAT_WINDOW, "qwen2.5:72b": 8192})

    def test_switched_off_all_models_report_the_chat_window(self):
        listed = self.listed_windows(windows(enabled=False, **{"qwen2.5:72b": 8192}))
        self.assertEqual(set(listed.values()), {CHAT_WINDOW})


if __name__ == "__main__":
    unittest.main()
