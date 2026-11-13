import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import http.client  # noqa: E402

from test_review_brain import KEY, Server  # noqa: E402

import attachments  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import media  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402

CONFIG = {"model": "qwen3:14b", "coding_model": "mimo:9b", "vision_model": "mimo:9b", "keep_alive": -1, "options": {}}
MIMO = {"name": "mimo:9b", "billions": 9.0, "tools": True, "vision": True, "embedding": False, "thinking": True}
QWEN = {"name": "qwen3:14b", "billions": 14.0, "tools": True, "vision": False, "embedding": False, "thinking": True}
LOOKER_WITHOUT_TOOLS = {**MIMO, "name": "llava:7b", "tools": False}
PICTURE_PART = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}}


def with_picture(text):
    return {"role": "user", "content": [{"type": "text", "text": text}, PICTURE_PART]}


def found(name):
    return {model["name"]: model for model in (MIMO, QWEN, LOOKER_WITHOUT_TOOLS)}.get(name)


class NewPicture(unittest.TestCase):
    def test_a_picture_in_the_latest_message_is_new(self):
        self.assertTrue(attachments.has_new_picture([{"role": "user", "content": "hi"}, with_picture("what is this?")]))

    def test_a_picture_only_in_an_earlier_message_is_not_new(self):
        messages = [with_picture("what is this?"), {"role": "assistant", "content": "A cat."}, {"role": "user", "content": "thanks"}]
        self.assertFalse(attachments.has_new_picture(messages))

    def test_plain_text_has_no_picture(self):
        self.assertFalse(attachments.has_new_picture([{"role": "user", "content": "what's the capital of Norway?"}]))
        self.assertFalse(attachments.has_new_picture([]))

    def test_an_uploaded_image_counts_and_an_uploaded_pdf_does_not(self):
        stored = {"img1": {"kind": "image"}, "doc1": {"kind": "document"}}
        with unittest.mock.patch.object(media, "get", side_effect=stored.get):
            for file_id, expected in (("img1", True), ("doc1", False), ("gone", False)):
                with self.subTest(file_id=file_id):
                    message = {"role": "user", "content": [{"type": "file", "file": {"file_id": file_id}}]}
                    self.assertIs(attachments.has_new_picture([message]), expected)


