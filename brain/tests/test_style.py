"""Writing in the user's voice: learning it, and when it's used. The model is faked.
Run from brain/: python3 -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["STYLE_FILE"] = os.path.join(tempfile.mkdtemp(), "style.json")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import style  # noqa: E402

LINKEDIN = """### Andrew (1w)
I attended Nutanix NEXT as part of my work experience week.

#AI #Infrastructure
### Someone else (2w) (repost)
(repost) Not mine.
"""
MESSAGES = [f"can you make it so the app does thing number {n} properly, cheers" for n in range(12)] + ["ok", "TEST: ignore me please, this is a test"]


class Learning(unittest.TestCase):
    def test_posts_are_split_and_reposts_left_out(self):
        posts = style.split_posts(LINKEDIN)
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].startswith("I attended Nutanix NEXT"))

    def test_short_messages_tests_and_repeats_are_left_out(self):
        picked = style.pick_messages(MESSAGES + [MESSAGES[0]])
        self.assertEqual(len(picked), 12)
        self.assertFalse(any(m.startswith("TEST") or m == "ok" for m in picked))

    def test_builds_a_profile_and_uses_it_for_writing(self):
        prompts = []
        reply = style.refresh(lambda: (MESSAGES, style.split_posts(LINKEDIN)),
                              lambda prompt: prompts.append(prompt) or "- Casual and friendly\n- British spelling")
        self.assertIn("12 of your messages and 1 of your LinkedIn posts", reply)
        self.assertIn("LinkedIn post:\nI attended Nutanix NEXT", prompts[0])
        note = style.voice_note()
        self.assertIn("British spelling", note)
        self.assertIn("correct spelling", note)
        self.assertNotIn("I attended Nutanix NEXT", note)  # small models copy an example's facts into new posts
        self.assertIn("never invent", note)
        self.assertFalse(style.due())

    def test_too_little_writing_says_so(self):
        reply = style.refresh(lambda: (["hi there, this is one message only"], []), lambda prompt: "unused")
        self.assertIn("haven't seen enough", reply)


class Drafting(unittest.TestCase):
    def test_a_new_post_is_drafted_from_the_request_and_quoted(self):
        prompts = []
        reply = style.draft("write a LinkedIn post about building my own AI assistant",
                            lambda prompt: prompts.append(prompt) or "I built my own AI assistant.\n\n#AI")
        self.assertTrue(reply.startswith("Here's a draft LinkedIn post in your style."))
        self.assertIn("> I built my own AI assistant.\n>\n> #AI", reply)
        self.assertIn("never add events", prompts[0])
        self.assertIn("no greeting and no sign-off", prompts[0])

    def test_a_posts_hashtags_go_on_their_own_line(self):
        reply = style.draft("write a LinkedIn post about my exam", lambda prompt: "I passed my exam. #Networking #Cyber")
        self.assertTrue(reply.endswith("> I passed my exam.\n>\n> #Networking #Cyber"))

    def test_the_models_own_lead_in_is_dropped(self):
        reply = style.draft("write a LinkedIn post about my homelab",
                            lambda prompt: "Here's a LinkedIn post based on your request:\n\n---\n\nMy homelab grew this year.\n\n---")
        self.assertTrue(reply.endswith("> My homelab grew this year."))
        self.assertNotIn("based on your request", reply)

    def test_only_new_writing_goes_straight_to_drafting(self):
        self.assertTrue(style.is_new_draft("draft an email to my tutor asking for an extension"))
        self.assertTrue(style.is_new_draft("make a linkedin post about my homelab"))
        self.assertFalse(style.is_new_draft("reply to the email from college"))
        self.assertFalse(style.is_new_draft("improve this post: I went to a thing"))
        self.assertFalse(style.is_new_draft("give me some LinkedIn post ideas"))


class WhenItsUsed(unittest.TestCase):
    def test_writing_requests(self):
        for text in ["draft an email to my tutor about the deadline", "write a LinkedIn post about my work experience",
                     "can you help me write a reply to this", "give me some post ideas", "reword this message for me"]:
            self.assertTrue(style.is_writing_request(text), text)
        for text in ["what's the weather", "check my emails", "read the email from college", "make me a cup of tea"]:
            self.assertFalse(style.is_writing_request(text), text)

    def test_learn_requests(self):
        self.assertTrue(style.is_learn_request("learn how I write"))
        self.assertTrue(style.is_learn_request("can you update my writing style"))
        self.assertFalse(style.is_learn_request("how do I write a CV"))


if __name__ == "__main__":
    unittest.main()
