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


class Honesty(unittest.TestCase):
    DESKTOP = ("Here's what I see on my desktop right now. I've opened a browser window showing an article on Medium. "
               "The background has a subtle grid pattern and the theme is dark.")

    def setUp(self):
        config = unittest.mock.patch.object(brain, "load_config", return_value={"model": "qwen3:14b"})
        config.start()
        self.addCleanup(config.stop)

    def judged(self, claims, kind="A"):
        def judge(url, body):
            prompt = body["messages"][0]["content"]
            verdict = {"kind": kind} if prompt.startswith("The person said") else {"false_claims": claims}
            return {"message": {"content": brain.json.dumps(verdict)}}
        return unittest.mock.patch.object(brain, "http_json", side_effect=judge)

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
        with unittest.mock.patch.object(brain, "http_json", side_effect=[claims, OSError("ollama down")]):
            self.assertEqual(brain.be_honest("Done.", [], []), brain.HONEST_NOTHING_DONE)

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
            self.assertEqual(brain.be_honest("Les lumières sont allumées.", [], []), brain.HONEST_NOTHING_DONE)

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
        record = brain.tools_record(used, failed)
        self.assertIn("- computer_edit_file: worked", record)
        self.assertIn("- computer_run_command: ran 2 times, 1 failed", record)

    def test_a_failed_check_leaves_the_reply_alone(self):
        with unittest.mock.patch.object(brain, "http_json", side_effect=OSError("ollama down")):
            self.assertEqual(brain.be_honest("Done, all sorted.", [], []), "Done, all sorted.")

    def test_tool_failures_are_recognised(self):
        for result in ["tool error 500: boom", "tool unavailable: timed out", "Error: no such file", "BLOCKED: login wall",
                       "I couldn't set that reminder"]:
            self.assertTrue(brain.TOOL_FAILED.search(result), result)
        self.assertFalse(brain.TOOL_FAILED.search("3 unread emails"))


    def test_sign_in_help_only_for_websites(self):
        console = {"desktop_console": "Proxmox web UI > VM 900 test-desktop > Console"}
        self.assertNotIn("VM 900", brain.question_for_user("Please sign in to your home phone and call your mother", console))
        self.assertIn("VM 900", brain.question_for_user("Please sign in to LinkedIn in the browser", console))

    def test_stray_punctuation_in_argument_names_is_repaired(self):
        with unittest.mock.patch.object(brain, "tool_parameters", lambda name: {"url", "max_length"}):
            self.assertEqual(brain.repair_arguments("fetch__fetch", {"url: ": "https://example.org"}), {"url": "https://example.org"})


    def test_a_failed_lookup_is_reported_not_turned_into_nothing_new(self):
        config = {"lookups": [{"tool": "workspace__gmail_unread", "pattern": "emails?"}]}
        down = "The browser isn't running on the desktop (it starts with the desktop session)."
        with unittest.mock.patch.object(brain.hub, "call", lambda name, arguments: down):
            used, notes, reply = brain.look_up_first(config, "any new emails?")
        self.assertEqual((used, reply), ([], None))
        self.assertIn("couldn't be checked", notes)
        self.assertIn("isn't running", notes)


    def test_a_failed_lookup_leaves_the_rest_of_the_message_to_the_model(self):
        config = {"lookups": [{"tool": "homelab__proxmox_status", "pattern": "servers?"}]}
        refused = "tool error 500: Error executing tool proxmox_status: Connection refused"
        with unittest.mock.patch.object(brain.hub, "call", lambda name, arguments: refused):
            used, notes, reply = brain.look_up_first(config, "remind me at 8pm to connect my microphone to the server")
        self.assertIsNone(reply)
        self.assertIn("unless it asks for something else", notes)
        self.assertIn("Connection refused", notes)


    def test_view_lookups_are_skipped_when_the_message_changes_something(self):
        config = {"lookups": [{"tool": "homelab__proxmox_status", "pattern": "list", "skip_for_changes": True}]}
        with unittest.mock.patch.object(brain.hub, "call", lambda name, arguments: "1. milk"):
            self.assertEqual(brain.look_up_first(config, "Add this to my to-do list: build the iOS app"), ([], "", None))
            used, notes, _ = brain.look_up_first(config, "what's on my to-do list")
        self.assertEqual(used, ["homelab__proxmox_status"])
        self.assertIn("1. milk", notes)


    def test_a_failed_lookup_does_not_hide_one_that_worked(self):
        config = {"lookups": [{"tool": "weather", "pattern": "rain"}, {"tool": "workspace__gmail_unread", "pattern": "emails?"}]}
        results = {"weather": "Tomorrow: dry, 14°C.", "workspace__gmail_unread": "tool unavailable: timed out"}
        with unittest.mock.patch.object(brain, "run_native", lambda name, *rest: (results[name], None)), \
                unittest.mock.patch.object(brain.hub, "call", lambda name, arguments: results[name]):
            used, notes, reply = brain.look_up_first(config, "will it rain, and any new emails?")
        self.assertEqual((used, reply), (["weather"], None))
        self.assertIn("dry, 14°C", notes)
        self.assertIn("couldn't be checked", notes)
        self.assertIn("timed out", notes)


