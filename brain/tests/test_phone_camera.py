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
_scratch = tempfile.mkdtemp(prefix="arty-phone-camera-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import connections  # noqa: E402
import phone  # noqa: E402


def action_in(reply):
    return json.loads(reply.split("<phone-action>")[1].split("</phone-action>")[0])


class CameraRequests(unittest.TestCase):
    def test_asking_for_the_camera_is_recognised(self):
        for text in ("take a photo", "please use the camera", "Can you open my phone camera", "take a picture", "look at this through the camera",
                     "scan this with the camera", "hey arty, take a snap"):
            self.assertEqual(phone.camera_request(text), {"kind": "camera", "question": ""}, text)

    def test_what_to_do_with_the_photo_becomes_the_question(self):
        self.assertEqual(phone.camera_request("take a photo and tell me what this is")["question"], "Tell me what this is")
        self.assertEqual(phone.camera_request("can you take a picture of this and read the label to me")["question"], "Read the label to me")
        self.assertEqual(phone.camera_request("use my phone camera so you can read this letter")["question"], "Read this letter")

    def test_talk_about_cameras_is_not_a_request(self):
        for text in ("how do I take a photo on iphone", "I can't use the camera", "what camera does the pixel have",
                     "my camera is broken, take it back to the shop", "remind me to take a photo of the car tomorrow"):
            self.assertIsNone(phone.camera_request(text), text)

    def test_the_ios_app_gets_a_button_to_press_and_the_question(self):
        action = phone.camera_request("take a photo and tell me what this is")
        reply = phone.camera_reply(action, "ios")
        self.assertEqual(action_in(reply), action)
        self.assertIn("Tap the button", reply)
        self.assertNotIn("phone-action", phone.without_action(reply))

    def test_the_android_app_is_pointed_at_the_paperclip_and_nothing_is_opened(self):
        reply = phone.camera_reply({"kind": "camera", "question": ""}, "phone")
        self.assertNotIn("phone-action", reply)
        self.assertIn("paperclip", reply)

    def test_other_apps_are_told_where_the_camera_lives(self):
        reply = phone.camera_reply({"kind": "camera", "question": ""}, "desktop")
        self.assertNotIn("phone-action", reply)
        self.assertIn("asks you first", reply)


class CameraInChat(unittest.TestCase):
    def turn(self, text, client):
        with unittest.mock.patch.object(brain, "archive_turn") as archived, \
                unittest.mock.patch.object(brain, "local_addon", None), \
                unittest.mock.patch.object(connections, "load_config", return_value={"model": "m"}), \
                unittest.mock.patch.object(brain, "use_requested_model"), \
                unittest.mock.patch.object(brain.desktop_look, "user_active"):
            result = brain.run_turn([{"role": "user", "content": text}], client=client)
        return result, archived

    def test_on_the_ios_app_the_camera_request_answers_straight_away_with_the_action(self):
        (reply, used, _, _), archived = self.turn("take a photo and tell me what this is", "ios")
        self.assertEqual(used, ["phone_camera"])
        self.assertEqual(action_in(reply)["kind"], "camera")
        archived.assert_called_once()

    def test_on_the_desktop_the_phone_shortcut_is_not_used(self):
        class ReachedTheModel(Exception):
            pass

        with unittest.mock.patch.object(brain, "archive_turn"), \
                unittest.mock.patch.object(brain, "local_addon", None), \
                unittest.mock.patch.object(connections, "load_config", return_value={"model": "m"}), \
                unittest.mock.patch.object(brain, "use_requested_model"), \
                unittest.mock.patch.object(brain, "quick_action", return_value=None), \
                unittest.mock.patch.object(brain, "camera_action") as camera_action, \
                unittest.mock.patch.object(brain, "expand_attachments", side_effect=ReachedTheModel), \
                unittest.mock.patch.object(brain.desktop_look, "user_active"):
            with self.assertRaises(ReachedTheModel):
                brain.run_turn([{"role": "user", "content": "take a photo with the webcam"}], client="desktop")
        camera_action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
