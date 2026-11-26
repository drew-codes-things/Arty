"""Arty says when it can't do something: no claiming actions no tool did, or that failed tools worked.
Run from brain/: python3 -m unittest discover tests"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):  # brain reads these at import; the checks here never use them
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-honesty-")
# Importing brain imports the modules other tests set up; keep every state file they'd use in scratch.
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import agent_loop  # noqa: E402
import connections  # noqa: E402
import honesty  # noqa: E402
import memory_facts  # noqa: E402
import native_calls  # noqa: E402
import reply_rules  # noqa: E402
import shortcuts  # noqa: E402
import steering  # noqa: E402


class Honesty(unittest.TestCase):
    DESKTOP = ("Here's what I see on my desktop right now. I've opened a browser window showing an article on Medium. "
               "The background has a subtle grid pattern and the theme is dark.")

    def setUp(self):
        config = unittest.mock.patch.object(connections, "load_config", return_value={"model": "qwen3:14b"})
        config.start()
        self.addCleanup(config.stop)

    def judged(self, claims, kind="A"):
        def judge(url, body, **_):
            prompt = body["messages"][0]["content"]
            verdict = {"kind": kind} if prompt.startswith("The person said") else {"false_claims": claims}
            return {"message": {"content": brain.json.dumps(verdict)}}
        return unittest.mock.patch.object(connections, "http_json", side_effect=judge)

    def test_a_fact_the_confirmation_calls_an_answer_is_kept(self):
        claim = {"text": "Your favourite biscuit is a custard cream.", "correction": "I don't know your favourite biscuit."}
        with self.judged([claim], kind="B"):
            reply = brain.be_honest("Your favourite biscuit is a custard cream.", [], [], request="Which biscuit did I say?")
        self.assertEqual(reply, "Your favourite biscuit is a custard cream.")

    def test_both_judges_see_what_the_user_said(self):
        with self.judged([{"text": "Done.", "correction": ""}]) as call:
            brain.be_honest("Done.", [], [], request="Turn the lights off.")
        prompts = [c.kwargs["body"]["messages"][0]["content"] for c in call.call_args_list]
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all("Turn the lights off." in prompt for prompt in prompts))

    def test_a_failed_confirmation_keeps_the_claim(self):
        claims = {"message": {"content": brain.json.dumps({"false_claims": [{"text": "Done.", "correction": ""}]})}}
        with unittest.mock.patch.object(connections, "http_json", side_effect=[claims, OSError("engine down")]):
            self.assertEqual(brain.be_honest("Done.", [], []), honesty.HONEST_NOTHING_DONE)

    def test_a_false_claim_is_replaced_by_its_correction_and_the_rest_is_kept(self):
        claim = {"text": "I've opened a browser window showing an article on Medium.",
                 "correction": "A browser window showing an article on Medium is open."}
        with self.judged([claim]):
            reply = brain.be_honest(self.DESKTOP, ["workspace__look_at_desktop"], [])
        self.assertNotIn("I've opened", reply)
        self.assertIn("is open.", reply)
        self.assertIn("grid pattern", reply)

    def test_a_reply_that_was_only_a_false_claim_says_nothing_was_done(self):
        with self.judged([{"text": "Les lumières sont allumées.", "correction": ""}]):
            self.assertEqual(brain.be_honest("Les lumières sont allumées.", [], []), honesty.HONEST_NOTHING_DONE)

    def test_a_claim_already_shown_is_corrected_afterwards(self):
        claim = {"text": "Your pizza is on its way.", "correction": "I can't order food, so nothing is on its way."}
        with self.judged([claim]):
            reply = brain.be_honest("Your pizza is on its way. Enjoy!", [], [], already_sent="Your pizza is on its way.")
        self.assertTrue(reply.startswith("Your pizza is on its way. Enjoy!"))
        self.assertTrue(reply.endswith("nothing is on its way."))

    def test_claims_the_judge_made_up_are_ignored(self):
        with self.judged([{"text": "Something the reply never said.", "correction": "x"}]):
            self.assertEqual(brain.be_honest("Paris is the capital of France.", [], []), "Paris is the capital of France.")

    def test_an_honest_reply_passes_through(self):
        with self.judged([]):
            self.assertEqual(brain.be_honest("Paris is the capital of France.", [], []), "Paris is the capital of France.")

    def test_work_a_tool_really_did_is_never_second_guessed(self):
        with self.judged([{"text": "I've opened the page.", "correction": "I didn't open it."}]) as call:
            self.assertEqual(brain.be_honest("I've opened the page.", ["workspace__browser_open"], []), "I've opened the page.")
        call.assert_not_called()

    def test_the_judge_sees_which_tools_worked_and_which_failed(self):
        with self.judged([]) as call:
            brain.be_honest("I've restarted the Kuma container.", ["use_tools", "homelab__restart", "weather"],
                            [("homelab__restart", "tool error 500: permission denied")])
        prompt = call.call_args.kwargs["body"]["messages"][0]["content"]
        self.assertIn("- homelab__restart: failed (tool error 500: permission denied)", prompt)
        self.assertIn("- weather: worked", prompt)
        self.assertNotIn("use_tools", prompt)

    def test_tools_the_app_ran_since_the_last_request_count(self):
        conversation = [{"role": "user", "content": "old"}, {"role": "tool", "tool_name": "computer_old", "content": "ok"},
                        {"role": "user", "content": "fix the bug"},
                        {"role": "tool", "tool_name": "computer_edit_file", "content": "edited"},
                        {"role": "tool", "tool_name": "computer_run_command", "content": "Error: No module named pytest"},
                        {"role": "tool", "tool_name": "computer_run_command", "content": "3 passed"}]
        used, failed = brain.client_tool_runs(conversation)
        self.assertEqual(used, ["computer_edit_file", "computer_run_command", "computer_run_command"])
        self.assertEqual(failed, [("computer_run_command", "Error: No module named pytest")])
        record = honesty.tools_record(used, failed)
        self.assertIn("- computer_edit_file: worked", record)
        self.assertIn("- computer_run_command: ran 2 times, 1 failed", record)

    def test_a_failed_check_leaves_the_reply_alone(self):
        with unittest.mock.patch.object(connections, "http_json", side_effect=OSError("engine down")):
            self.assertEqual(brain.be_honest("Done, all sorted.", [], []), "Done, all sorted.")

    LAB_REPORT = ("Aim: to find how temperature changes the rate of reaction between sodium thiosulfate and hydrochloric "
                  "acid.\n\nMethod: 50 ml of thiosulfate was heated to 20, 30, 40 and 50 degrees, acid was added and the time "
                  "for a cross under the flask to vanish was recorded three times at each temperature.\n\nResults: the mean "
                  "times were 62, 35, 19 and 11 seconds.\n\nConclusion: the rate roughly doubles for every 10 degree rise.")
    SOLUTIONS = ("**1. Integral of x e^x dx**\nu = x, dv = e^x dx, so v = e^x\n= x e^x - integral of e^x dx = x e^x - e^x + C\n\n"
                 "**2. Integral of x sin(x) dx**\nu = x, dv = sin(x) dx, so v = -cos(x)\n= -x cos(x) + sin(x) + C\n\n"
                 "**3. Integral of ln(x) dx**\nu = ln(x), dv = dx, so v = x\n= x ln(x) - x + C")

    def checked_reply(self, reply, claim, request, tools=()):
        with self.judged([{"text": claim, "correction": "I did not write or save that for you."}]):
            return brain.be_honest(reply, list(tools), [], request=request)

    def test_work_written_out_in_the_reply_is_not_a_false_claim(self):
        claim = "I've written your lab report:"
        reply = f"{claim}\n\n{self.LAB_REPORT}\n\nLet me know if you want it longer."
        self.assertEqual(self.checked_reply(reply, claim, "Write my chemistry lab report."), reply)

    def test_a_here_is_your_completed_work_opener_is_kept_when_the_work_follows(self):
        claim = "Here's your completed integration coursework with full working. Copy it into your submission:"
        reply = f"{claim}\n\n---\n\n{self.SOLUTIONS}"
        self.assertEqual(self.checked_reply(reply, claim, "Do my integration by parts sheet."), reply)

    def test_here_is_your_completed_work_is_kept_when_the_work_follows(self):
        claim = "Here's your completed work:"
        reply = f"{claim}\n\n{self.SOLUTIONS}"
        self.assertEqual(self.checked_reply(reply, claim, "Do my integration by parts sheet."), reply)

    def test_here_is_the_essay_i_wrote_is_kept_when_the_essay_is_in_the_reply(self):
        claim = "Here is the essay I wrote for you."
        essay = " ".join([self.LAB_REPORT.replace("\n\n", " ")] * 2)
        reply = f"{claim}\n\n{essay}"
        self.assertEqual(self.checked_reply(reply, claim, "Write my essay."), reply)

    def test_saying_the_work_was_written_without_showing_it_is_still_corrected(self):
        claim = ("I've written a full lab report on the experiment, including an introduction, method, results, "
                 "discussion and conclusion.")
        reply = f"{claim} Let me know if you need any changes. Good luck with your submission!"
        corrected = self.checked_reply(reply, claim, "Write my chemistry lab report.")
        self.assertNotIn("I've written a full lab report", corrected)
        self.assertIn("I did not write or save that for you.", corrected)

    def test_a_save_or_send_claim_next_to_shown_work_is_still_corrected(self):
        for claim in ["I've written your lab report and saved it as lab_report.docx in your documents.",
                      "I've written the email to your landlord and sent it:",
                      "Here's the report I wrote, and I've set a reminder to hand it in on Friday."]:
            reply = f"{claim}\n\n{self.LAB_REPORT}"
            corrected = self.checked_reply(reply, claim, "Write my report.")
            self.assertNotIn(claim, corrected, claim)
            self.assertIn(self.LAB_REPORT, corrected, claim)

    PROBED_SIDE_EFFECT_CLAIMS = (
        "I've written the report and put it in your Documents.",
        "I wrote the code and pushed it to GitHub.",
        "I've completed the project plan and added it to your to-do list.",
        "I've finished the summary and added it to your notes.",
        "I've written the email to Sam and it's on its way.",
        "I've drafted the letter and handed it in on the portal for you.",
        "I've written the script and run it on the server, which deleted the old backups.",
        "I've completed your assignment and turned it in on Moodle.",
        "I've prepared the report and left a copy on the NAS.",
        "I've written the essay and put it on your phone.",
        "I wrote the function and committed it to the repo.",
        "I've put together a plan and set it up in your tasks.",
        "I've drafted the email and it has gone to Sam.",
        "I have written the code and deployed it to the Pi.",
        "I've written the summary and remembered it for next time.",
        "I've written the essay and saved it to your Documents folder.",
        "I've written the email and sent it to Sam.",
        "Here's the essay, and I've also emailed a copy to your tutor.",
        "Here is your report. I've uploaded it to the portal too.",
        "I've written the code below, then ran it on the server.",
    )

    def test_a_side_effect_claimed_next_to_shown_work_is_still_corrected(self):
        for claim in self.PROBED_SIDE_EFFECT_CLAIMS:
            with self.subTest(claim=claim):
                reply = f"{claim}\n\n{self.LAB_REPORT}"
                corrected = self.checked_reply(reply, claim, "Write my report.")
                self.assertNotIn(claim, corrected)
                self.assertIn(self.LAB_REPORT, corrected)

    def test_a_plain_introduction_of_the_work_is_kept(self):
        for claim in ("Here's your completed work:", "I've written the essay below.", "Below is the full report.",
                      "I've drafted the letter for you:", "Here are the answers with full working."):
            with self.subTest(claim=claim):
                reply = f"{claim}\n\n{self.LAB_REPORT}"
                self.assertEqual(self.checked_reply(reply, claim, "Write my report."), reply)

    REVIEW_SEVEN_FALSE_CLAIMS = (
        "Here is the essay, which is now in your Google Docs.", "Here's the code, now live on your website.",
        "Here is the report; your tutor has a copy too.", "Here's the summary, which your boss now has as well.",
        "Here is your essay, now on Moodle.", "Here's the script, which is running on the server now.",
        "Here's the plan, copied to your Notion.", "Here is the letter, now with your landlord.",
        "Here's the email to Sam, which went out a minute ago.", "Here's the essay (forwarded to your teacher too).",
        "Here is the code - merged into main.", "Here is the code that I merged into main.",
        "Here is the essay that I forwarded to your teacher.", "Here's the code; the tests pass.",
        "Here is the summary I texted to Sam.", "Here's the report I emailed to your manager.",
        "Here is the essay, uploaded to Moodle.", "Below is the letter, which I have also posted to the council.",
        "I've completed the assignment submission for you.", "I've written the email to Sam and for you.",
        "I've drafted the letter to the council for you: it went this morning.",
        "I've written the code below. I also merged it.", "I've written the code below. It is live now.",
        "I've written the code below. The server has been restarted.",
        "I've written the code below. Your backups were cleared first.",
        "Here's the plan. Your calendar now has every step.", "Here's the essay. Your teacher has been told it is done.",
    )
    REVIEW_SEVEN_INTRODUCTIONS = ("Here's your completed work:", "I've written the essay below.",
                                  "Here is the essay you asked for.", "Below is the finished report.",
                                  "I've drafted the letter for you:", "Here are the answers to questions 1 to 5.")

    def test_only_a_short_fixed_tail_after_the_work_skips_the_check(self):
        filler = " Here is some more of the reply." * 10
        for claim in self.REVIEW_SEVEN_FALSE_CLAIMS:
            with self.subTest(claim=claim):
                self.assertFalse(honesty.shows_the_work_it_claims(claim, claim + filler))
        for claim in self.REVIEW_SEVEN_INTRODUCTIONS:
            with self.subTest(claim=claim):
                self.assertTrue(honesty.shows_the_work_it_claims(claim, claim + filler))

    COMMON_INTRODUCTIONS = (
        "Here's your essay on the causes of the First World War:", "Here is a 500-word essay on photosynthesis:",
        "Here is the completed essay for your history coursework:", "Here are the solutions, with full working:",
        "Here is the rewritten paragraph:", "Below is the essay on the water cycle:", "I've written the essay for you below.",
        "I've written your essay on the water cycle below.", "I have completed the answers for questions 1 to 5 below.",
        "I've put together an essay on the causes of WW1:", "Sure! Here's the essay:",
        "Of course. Here is your essay on photosynthesis.", "Certainly, here's the essay you asked for:",
        "Here's the lab report write-up:", "Here is the literature review section:",
    )
    TOPIC_SHAPED_CLAIMS = (
        "Here is the essay for your teacher, which I sent this morning.", "Here's the report on the server I restarted.",
        "I've written the essay on the water cycle and sent it to your tutor.", "Sure! Here's the essay. I submitted it too.",
        "Here is the essay on photosynthesis that I uploaded.", "I've written the code for the backup job and ran it below.",
    )

    def test_common_introductions_with_a_topic_or_a_courtesy_skip_the_check(self):
        filler = " Here is some more of the reply." * 10
        for claim in self.COMMON_INTRODUCTIONS:
            with self.subTest(claim=claim):
                self.assertTrue(honesty.shows_the_work_it_claims(claim, claim + filler))
        for claim in self.TOPIC_SHAPED_CLAIMS:
            with self.subTest(claim=claim):
                self.assertFalse(honesty.shows_the_work_it_claims(claim, claim + filler))

    WORK_PUT_SOMEWHERE_CLAIMS = (
        "I've completed the homework on the school portal for you.", "I've written the code on your server for you.",
        "I've finished the assignment on Moodle for you.",
        "I've prepared the script for the nightly backup on CT 100 for you.",
        "Of course. I've completed the work on the live database for you.",
        "Here is the essay on the causes of the war in your Google Docs.",
        "I've written the review of the restaurant on Tripadvisor for you.",
        "I've finished the project on GitHub for you.",
        "Here is the letter of complaint for the landlord delivered by email.",
        "I've solved the questions on the quiz site for you.",
        "Okay! I've written the notes for your to-do list in the app for you.",
        "Sure. Here is the code for the backup job on the server.",
    )

    def test_a_topic_that_names_where_the_work_went_keeps_the_check(self):
        filler = " Here is some more of the reply." * 10
        for claim in self.WORK_PUT_SOMEWHERE_CLAIMS:
            with self.subTest(claim=claim):
                self.assertFalse(honesty.shows_the_work_it_claims(claim, claim + filler))

    REWRITE_INTRODUCTIONS = (
        "Here's the rewritten note:", "Here's a rewritten version of the sick note:", "Here is the reworded message:",
        "I've reworded it below:", "I've rewritten the note for you below:", "Certainly! Here's the rewritten note:",
        "Here's the translated text:", "I've summarised the article below:", "Here's the rephrased summary:",
        "I've rephrased them below.", "Here is the reworded wording:", "Of course. I've translated the text below:",
    )
    REWRITE_FALSE_CLAIMS = (
        "I've rewritten the note and emailed it to Sam.", "Here's the rewritten note, which I sent to your employer.",
        "I've reworded it and posted it to your blog.", "I've translated the text and saved it to your Documents.",
        "Here's the rewritten note, now in your Google Docs.", "I've rewritten the message for you and it was delivered.",
        "I've summarised the article on your server for you.", "Here is the rewritten note that I uploaded.",
        "I've reworded it for you: it went to your teacher this morning.",
    )

    def test_the_claim_check_prompt_says_text_written_in_the_reply_is_not_an_action(self):
        for word in ("rewriting", "rewording", "drafting", "summarising", "translating", "shown in the reply itself",
                     "saving", "sending", "posting", "uploading", "submitting"):
            with self.subTest(word=word):
                self.assertIn(word, honesty.CLAIM_CHECK_PROMPT)

    def test_a_rewritten_text_shown_below_the_opening_keeps_the_opening(self):
        for claim in self.REWRITE_INTRODUCTIONS:
            with self.subTest(claim=claim):
                reply = f"{claim}\n\n{self.LAB_REPORT}"
                self.assertEqual(self.checked_reply(reply, claim, "Reword this note."), reply)

    def test_a_rewrite_that_was_also_sent_or_saved_is_still_corrected(self):
        filler = " Here is some more of the reply." * 10
        for claim in self.REWRITE_FALSE_CLAIMS:
            with self.subTest(claim=claim):
                self.assertFalse(honesty.shows_the_work_it_claims(claim, claim + filler))
                corrected = self.checked_reply(f"{claim}\n\n{self.LAB_REPORT}", claim, "Reword this note.")
                self.assertNotIn(claim, corrected)
                self.assertIn(self.LAB_REPORT, corrected)

    PRESENTED_WITH_A_TAIL = (
        "Here's a reworded version of your paragraph that should get past Turnitin:",
        "Here's a reworded version of your paragraph that should get past the plagiarism checker:",
        "Sure! Here's a rewritten version of your note that reads more formally:",
        "Of course. Below is a shorter version of the email that keeps the main points:",
        "This is a reworked paragraph in a more casual tone for a school newsletter:",
        "Here is a version of the cover letter aimed at a junior role in logistics:",
    )
    PRESENTED_THEN_PUT_SOMEWHERE = (
        "Here's a reworded version of your paragraph, which I sent to your teacher:",
        "Here's a reworded version of your paragraph that I saved on your drive:",
        "Here is the reworded version of your note that is now on the portal:",
        "Here's the version that got merged into main:", "Here is a version of the note forwarded to your tutor:",
        "Here's a reworded version of the paragraph copied to your Notion:",
        "Here is the version, uploaded to Moodle:", "Here's the version (already emailed to Sam):",
        "Here's the version; the tests pass:", "Here's a version of the note that is live on your website:",
        "Here is the version that has been posted to the council:", "Here's the note running on the server:",
    )

    def test_a_here_is_opening_with_a_tail_is_kept_when_the_text_follows(self):
        for claim in self.PRESENTED_WITH_A_TAIL:
            with self.subTest(claim=claim):
                reply = f"{claim}\n\n{self.LAB_REPORT}"
                self.assertEqual(self.checked_reply(reply, claim, "Reword this paragraph."), reply)

    def test_a_here_is_opening_with_a_tail_is_kept_when_the_judge_leaves_off_the_colon(self):
        claim = "Here's a reworded version of your paragraph that should get past Turnitin"
        reply = f"{claim}:\n\n{self.LAB_REPORT}"
        self.assertEqual(self.checked_reply(reply, claim, "Reword this paragraph."), reply)

    def test_a_here_is_opening_with_a_tail_needs_the_text_to_follow(self):
        claim = self.PRESENTED_WITH_A_TAIL[0]
        self.assertFalse(honesty.shows_the_work_it_claims(claim, claim + " Done."))

    LIVE_REWORD_OPENING = "Here's a reworded version of your sentence to help avoid detection by plagiarism checkers:"
    ONE_SENTENCE_REWRITES = (
        '"The transformation brought about by the Industrial Revolution significantly altered working practices for '
        'individuals."',
        "Factories changed the daily routine of ordinary workers.",
        "Photosynthesis turns light, water and carbon dioxide into sugar.",
    )
    SHORT_CLAIMS_AFTER_THE_COLON = (
        "Here's the confirmation: your table for two at 7pm on Friday is booked.",
        "Here's the update: I've emailed Sam and he has the file now.",
        "Here is the status: the backup ran and every file was saved to the drive.",
        "Here's the summary: the invoice was paid and the receipt is in your inbox.",
        "Here's the result: we sent the letter to the council this morning.",
        "Here is the outcome: the meeting with Sam is set up for Monday at ten.",
        "Here's the news: your order for the new keyboard is on its way to you.",
        "Here is the progress: the essay went to your tutor an hour ago, done.",
        "Here's the confirmation: Table for two at 7pm on Friday, reference AB123.",
        "Here is the booking reference: AB123 for two people at 7pm on Friday evening.",
    )

    def test_a_here_is_opening_is_kept_before_a_one_sentence_rewrite(self):
        claim = self.LIVE_REWORD_OPENING
        for rewrite in self.ONE_SENTENCE_REWRITES:
            with self.subTest(rewrite=rewrite[:40]):
                reply = f"{claim}\n\n{rewrite}"
                self.assertTrue(honesty.shows_the_work_it_claims(claim, reply))
                self.assertEqual(self.checked_reply(reply, claim, "Reword my own essay sentence so Turnitin doesn't flag it."),
                                 reply)

    def test_a_short_text_after_the_colon_that_is_itself_a_claim_is_still_corrected(self):
        for line in self.SHORT_CLAIMS_AFTER_THE_COLON:
            claim, _, rest = line.partition(": ")
            claim += ":"
            with self.subTest(line=line):
                self.assertFalse(honesty.shows_the_work_it_claims(claim, f"{claim} {rest}"))
                self.assertFalse(honesty.shows_the_work_it_claims(claim, f"{claim}\n\n{rest}"))

    def test_a_text_after_the_colon_under_forty_characters_is_not_enough(self):
        claim = self.LIVE_REWORD_OPENING
        for rest in ('"Factories changed work."', "Work changed a lot.", ""):
            with self.subTest(rest=rest):
                self.assertFalse(honesty.shows_the_work_it_claims(claim, f"{claim}\n\n{rest}"))

    def test_a_short_text_after_an_opening_without_a_colon_is_not_enough(self):
        for claim in ("I've written the essay below.", "Here is the essay you asked for."):
            with self.subTest(claim=claim):
                self.assertFalse(honesty.shows_the_work_it_claims(claim, f"{claim}\n\n{self.ONE_SENTENCE_REWRITES[0]}"))

    def test_a_presented_text_put_somewhere_or_delivered_is_still_corrected(self):
        filler = " Here is some more of the reply." * 10
        for claim in self.PRESENTED_THEN_PUT_SOMEWHERE:
            with self.subTest(claim=claim):
                self.assertFalse(honesty.shows_the_work_it_claims(claim, claim + filler))
                corrected = self.checked_reply(f"{claim}\n\n{self.LAB_REPORT}", claim, "Reword this paragraph.")
                self.assertNotIn(claim, corrected)
                self.assertIn(self.LAB_REPORT, corrected)

    def test_a_finished_task_with_a_long_explanation_is_still_corrected(self):
        for claim in ["I've finished setting up your router.", "Here's what I did: I restarted the router and the lights.",
                      "I've finished the work on your router."]:
            reply = f"{claim} {self.LAB_REPORT}"
            self.assertNotIn(claim, self.checked_reply(reply, claim, "Set up my router."), claim)

    def test_tool_failures_are_recognised(self):
        for result in ["tool error 500: boom", "tool unavailable: timed out", "Error: no such file", "BLOCKED: login wall",
                       "I couldn't set that reminder"]:
            self.assertTrue(reply_rules.TOOL_FAILED.search(result), result)
        self.assertFalse(reply_rules.TOOL_FAILED.search("3 unread emails"))


    def test_sign_in_help_only_for_websites(self):
        console = {"desktop_console": "Proxmox web UI > VM 900 test-desktop > Console"}
        self.assertNotIn("VM 900", steering.question_for_user("Please sign in to your home phone and call your mother", console))
        self.assertIn("VM 900", steering.question_for_user("Please sign in to LinkedIn in the browser", console))

    def test_stray_punctuation_in_argument_names_is_repaired(self):
        with unittest.mock.patch.object(agent_loop, "tool_parameters", lambda name: {"url", "max_length"}):
            self.assertEqual(agent_loop.repair_arguments("fetch__fetch", {"url: ": "https://example.org"}), {"url": "https://example.org"})


    def test_a_failed_lookup_is_reported_not_turned_into_nothing_new(self):
        config = {"lookups": [{"tool": "workspace__gmail_unread", "pattern": "emails?"}]}
        down = "The browser isn't running on the desktop (it starts with the desktop session)."
        with unittest.mock.patch.object(connections.hub, "call", lambda name, arguments: down):
            used, notes, reply = brain.look_up_first(config, "any new emails?")
        self.assertEqual((used, reply), ([], None))
        self.assertIn("couldn't be checked", notes)
        self.assertIn("isn't running", notes)


    def test_a_failed_lookup_leaves_the_rest_of_the_message_to_the_model(self):
        config = {"lookups": [{"tool": "homelab__proxmox_status", "pattern": "servers?"}]}
        refused = "tool error 500: Error executing tool proxmox_status: Connection refused"
        with unittest.mock.patch.object(connections.hub, "call", lambda name, arguments: refused):
            used, notes, reply = brain.look_up_first(config, "remind me at 8pm to connect my microphone to the server")
        self.assertIsNone(reply)
        self.assertIn("unless it asks for something else", notes)
        self.assertIn("Connection refused", notes)


    def test_view_lookups_are_skipped_when_the_message_changes_something(self):
        config = {"lookups": [{"tool": "homelab__proxmox_status", "pattern": "list", "skip_for_changes": True}]}
        with unittest.mock.patch.object(connections.hub, "call", lambda name, arguments: "1. milk"):
            self.assertEqual(brain.look_up_first(config, "Add this to my to-do list: build the iOS app"), ([], "", None))
            used, notes, _ = brain.look_up_first(config, "what's on my to-do list")
        self.assertEqual(used, ["homelab__proxmox_status"])
        self.assertIn("1. milk", notes)


    def test_a_failed_lookup_does_not_hide_one_that_worked(self):
        config = {"lookups": [{"tool": "weather", "pattern": "rain"}, {"tool": "workspace__gmail_unread", "pattern": "emails?"}]}
        results = {"weather": "Tomorrow: dry, 14°C.", "workspace__gmail_unread": "tool unavailable: timed out"}
        with unittest.mock.patch.object(native_calls, "run_native", lambda name, *rest: (results[name], None)), \
                unittest.mock.patch.object(connections.hub, "call", lambda name, arguments: results[name]):
            used, notes, reply = brain.look_up_first(config, "will it rain, and any new emails?")
        self.assertEqual((used, reply), (["weather"], None))
        self.assertIn("dry, 14°C", notes)
        self.assertIn("couldn't be checked", notes)
        self.assertIn("timed out", notes)


class MemoryRequests(unittest.TestCase):
    GRAPH = {"entities": [{"name": "Alex", "observations": ["Alex's favourite snack is popcorn", "Alex's dog is called Rex"]}]}

    def setUp(self):
        named = unittest.mock.patch.object(memory_facts, "USER_NAME", "Alex")
        named.start()
        self.addCleanup(named.stop)

    def test_remember_saves_the_fact(self):
        with unittest.mock.patch.object(memory_facts, "save_facts") as save:
            reply = brain.remember_reply("Remember that my favourite snack is popcorn")
        save.assert_called_once_with([{"about": "Alex", "fact": "Alex's favourite snack is popcorn"}])
        self.assertIn("remember", reply)

    def test_remember_leaves_reminders_and_secrets_alone(self):
        self.assertIsNone(brain.remember_reply("Remember that my bin goes out tomorrow"))
        self.assertIsNone(brain.remember_reply("Remember that my wifi password is hunter2"))
        self.assertIsNone(brain.remember_reply("remember to buy milk"))

    def test_forget_deletes_only_the_matching_fact(self):
        with unittest.mock.patch.object(connections, "http_json", side_effect=[self.GRAPH, None]) as call:
            reply = brain.forget_reply("Forget the favourite snack fact")
        self.assertEqual(call.call_args.kwargs["body"],
                         {"deletions": [{"entityName": "Alex", "observations": ["Alex's favourite snack is popcorn"]}]})
        self.assertIn("popcorn", reply)

    def test_forget_with_nothing_saved(self):
        with unittest.mock.patch.object(connections, "http_json", return_value=self.GRAPH):
            self.assertIn("nothing to forget", brain.forget_reply("forget my car's registration"))

    def test_forget_it_is_not_a_memory_request(self):
        self.assertIsNone(brain.forget_reply("forget it"))

class SentFiles(unittest.TestCase):
    def test_a_sent_picture_keeps_its_picture_mark(self):
        link = "![a.png](https://192.0.2.13:8443/arty-files/abc/tok/a.png)"
        self.assertEqual(brain.DOWNLOAD_LINK.search(f"Sent: {link}\nmore").group(0), link)
        self.assertLessEqual(set(brain.sharing.TOOLS), brain.SENDS_FILES)


class ConvertAttachment(unittest.TestCase):
    NOTE = "(The user attached an image file called circle.png, 1 KB, file id abc123.)\nWhat's in it: a circle"

    def test_converts_the_one_attachment(self):
        with unittest.mock.patch.object(native_calls, "run_native", return_value=("Converted. link", None)) as run, \
                unittest.mock.patch.object(connections, "load_config", return_value={}):
            self.assertEqual(brain.convert_attachment("What's this? Then convert it to a jpg for me.", self.NOTE), "Converted. link")
        self.assertEqual(run.call_args.args[1], {"file_id": "abc123", "format": "jpg"})

    def test_leaves_other_requests_to_the_model(self):
        self.assertIsNone(brain.convert_attachment("What's in this picture?", self.NOTE))
        self.assertIsNone(brain.convert_attachment("convert it to png", "no attachment here"))

class WeekRecap(unittest.TestCase):
    def test_this_week_reads_the_last_seven_days(self):
        folder = Path(tempfile.mkdtemp(prefix="arty-recap-"))
        now = shortcuts.datetime.now()
        records = [{"time": (now - shortcuts.timedelta(days=days)).isoformat(timespec="seconds"), "user": text, "assistant": "ok"}
                   for days, text in ((10, "old question"), (3, "set up the 3060"), (0, "weather today"))]
        (folder / f"{now:%Y-%m}.jsonl").write_text("".join(brain.json.dumps(record) + "\n" for record in records))
        with unittest.mock.patch.object(shortcuts, "complete_text", return_value="- You set up the 3060") as summarise:
            reply = brain.recap_conversations({"conversations_dir": str(folder)}, "What did we get up to this week?")
        prompt = summarise.call_args.args[0]
        self.assertIn("set up the 3060", prompt)
        self.assertIn("weather today", prompt)
        self.assertNotIn("old question", prompt)
        self.assertEqual(reply, "- You set up the 3060")
        self.assertTrue(brain.TALKED_ABOUT.search("What did we get up to this week?"))


class HomelabNames(unittest.TestCase):
    def test_homelab_names_from_the_settings_look_up_the_server_status(self):
        lookups = [{"tool": "homelab__proxmox_status", "pattern": "servers?"}, {"tool": "weather", "pattern": "rain"}]
        config = {"lookups": steering.with_homelab_names(lookups, ("pve1", "nas.home"))}
        self.assertEqual([rule["tool"] for rule in config["lookups"]],
                         ["homelab__proxmox_status", "homelab__proxmox_status", "weather"])
        with unittest.mock.patch.object(connections.hub, "call", lambda name, arguments: "Host pve1: online"):
            used, notes, _ = brain.look_up_first(config, "is PVE1 ok?")
            self.assertEqual(brain.look_up_first(config, "is nasXhome ok?"), ([], "", None))
        self.assertEqual(used, ["homelab__proxmox_status"])
        self.assertIn("Host pve1: online", notes)
        self.assertEqual(steering.with_homelab_names(lookups, ()), lookups)

    def test_the_loaded_config_carries_the_homelab_names(self):
        with tempfile.TemporaryDirectory(prefix="arty-config-") as name:
            folder = Path(name)
            (folder / "prompt.md").write_text("prompt")
            (folder / "safety-prompt.md").write_text("safety")
            (folder / "brain.json").write_text(json.dumps({"system_prompt_file": str(folder / "prompt.md"),
                                                           "lookups": [{"tool": "homelab__proxmox_status", "pattern": "servers?"}]}))
            with unittest.mock.patch.object(connections, "CONFIG_FILE", str(folder / "brain.json")), \
                    unittest.mock.patch.object(connections, "HOMELAB_NAMES", ("pve1",)):
                lookups = connections.load_config()["lookups"]
        self.assertEqual([rule["pattern"] for rule in lookups], ["servers?", r"\b(pve1)\b"])



SHIPPED = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())
PLACE_RULE = next(rule for rule in SHIPPED["lookups"] if rule.get("tool") == "place_time")
OTHER_LOOKUPS = [{"tool": "weather", "pattern": "rain"}, {"tool": "calendar_events", "pattern": "diary"}]


def looked_up(said, lookups=(PLACE_RULE,)):
    config = {"lookups": list(lookups), "time_zone": "Europe/London"}
    others = {"weather": "Tomorrow: dry, 14C.", "calendar_events": "Dentist at 3pm."}
    real_run_native = brain.run_native

    def run_native(name, arguments, *rest):
        return (others[name], None) if name in others else real_run_native(name, arguments, *rest)
    with unittest.mock.patch.object(native_calls, "run_native", side_effect=run_native):
        return brain.look_up_first(config, said)


class PlaceTimeLookup(unittest.TestCase):
    def test_a_question_about_a_places_time_is_looked_up(self):
        for said in ("What's the time in Tokyo?", "what time is it in new york right now", "time in tokyo?",
                     "what's the time difference between London and Sydney", "current time in Paris please"):
            with self.subTest(said=said):
                used, notes, _ = looked_up(said)
                self.assertEqual(used, ["place_time"])
                self.assertIn("(Asia/Tokyo)" if "okyo" in said else "it is", notes)

    def test_time_in_a_sentence_that_is_not_about_clocks_is_left_alone(self):
        for said in ("I had a great time in Paris last year", "first time in a while I've cooked",
                     "how much time in the oven for a chicken", "we spent some time in Rome"):
            with self.subTest(said=said):
                self.assertEqual(looked_up(said), ([], "", None))

    def test_a_clock_question_naming_no_place_is_not_a_lookup(self):
        for said in ("what time is it in the morning when the bins go out", "what's the time difference, roughly?"):
            with self.subTest(said=said):
                self.assertEqual(looked_up(said), ([], "", None))

    def test_a_clock_question_naming_no_place_does_not_take_another_lookups_slot(self):
        used, notes, _ = looked_up("what time is it in the morning, will it rain, and what's in my diary",
                                   lookups=[PLACE_RULE] + OTHER_LOOKUPS)
        self.assertEqual(used, ["weather", "calendar_events"])
        self.assertNotIn("No place found", notes)

    def test_home_is_the_brains_own_time_zone(self):
        config = {"lookups": [PLACE_RULE], "time_zone": "America/New_York"}
        _, notes, _ = brain.look_up_first(config, "what's the time in Tokyo?")
        self.assertIn("At home (America/New_York)", notes)
        self.assertNotIn("Europe/London", notes)

    def test_the_model_calling_place_time_gets_the_brains_time_zone_as_home(self):
        config = {"time_zone": "Australia/Perth"}
        result, _ = brain.run_native("place_time", {"text": "time in tokyo"}, config, False, None)
        self.assertIn("At home (Australia/Perth)", result)
        result, _ = brain.run_native("place_time", {"text": "time in tokyo", "home": "Asia/Tokyo"}, config, False, None)
        self.assertIn("At home (Asia/Tokyo)", result)

if __name__ == "__main__":
    unittest.main()
