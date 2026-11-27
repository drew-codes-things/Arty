import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_brain import Server  # noqa: E402

import agent_loop  # noqa: E402
import attachments  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import honesty  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402

MAIN = "hf.co/owner/big-a3b-gguf:iq4_xs"
SEEING_MAIN = {"name": MAIN, "billions": 34.7, "tools": True, "vision": True, "embedding": False, "thinking": True}
BLIND_MAIN = {**SEEING_MAIN, "vision": False}
CONFIG = {"model": MAIN, "one_model": True, "coding_model": "coder:9b", "vision_model": "looker:9b",
          "model_strengths": {"coder:9b": "code"}, "keep_alive": -1, "options": {"num_ctx": 24576, "num_predict": 2048}}


class OneModelKeepsTheMainLoaded(unittest.TestCase):
    def test_coding_turns_and_hand_offs_stay_on_the_main(self):
        with unittest.mock.patch.object(models, "find", return_value=BLIND_MAIN):
            kept = models.with_one_model(dict(CONFIG))
        self.assertEqual(kept["coding_model"], MAIN)
        self.assertEqual(kept["model_strengths"], {})

    def test_pictures_go_to_the_main_once_it_can_see(self):
        with unittest.mock.patch.object(models, "find", return_value=SEEING_MAIN):
            self.assertEqual(models.with_one_model(dict(CONFIG))["vision_model"], MAIN)
        with unittest.mock.patch.object(models, "find", return_value=BLIND_MAIN):
            self.assertEqual(models.with_one_model(dict(CONFIG))["vision_model"], "looker:9b")

    def test_an_engine_that_cannot_be_asked_leaves_the_picture_model_alone(self):
        with unittest.mock.patch.object(models, "find", side_effect=OSError("engine down")):
            self.assertEqual(models.with_one_model(dict(CONFIG))["vision_model"], "looker:9b")

    def test_without_one_model_nothing_changes(self):
        plain = {**CONFIG, "one_model": False}
        self.assertEqual(models.with_one_model(dict(plain)), plain)

    def test_the_settings_file_is_read_through_one_model(self):
        with unittest.mock.patch.object(models, "with_one_model", side_effect=lambda config: {**config, "seen": True}), \
                unittest.mock.patch.object(connections, "own_app_hosts", return_value=[]):
            self.assertTrue(connections.load_config()["seen"]) if Path(connections.CONFIG_FILE).is_file() \
                else self.skipTest("no brain.json on this machine")

    def test_a_coding_chat_on_one_model_does_not_switch_models(self):
        with unittest.mock.patch.object(models, "find", return_value=BLIND_MAIN), \
                unittest.mock.patch.object(models, "usable", return_value=True), \
                unittest.mock.patch.object(models, "is_loaded", return_value=True):
            config = models.with_one_model(dict(CONFIG))
            chosen = model_choice.chat_model(config, "Arty", "write a python function that reverses a string")
            self.assertEqual(model_choice.chosen_model(MAIN, chosen, any_installed=False), MAIN)

    def test_describing_a_picture_with_the_main_never_unloads_it(self):
        sent = []
        config = {**CONFIG, "vision_model": MAIN}
        with unittest.mock.patch.object(connections, "load_config", return_value=config), \
                unittest.mock.patch.object(attachments, "vision_model", return_value=SEEING_MAIN), \
                unittest.mock.patch.object(models, "make_room", return_value=[]), \
                unittest.mock.patch.object(connections, "http_json",
                                           side_effect=lambda url, body=None, **_: sent.append(body) or {"message": {"content": "a cat"}}):
            attachments.look(["aW1n"], "what is this?")
        self.assertEqual(sent[0]["keep_alive"], -1)

    def test_handing_a_job_to_another_model_is_refused_on_one_model(self):
        with unittest.mock.patch.object(models, "find", return_value={**BLIND_MAIN, "name": "coder:9b"}), \
                unittest.mock.patch.object(models, "_engine") as engine:
            answer = models.ask("coder:9b", "write a sort", CONFIG)
        engine.assert_not_called()
        self.assertIn("one model", answer)


class SideJobWaits(unittest.TestCase):
    def judged(self, scale):
        waited = []
        config = {"model": MAIN, "side_job_seconds_scale": scale, "options": {"num_ctx": 24576}}

        def engine(url, body=None, timeout=600, **_):
            waited.append(timeout)
            return {"message": {"content": json.dumps({"ok": True})}}
        with unittest.mock.patch.object(connections, "load_config", return_value=config), \
                unittest.mock.patch.object(connections, "http_json", side_effect=engine):
            honesty.ask_judge("is it ok?", 10, seconds=45)
        return waited[0]

    def test_judges_wait_longer_for_a_slow_main(self):
        self.assertEqual(self.judged(3), 135)

    def test_odd_scales_leave_the_wait_alone(self):
        for scale in (0, -2, "3", True, float("inf"), None):
            with self.subTest(scale=scale):
                self.assertEqual(self.judged(scale), 45)