class MemoryRequests(unittest.TestCase):
    GRAPH = {"entities": [{"name": "Alex", "observations": ["Alex's favourite snack is popcorn", "Alex's dog is called Rex"]}]}

    def setUp(self):
        named = unittest.mock.patch.object(brain, "USER_NAME", "Alex")
        named.start()
        self.addCleanup(named.stop)

    def test_remember_saves_the_fact(self):
        with unittest.mock.patch.object(brain, "save_facts") as save:
            reply = brain.remember_reply("Remember that my favourite snack is popcorn")
        save.assert_called_once_with([{"about": "Alex", "fact": "Alex's favourite snack is popcorn"}])
        self.assertIn("remember", reply)

    def test_remember_leaves_reminders_and_secrets_alone(self):
        self.assertIsNone(brain.remember_reply("Remember that my bin goes out tomorrow"))
        self.assertIsNone(brain.remember_reply("Remember that my wifi password is hunter2"))
        self.assertIsNone(brain.remember_reply("remember to buy milk"))

    def test_forget_deletes_only_the_matching_fact(self):
        with unittest.mock.patch.object(brain, "http_json", side_effect=[self.GRAPH, None]) as call:
            reply = brain.forget_reply("Forget the favourite snack fact")
        self.assertEqual(call.call_args.kwargs["body"],
                         {"deletions": [{"entityName": "Alex", "observations": ["Alex's favourite snack is popcorn"]}]})
        self.assertIn("popcorn", reply)

    def test_forget_with_nothing_saved(self):
        with unittest.mock.patch.object(brain, "http_json", return_value=self.GRAPH):
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
        with unittest.mock.patch.object(brain, "run_native", return_value=("Converted. link", None)) as run, \
                unittest.mock.patch.object(brain, "load_config", return_value={}):
            self.assertEqual(brain.convert_attachment("What's this? Then convert it to a jpg for me.", self.NOTE), "Converted. link")
        self.assertEqual(run.call_args.args[1], {"file_id": "abc123", "format": "jpg"})

    def test_leaves_other_requests_to_the_model(self):
        self.assertIsNone(brain.convert_attachment("What's in this picture?", self.NOTE))
        self.assertIsNone(brain.convert_attachment("convert it to png", "no attachment here"))

class WeekRecap(unittest.TestCase):
    def test_this_week_reads_the_last_seven_days(self):
        folder = Path(tempfile.mkdtemp(prefix="arty-recap-"))
        now = brain.datetime.now()
        records = [{"time": (now - brain.timedelta(days=days)).isoformat(timespec="seconds"), "user": text, "assistant": "ok"}
                   for days, text in ((10, "old question"), (3, "set up the 3060"), (0, "weather today"))]
        (folder / f"{now:%Y-%m}.jsonl").write_text("".join(brain.json.dumps(record) + "\n" for record in records))
        with unittest.mock.patch.object(brain, "complete_text", return_value="- You set up the 3060") as summarise:
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
        config = {"lookups": brain.with_homelab_names(lookups, ("pve1", "nas.home"))}
        self.assertEqual([rule["tool"] for rule in config["lookups"]],
                         ["homelab__proxmox_status", "homelab__proxmox_status", "weather"])
        with unittest.mock.patch.object(brain.hub, "call", lambda name, arguments: "Host pve1: online"):
            used, notes, _ = brain.look_up_first(config, "is PVE1 ok?")
            self.assertEqual(brain.look_up_first(config, "is nasXhome ok?"), ([], "", None))
        self.assertEqual(used, ["homelab__proxmox_status"])
        self.assertIn("Host pve1: online", notes)
        self.assertEqual(brain.with_homelab_names(lookups, ()), lookups)

    def test_the_loaded_config_carries_the_homelab_names(self):
        with tempfile.TemporaryDirectory(prefix="arty-config-") as name:
            folder = Path(name)
            (folder / "prompt.md").write_text("prompt")
            (folder / "brain.json").write_text(json.dumps({"system_prompt_file": str(folder / "prompt.md"),
                                                           "lookups": [{"tool": "homelab__proxmox_status", "pattern": "servers?"}]}))
            with unittest.mock.patch.object(brain, "CONFIG_FILE", str(folder / "brain.json")), \
                    unittest.mock.patch.object(brain, "HOMELAB_NAMES", ("pve1",)):
                lookups = brain.load_config()["lookups"]
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
    with unittest.mock.patch.object(brain, "run_native", side_effect=run_native):
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
