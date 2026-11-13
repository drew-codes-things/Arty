"""Writing in the user's voice: learning it, and when it's used. The model is faked.
Run from brain/: python3 -m unittest discover tests"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

os.environ["STYLE_FILE"] = os.path.join(tempfile.mkdtemp(), "style.json")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import style  # noqa: E402

LINKEDIN = """### Jordan (1w)
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


def writer(draft_text, invented=None, prompts=None):
    def complete(prompt, json_mode=False, **_):
        if prompts is not None:
            prompts.append((prompt, json_mode))
        if json_mode:
            return invented if isinstance(invented, str) else json.dumps({"invented": invented or []})
        return draft_text
    return complete


HOMELAB_POST = ("I set up my first homelab server on a mini PC with an SSD. It\u2019s been a wild ride.\n\n"
                "Took me 20 minutes to get Debian on it.\n\n#Homelab #Linux")


class HonestDrafts(unittest.TestCase):
    def test_the_writer_is_told_to_use_only_given_facts_and_placeholders_for_the_rest(self):
        prompts = []
        style.draft("write a LinkedIn post about getting my first homelab server", writer("My first homelab.", prompts=prompts))
        written = " ".join(prompts[0][0].split())
        self.assertIn("Never add anything else", written)
        self.assertIn("placeholder in square brackets", written)
        self.assertIn("A short draft with placeholders is better", written)

    def test_details_the_check_finds_made_up_become_placeholders(self):
        reply = style.draft("write a LinkedIn post about getting my first homelab server", writer(HOMELAB_POST, [
            {"words": "on a mini PC with an SSD", "placeholder": "[the hardware]"},
            {"words": "Took me 20 minutes to get Debian on it.", "placeholder": "what you set up first"}]))
        self.assertNotIn("mini PC", reply)
        self.assertNotIn("Debian", reply)
        self.assertIn("> I set up my first homelab server [the hardware].", reply)
        self.assertIn("> [what you set up first]", reply)

    def test_the_check_sees_the_request_the_known_facts_and_the_draft(self):
        prompts = []
        style.draft("write a LinkedIn post about my course", writer("My course is going well.", prompts=prompts),
                    known="- Studies a Level 3 Cybersecurity Diploma at Riverside College")
        checked = [prompt for prompt, json_mode in prompts if json_mode]
        self.assertEqual(len(checked), 1)
        self.assertIn("write a LinkedIn post about my course", checked[0])
        self.assertIn("Riverside College", checked[0])
        self.assertIn("My course is going well.", checked[0])
        self.assertIn("Riverside College", prompts[0][0])

    def test_curly_and_straight_apostrophes_still_match(self):
        reply = style.draft("write a LinkedIn post about getting my first homelab server", writer(HOMELAB_POST, [
            {"words": "It's been a wild ride.", "placeholder": "[how it went]"}]))
        self.assertNotIn("wild ride", reply)
        self.assertIn("[how it went]", reply)

    def test_specs_prices_and_years_nobody_gave_become_placeholders_even_when_the_check_misses_them(self):
        reply = style.draft("write a LinkedIn post about running my first 2 VMs", writer(
            "Running my first 2 VMs on 32GB of RAM and a 1 TB disk cost \u00a3150 in March 2024.\n\n#Homelab #Proxmox2026"))
        self.assertIn("my first 2 VMs on [number]GB of RAM and a [number] TB disk cost [the amount] in March [the year].", reply)
        self.assertIn("#Proxmox2026", reply)

    def test_the_check_knows_the_writers_name_so_the_sign_off_stays(self):
        prompts = []
        reply = style.draft("draft an email to my tutor saying I'll be late tomorrow", writer(
            "Hi [Tutor's name],\n\nI'll be late tomorrow.\n\nThanks,\nRobin", prompts=prompts), name="Robin")
        checked = [prompt for prompt, json_mode in prompts if json_mode][0]
        self.assertIn("Their name: Robin", checked)
        self.assertIn("> Robin", reply)

    def test_hashtags_are_never_turned_into_placeholders(self):
        reply = style.draft("write a LinkedIn post about passing the CCNA", writer(
            "I passed the CCNA.\n\n#CCNA #Networking", [{"words": "#CCNA #Networking", "placeholder": "[relevant hashtags]"},
                                                      {"words": "#Networking", "placeholder": "[relevant hashtag]"}]))
        self.assertIn("> #CCNA #Networking", reply)
        self.assertNotIn("[relevant", reply)

    def test_the_check_leaves_general_motivation_alone(self):
        prompts = []
        style.draft("write a cover letter for an IT support apprenticeship", writer("I enjoy helping people.", prompts=prompts))
        checked = [prompt for prompt, json_mode in prompts if json_mode][0]
        self.assertIn("why they want it", " ".join(checked.split()))

    def test_words_from_the_request_are_never_blanked_even_if_the_check_names_them(self):
        reply = style.draft("write a linkedin post with 3 tips for passing the CCNA after five years in support", writer(
            "Passing the CCNA after five years in support? Here's what worked.",
            [{"words": "CCNA", "placeholder": "[certification name]"},
             {"words": "five years in support", "placeholder": "[time in support role]"}]))
        self.assertIn("> Passing the CCNA after five years in support?", reply)
        self.assertNotIn("Fill in", reply)

    def test_a_placeholder_the_writer_left_is_never_wrapped_again(self):
        reply = style.draft("write a cover letter for an IT support apprenticeship", writer(
            "Hi [Recipient's name], I'm applying.", [{"words": "[Recipient's name]", "placeholder": "[Recipient's name]"},
                                                    {"words": "Recipient's name", "placeholder": "[the name]"}]))
        self.assertIn("> Hi [Recipient's name], I'm applying.", reply)
        self.assertNotIn("[[", reply)

    def test_ordinary_numbers_in_a_draft_are_left_alone(self):
        written = ("After 5 years in support, here are my 3 tips:\n1. Lab every day\n2. Read the docs\n3. Book the exam\n\n"
                   "Give it 100% and keep at it 24/7, even with COVID-19 lessons and Windows 11 in mind.\n\n#CCNA")
        reply = style.draft("write a linkedin post with 3 tips for passing the CCNA after five years in support", writer(written))
        for line in written.splitlines()[:-1]:
            if line:
                self.assertIn(f"> {line}", reply)
        self.assertNotIn("[", reply)
        self.assertNotIn("Fill in", reply)

    def test_a_made_up_time_is_replaced_whole(self):
        reply = style.draft("write an email to my tutor asking to meet", writer("Hi [Tutor's name],\n\nCould we meet at 2:00 pm?\n\nSam"), name="Sam")
        self.assertIn("Could we meet at [the time]?", reply)

    def test_prices_and_durations_the_user_gave_are_kept_even_in_words(self):
        reply = style.draft("write a LinkedIn post about my server that cost me \u00a3150 after five years in support", writer(
            "After 5 years in support I bought a \u00a3150 server."))
        self.assertIn("After 5 years in support I bought a \u00a3150 server.", reply)

    def test_a_made_up_time_or_date_gets_a_placeholder_saying_so(self):
        reply = style.draft("draft an email to my tutor saying I'll be late tomorrow", writer(
            "Hi [Tutor's name],\n\nI'll be in at 10:30am on the 7th.\n\nThanks,\nSam"), name="Sam")
        self.assertIn("I'll be in at [the time] on the [the date].", reply)

    def test_numbers_from_the_known_facts_are_kept(self):
        reply = style.draft("write a LinkedIn post about my course", writer("Level 3 is going well."),
                            known="- Studies a Level 3 Cybersecurity Diploma")
        self.assertIn("Level 3 is going well.", reply)

    def test_the_reply_says_what_to_fill_in(self):
        reply = style.draft("draft an email to my tutor saying I'll be late tomorrow", writer(
            "Subject: Late tomorrow\n\nHi [Tutor's name],\n\nI'll be late tomorrow because of [the reason].\n\nThanks,\nSam"), name="Sam")
        quote_end = reply.rindex("> Sam")
        self.assertIn("Fill in [Tutor's name] and [the reason] before you send it.", reply[quote_end:])

    def test_a_draft_with_nothing_to_fill_in_has_no_fill_in_line(self):
        reply = style.draft("write a LinkedIn post about my exam", writer("I passed my exam.\n\n#Exams"))
        self.assertNotIn("Fill in", reply)

    def test_a_check_that_gives_no_usable_answer_leaves_the_draft_as_written(self):
        reply = style.draft("write a LinkedIn post about my exam", writer("I passed my exam.\n\n#Exams", invented="not json"))
        self.assertIn("> I passed my exam.", reply)