class LongCodingReplies(unittest.TestCase):
    def test_a_reply_call_waits_long_enough_for_a_long_slow_answer(self):
        waited = []
        with unittest.mock.patch.object(connections, "http_json",
                                        side_effect=lambda url, body=None, timeout=600, **_: waited.append(timeout) or {}):
            agent_loop.engine_reply({"model": MAIN}, None, None, None)
        self.assertGreaterEqual(waited[0], 8192 / 15 + 120)

    def test_a_streamed_reply_waits_as_long(self):
        waited = []
        with unittest.mock.patch.object(connections, "stream_chat",
                                        side_effect=lambda body, on_text, timeout=600, **_: waited.append(timeout) or {}):
            agent_loop.engine_reply({"model": MAIN}, unittest.mock.Mock(), None, None)
        self.assertGreaterEqual(waited[0], 8192 / 15 + 120)

    def test_the_shipped_settings_leave_room_for_long_code(self):
        shipped = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())
        self.assertGreaterEqual(shipped["agent_options"]["num_predict"], 8192)
        self.assertNotIn("fallback_model", shipped)
        self.assertTrue(shipped["one_model"])
        if shipped["arty_models"]:
            self.assertIn(shipped["model"], shipped["arty_models"])


HELPER = {"name": "looker:7b", "billions": 7.6, "tools": False, "vision": True, "embedding": False, "thinking": False}


class HelperModelsStayOutOfPickers(unittest.TestCase):
    def setUp(self):
        for patch in (unittest.mock.patch.object(models, "installed", return_value=[BLIND_MAIN, HELPER]),
                      unittest.mock.patch.object(models, "helper_models", return_value=["looker:7b"]),
                      unittest.mock.patch.object(models, "arty_models", return_value=[]),
                      unittest.mock.patch.object(models, "solo_models", return_value=[]),
                      unittest.mock.patch.object(models, "toolless_allowed", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_helper_model_is_never_offered_to_chat_with(self):
        self.assertEqual([m["name"] for m in models.chat_models()], [MAIN])
        self.assertEqual([m["name"] for m in models.downloaded_chat_models()], [MAIN])

    def test_the_picture_helper_still_describes_pictures(self):
        with unittest.mock.patch.object(connections, "load_config", return_value={"vision_model": "looker:7b"}), \
                unittest.mock.patch.object(models, "find", return_value=HELPER):
            self.assertEqual(attachments.vision_model()["name"], "looker:7b")

    def test_the_shipped_settings_hide_the_picture_helper(self):
        shipped = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())
        self.assertIn(shipped["vision_model"], shipped["helper_models"])


class DirectPictureQuestionsUseTheSeeingMain(Server):
    def test_an_app_asking_the_picture_helper_gets_the_main_once_it_can_see(self):
        sent = []
        config = {**CONFIG, "vision_model": MAIN, "options": {"num_ctx": 24576, "num_predict": 2048}}
        picture = [{"role": "user", "content": [{"type": "text", "text": "where is Save?"},
                                                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}}]}]
        with unittest.mock.patch.object(connections, "load_config", return_value=config), \
                unittest.mock.patch.object(models, "find", side_effect=lambda name: {MAIN: SEEING_MAIN, "looker:7b": HELPER}.get(name)), \
                unittest.mock.patch.object(models, "make_room", return_value=[]), \
                unittest.mock.patch.object(brain.model_choice.main_model_return, "schedule") as schedule, \
                unittest.mock.patch.object(connections, "http_json",
                                           side_effect=lambda url, body=None, **_: sent.append(body) or {"message": {"content": "top left"}}):
            status, _ = self.request("POST", "/v1/chat/completions",
                                     {"model": "looker:7b", "messages": picture, "arty_direct_vision": True})
        self.assertEqual(status, 200)
        self.assertEqual((sent[0]["model"], sent[0]["keep_alive"]), (MAIN, -1))
        schedule.assert_not_called()


LABELS = {MAIN: "Big 35B"}


class ModelsShowTheirLabels(Server):
    def listed(self):
        with unittest.mock.patch.object(connections, "load_config", return_value={**CONFIG, "model_labels": LABELS}), \
                unittest.mock.patch.object(models, "find", return_value=BLIND_MAIN), \
                unittest.mock.patch.object(models, "chat_models", return_value=[BLIND_MAIN]), \
                unittest.mock.patch.object(models, "model_labels", return_value=LABELS), \
                unittest.mock.patch.object(models, "solo_entries", return_value=[]), \
                unittest.mock.patch.object(brain, "local_addon", None):
            status, body = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        return json.loads(body)["data"]

    def test_the_main_is_listed_under_its_label(self):
        arty, entry = self.listed()
        self.assertEqual(entry["id"], f"arty/{MAIN}")
        self.assertEqual(entry["name"], "Arty (Big 35B)")
        self.assertEqual(entry["label"], "Big 35B")
        self.assertIn("Big 35B", arty["description"])

    def test_arty_says_one_model_does_every_job(self):
        config = {**CONFIG, "coding_model": MAIN, "vision_model": MAIN}
        with unittest.mock.patch.object(models, "model_labels", return_value=LABELS):
            self.assertEqual(model_choice.arty_description(config, SEEING_MAIN), "Big 35B for chat, code and pictures")

    def test_an_unlabelled_model_keeps_its_id(self):
        with unittest.mock.patch.object(models, "model_labels", return_value={}):
            self.assertEqual(models.display_name("qwen3:14b"), "qwen3:14b")
        with unittest.mock.patch.object(models, "model_labels", return_value={"qwen3:14b": 5}):
            self.assertEqual(models.display_name("qwen3:14b"), "qwen3:14b")


if __name__ == "__main__":
    unittest.main()
