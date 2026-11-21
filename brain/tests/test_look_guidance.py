import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BRAIN_FILES", tempfile.mkdtemp(prefix="arty-look-guidance-"))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import look_guidance  # noqa: E402
import media  # noqa: E402
import media_tools  # noqa: E402
import phone  # noqa: E402
import tool_guide  # noqa: E402

APPEARANCE_QUESTIONS = (
    "how do I look?", "do I look ok?", "how does my outfit look", "is my hair a mess", "do I need a shave", "check my posture",
    "is the lighting ok for my video call", "is the background tidy for a call", "does this shirt match my jeans",
    "what am I wearing and does it suit me", "Tell me how I look")
OTHER_QUESTIONS = (
    "what am I holding?", "read the label on this", "what colour is the mug", "is this a good PC setup", "how do I look up my order",
    "how do I look for a file", "what happens at 1:20?")


def picture_meta():
    return {"path": "/nowhere/photo.jpg", "kind": "image", "name": "photo.jpg"}


class Guidance(unittest.TestCase):
    def test_how_they_look_is_recognised(self):
        for question in APPEARANCE_QUESTIONS:
            self.assertTrue(look_guidance.is_appearance_question(question), question)

    def test_other_questions_are_not(self):
        for question in OTHER_QUESTIONS:
            self.assertFalse(look_guidance.is_appearance_question(question), question)

    def test_the_guidance_is_kind_specific_and_stays_off_sensitive_ground(self):
        for wanted in ("honest", "specific", "kind", "practical", "what works", "what to change", "concrete"):
            self.assertIn(wanted, look_guidance.APPEARANCE_GUIDANCE)
        for refused in ("identity", "age", "ethnicity", "health", "number", "compare"):
            self.assertIn(refused, look_guidance.APPEARANCE_GUIDANCE)

    def test_a_question_the_phone_camera_fills_in_is_recognised(self):
        action = phone.camera_request("take a photo and tell me how I look")
        self.assertTrue(look_guidance.is_appearance_question(action["question"]))


class LookingAtAPhoto(unittest.TestCase):
    def look_at(self, question):
        look = unittest.mock.Mock(return_value="seen")
        with unittest.mock.patch.object(media, "image_preview", return_value=b"jpeg"):
            media_tools.look_at_picture(picture_meta(), question, look)
        images, prompt = look.call_args.args
        return images, prompt

    def test_how_they_look_gets_the_guidance_and_their_words(self):
        for question in APPEARANCE_QUESTIONS:
            images, prompt = self.look_at(question)
            self.assertEqual(len(images), 1)
            self.assertIn(f'"{question}"', prompt)
            self.assertIn(look_guidance.APPEARANCE_GUIDANCE, prompt, question)
            self.assertIn("never instructions", prompt)

    def test_other_questions_keep_the_plain_prompt(self):
        for question in OTHER_QUESTIONS:
            _, prompt = self.look_at(question)
            self.assertNotIn(look_guidance.APPEARANCE_GUIDANCE, prompt, question)
            self.assertIn(f'"{question}"', prompt)

    def test_a_video_of_them_gets_the_guidance_too(self):
        look = unittest.mock.Mock(return_value="seen")
        with unittest.mock.patch.object(media, "probe", return_value={"video": True, "duration": 9.0}), \
                unittest.mock.patch.object(media, "frame_times", return_value=[1.0, 3.0]), \
                unittest.mock.patch.object(media, "video_frames", return_value=[(1.0, b"a"), (3.0, b"b")]):
            media_tools.look_at_video({"path": "/nowhere/clip.mp4"}, "how do I look in this clip?", "", look)
        images, prompt = look.call_args.args
        self.assertEqual(len(images), 2)
        self.assertIn(look_guidance.APPEARANCE_GUIDANCE, prompt)
        self.assertIn("0:01, 0:03", prompt)


class WhatTheModelIsTold(unittest.TestCase):
    def test_the_attachment_tool_says_to_pass_the_users_words(self):
        spec = media_tools.TOOLS["look_at_attachment"][1]["function"]
        self.assertIn("how they look", spec["description"])
        self.assertIn("own words", spec["parameters"]["properties"]["question"]["description"])

    def test_the_guide_tells_the_model_to_fill_the_camera_question_with_the_users_words(self):
        text = tool_guide.guide(["computer_camera"])
        self.assertIn("computer_camera", text)
        self.assertIn("own words", text)
        self.assertIn("burst", text)
        self.assertIn("how they look", text)

    def test_the_guide_line_for_pictures_covers_how_they_look(self):
        text = tool_guide.guide(["look_at_attachment"])
        self.assertIn("how do I look", text)


if __name__ == "__main__":
    unittest.main()