class Drafting(unittest.TestCase):
    def test_the_writer_gets_the_voice_without_the_examples_it_would_copy_as_facts(self):
        profile = ("- **Tone**: Casual and direct; informal phrasing (e.g., \u201cnearly did,\u201d \u201cjust a bit phased\u201d).\n"
                   "- **Words**: Technical jargon (e.g., \u201csubnet,\u201d \u201cDebian Linux\u201d), casual expressions "
                   "(\u201csniffly nose\u201d).\n- **Layout**: Short paragraphs; lists for tasks (e.g., \u201c- Connect my microphone\u2026\u201d).")
        prompts = []
        with unittest.mock.patch.object(style, "load", return_value={"profile": profile}):
            style.draft("write a LinkedIn post about my course", writer("My course is going well.", prompts=prompts))
        written = prompts[0][0]
        for copied in ("sniffly", "Debian", "subnet", "nearly did", "microphone"):
            self.assertNotIn(copied, written)
        self.assertIn("Casual and direct; informal phrasing.", written)
        self.assertIn("Technical jargon, casual expressions.", written)

    def test_a_new_post_is_drafted_from_the_request_and_quoted(self):
        prompts = []
        reply = style.draft("write a LinkedIn post about building my own AI assistant",
                            lambda prompt, **_: prompts.append(prompt) or "I built my own AI assistant.\n\n#AI")
        self.assertTrue(reply.startswith("Here's a draft LinkedIn post in your style."))
        self.assertIn("> I built my own AI assistant.\n>\n> #AI", reply)
        self.assertIn("Never add anything else", prompts[0])
        self.assertIn("no greeting and no sign-off", prompts[0])

    def test_a_posts_hashtags_go_on_their_own_line(self):
        reply = style.draft("write a LinkedIn post about my exam", lambda prompt, **_: "I passed my exam. #Networking #Cyber")
        self.assertTrue(reply.endswith("> I passed my exam.\n>\n> #Networking #Cyber"))

    def test_the_models_own_lead_in_is_dropped(self):
        reply = style.draft("write a LinkedIn post about my homelab",
                            lambda prompt, **_: "Here's a LinkedIn post based on your request:\n\n---\n\nMy homelab grew this year.\n\n---")
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