class PictureRouting(unittest.TestCase):
    def setUp(self):
        for name, value in (("find", found), ("is_loaded", lambda name: False)):
            patcher = unittest.mock.patch.object(models, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_new_picture_for_the_main_entry_runs_on_the_vision_model(self):
        self.assertEqual(model_choice.chat_model(CONFIG, "Arty", "what's the total on this invoice?", True), "arty/mimo:9b")

    def test_without_a_new_picture_nothing_changes(self):
        self.assertEqual(model_choice.chat_model(CONFIG, "Arty", "what's the total on this invoice?"), "Arty")

    def test_picture_routing_can_be_switched_off(self):
        config = {**CONFIG, "route_picture_chats": False}
        self.assertEqual(model_choice.chat_model(config, "Arty", "what's on this?", True), "Arty")

    def test_an_explicit_model_pick_is_left_alone(self):
        self.assertEqual(model_choice.chat_model(CONFIG, "arty/qwen3:14b", "what's on this?", True), "arty/qwen3:14b")

    def test_a_vision_model_that_cannot_use_tools_only_looks(self):
        config = {**CONFIG, "vision_model": "llava:7b"}
        self.assertEqual(model_choice.chat_model(config, "Arty", "what's on this?", True), "Arty")

    def test_a_missing_vision_model_leaves_the_turn_on_the_main_model(self):
        config = {**CONFIG, "vision_model": "gone:7b"}
        with self.assertLogs("brain", "WARNING"):
            self.assertEqual(model_choice.chat_model(config, "Arty", "what's on this?", True), "Arty")


class LookingDuringAPictureTurn(unittest.TestCase):
    def setUp(self):
        self.sent = []
        patches = [
            unittest.mock.patch.object(connections, "load_config", return_value=dict(CONFIG)),
            unittest.mock.patch.object(attachments, "vision_model", return_value=MIMO),
            unittest.mock.patch.object(models, "make_room", return_value=[]),
            unittest.mock.patch.object(connections, "http_json",
                                       side_effect=lambda url, body=None, **_: self.sent.append(body) or {"message": {"content": "an invoice"}}),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def test_look_keeps_the_vision_model_when_the_turn_runs_on_it(self):
        attachments.look(["aW1n"], "what is it?", keep_alive=model_choice.OTHER_MODEL_KEEP_ALIVE)
        self.assertEqual(self.sent[0]["keep_alive"], model_choice.OTHER_MODEL_KEEP_ALIVE)

    def test_look_still_unloads_by_default(self):
        attachments.look(["aW1n"], "what is it?")
        self.assertEqual(self.sent[0]["keep_alive"], 0)

    def test_the_look_keep_alive_reaches_the_look(self):
        stored = {"id": "f1", "kind": "image", "name": "a.png", "size": 2048, "path": "/nowhere"}
        with unittest.mock.patch.object(attachments, "stored_attachment", return_value=stored), \
                unittest.mock.patch.object(media, "analyse", side_effect=lambda meta, question, look: look(["aW1n"], question)):
            attachments.expand_attachments([with_picture("what is it?")], model_choice.OTHER_MODEL_KEEP_ALIVE)
        self.assertEqual(self.sent[0]["keep_alive"], model_choice.OTHER_MODEL_KEEP_ALIVE)


class PictureKeepAlive(unittest.TestCase):
    def test_a_turn_on_the_vision_model_keeps_it_for_the_look(self):
        config = {**CONFIG, "model": "mimo:9b", "keep_alive": model_choice.OTHER_MODEL_KEEP_ALIVE}
        self.assertEqual(brain.look_keep_alive(config, True), model_choice.OTHER_MODEL_KEEP_ALIVE)

    def test_a_turn_on_the_main_model_unloads_the_looker_straight_away(self):
        self.assertEqual(brain.look_keep_alive(dict(CONFIG), False), 0)

    def test_a_coding_turn_on_another_model_unloads_the_looker(self):
        config = {**CONFIG, "model": "coder:7b", "vision_model": "mimo:9b", "keep_alive": "3m"}
        self.assertEqual(brain.look_keep_alive(config, True), 0)


class WarmOnUpload(unittest.TestCase):
    def setUp(self):
        self.sent, self.room, self.returns = [], [], []
        patches = [
            unittest.mock.patch.object(models, "find", side_effect=found),
            unittest.mock.patch.object(models, "is_loaded", return_value=False),
            unittest.mock.patch.object(models, "make_room", side_effect=lambda name, budget: self.room.append(name) or []),
            unittest.mock.patch.object(connections, "http_json", side_effect=lambda url, body=None, **_: self.sent.append((url, body)) or {}),
            unittest.mock.patch.object(model_choice.main_model_return, "schedule", side_effect=lambda config, *rest: self.returns.append(config)),
        ]
        for patch in patches:
            patch.start()
        self.addCleanup(unittest.mock.patch.stopall)

    def test_an_uploaded_picture_loads_the_vision_model_and_the_main_model_comes_back_later(self):
        model_choice.warm_picture_model(dict(CONFIG)).join(5)
        self.assertEqual(self.room, ["mimo:9b"])
        url, body = self.sent[0]
        self.assertTrue(url.endswith("/api/generate"))
        self.assertEqual((body["model"], body["keep_alive"]), ("mimo:9b", model_choice.OTHER_MODEL_KEEP_ALIVE))
        self.assertEqual(self.returns[0]["model"], "qwen3:14b")

    def test_a_vision_model_already_loaded_is_left_alone(self):
        with unittest.mock.patch.object(models, "is_loaded", return_value=True):
            self.assertIsNone(model_choice.warm_picture_model(dict(CONFIG)))
        self.assertEqual((self.room, self.sent), ([], []))

    def test_nothing_is_warmed_when_picture_routing_is_off(self):
        self.assertIsNone(model_choice.warm_picture_model({**CONFIG, "route_picture_chats": False}))
        self.assertEqual(self.room, [])

    def test_nothing_is_warmed_while_an_agent_session_holds_a_model(self):
        with unittest.mock.patch.object(model_choice.main_model_return, "holds_a_session", return_value=True):
            self.assertIsNone(model_choice.warm_picture_model(dict(CONFIG)))
        self.assertEqual(self.room, [])


class UploadWarmsTheLooker(Server):
    def upload(self, kind):
        meta = {"id": "f1", "name": "a.png", "mime": "image/png", "kind": kind, "size": 3}
        with unittest.mock.patch.object(media, "save_stream", return_value=meta), \
                unittest.mock.patch.object(media, "public", return_value={"id": "f1"}), \
                unittest.mock.patch.object(connections, "load_config", return_value=dict(CONFIG)), \
                unittest.mock.patch.object(model_choice, "warm_picture_model") as warm:
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
            self.addCleanup(connection.close)
            connection.request("POST", "/v1/files", body=b"png", headers={
                "Authorization": f"Bearer {KEY}", "Content-Type": "image/png", "X-Filename": "a.png", "Content-Length": "3"})
            self.assertEqual(connection.getresponse().status, 201)
        return warm

    def test_an_uploaded_picture_warms_the_vision_model(self):
        self.upload("image").assert_called_once()

    def test_an_uploaded_document_warms_nothing(self):
        self.upload("document").assert_not_called()


if __name__ == "__main__":
    unittest.main()
