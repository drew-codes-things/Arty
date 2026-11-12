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
        room = unittest.mock.patch.object(brain.models, "make_room", return_value=[])
        room.start()
        self.addCleanup(room.stop)

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

    def test_look_asks_the_vision_model_to_answer_without_thinking(self):
        with self.fake_http({"message": {"content": "an invoice"}}):
            brain.look(["aW1n"], "what is it?")
        self.assertIs(self.calls[0][1]["think"], False)

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

    def direct_vision(self):
        handler = object.__new__(brain.Handler)
        handler.send_json = unittest.mock.Mock()
        picture = {"role": "user", "content": [{"type": "text", "text": "what is it?"},
                                               {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}}]}
        handler.vision({}, [picture])
        return handler

    def test_a_direct_picture_keeps_the_vision_model_loaded_for_the_next_one(self):
        with self.fake_http({"message": {"content": "a cat"}}), \
                unittest.mock.patch.object(brain, "main_model_return"):
            self.direct_vision()
        self.assertEqual(self.calls[0][1]["keep_alive"], brain.VISION_KEEP_ALIVE)

    def test_back_to_back_direct_pictures_bring_the_main_model_back_once_after_a_pause(self):
        with self.fake_http({"message": {"content": "a cat"}}), \
                unittest.mock.patch.object(brain, "reload_main_model") as reload, \
                unittest.mock.patch.object(brain, "main_model_return") as main_return:
            self.direct_vision()
            self.direct_vision()
        reload.assert_not_called()
        self.assertEqual(main_return.schedule.call_args_list,
                         [unittest.mock.call(CONFIG, brain.VISION_MAIN_RETURN_SECONDS)] * 2)
        self.assertLess(brain.VISION_MAIN_RETURN_SECONDS, brain.MAIN_RELOAD_DELAY_SECONDS)

    def test_a_scheduled_return_replaces_the_one_before(self):
        main_return = brain.MainModelReturn()
        with unittest.mock.patch.object(brain, "reload_main_model") as reload:
            main_return.schedule(CONFIG, 0.2)
            main_return.schedule(CONFIG, 0.2)
            main_return.timer.join(2)
        reload.assert_called_once_with(CONFIG)

    def test_the_main_model_gets_room_on_the_card_before_it_loads(self):
        order = []
        with self.fake_http({}), \
                unittest.mock.patch.object(brain.models, "make_room",
                                           lambda name, budget: order.append(("room", name, budget))):
            brain.reload_main_model(CONFIG).join(5)
        order.extend(("load", body["model"], None) for _, body in self.calls)
        self.assertEqual(order, [("room", "qwen3:14b", brain.models.vram_budget(CONFIG)), ("load", "qwen3:14b", None)])


if __name__ == "__main__":
    unittest.main()
