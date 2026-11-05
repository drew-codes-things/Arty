"""Lecture capture: recording -> transcript -> notes -> cards, with the model and Whisper faked.
Run from brain/: python3 -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_scratch = tempfile.mkdtemp()
os.environ["BRAIN_DB"] = os.path.join(_scratch, "arty.db")
os.environ["BRAIN_FILES"] = os.path.join(_scratch, "files")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lectures  # noqa: E402
import media  # noqa: E402
import personal  # noqa: E402
import store  # noqa: E402

TRANSCRIPT = "\n".join(f"[{minute:02d}:00] Today we cover TCP and UDP, part {minute}." for minute in range(60))


def fake_model(prompts):
    def complete(prompt, json_mode=False):
        prompts.append(prompt)
        if json_mode:
            return '{"cards": [{"question": "Is UDP reliable?", "answer": "No"}, {"question": "", "answer": "skipped"}]}'
        return "## TCP vs UDP\n- TCP is reliable"
    return complete


class Pipeline(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        lectures.init()
        self.recording = media.save_bytes(b"not really audio", "lesson.m4a", "audio/mp4", "test")

    def test_a_long_lecture_is_noted_in_sections_then_combined_and_carded(self):
        lecture = lectures.add(self.recording["id"], "Networking basics")
        prompts = []
        with mock.patch.object(media, "probe", return_value={"duration": 3600.0}), \
                mock.patch.object(media, "transcribe", return_value=TRANSCRIPT), \
                mock.patch.object(lectures, "CHUNK_WORDS", 200):
            outcome = lectures.process(lectures.get(lecture["id"]), fake_model(prompts))
        sections = [p for p in prompts if p.startswith("You are writing revision notes")]
        self.assertGreater(len(sections), 1)
        self.assertTrue(any(p.startswith("Below are notes on each part") for p in prompts))
        self.assertEqual(outcome["status"], "done")
        self.assertEqual(outcome["cards"], 1)  # the card without a question is dropped
        self.assertIn("Is UDP reliable?", personal.quiz_next("networking basics"))

    def test_a_recording_with_no_speech_fails_clearly(self):
        lecture = lectures.add(self.recording["id"], "Silent")
        with mock.patch.object(media, "probe", return_value={"duration": 5.0}), \
                mock.patch.object(media, "transcribe", return_value=""):
            with self.assertRaisesRegex(lectures.LectureError, "No speech"):
                lectures.process(lectures.get(lecture["id"]), fake_model([]))

    def test_only_audio_or_video_is_accepted(self):
        picture = media.save_bytes(b"png", "photo.png", "image/png", "test")
        with self.assertRaises(lectures.LectureError):
            lectures.add(picture["id"], "Not a lecture")
        with self.assertRaises(lectures.LectureError):
            lectures.add("0" * 24, "Missing")

    def test_an_untitled_lecture_gets_the_date(self):
        self.assertTrue(lectures.add(self.recording["id"], "  ")["title"].startswith("Lecture "))


class Pieces(unittest.TestCase):
    def test_chunks_split_between_lines_and_keep_everything(self):
        pieces = lectures.chunks(TRANSCRIPT, words=100)
        self.assertGreater(len(pieces), 1)
        self.assertEqual("\n".join(pieces), TRANSCRIPT)

    def test_bad_card_json_gives_no_cards(self):
        self.assertEqual(lectures.parse_cards("not json"), [])
        self.assertEqual(lectures.parse_cards('{"cards": "nope"}'), [])


if __name__ == "__main__":
    unittest.main()
