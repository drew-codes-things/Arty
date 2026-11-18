import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_brain import Server  # noqa: E402

import auto_choice  # noqa: E402
import brain  # noqa: E402
import coding  # noqa: E402
import connections  # noqa: E402
import media  # noqa: E402
import model_choice  # noqa: E402
import models  # noqa: E402
import run_requests  # noqa: E402

CONFIG = {"model": "qwen3:14b", "coding_model": "coder:9b", "vision_model": "looker:7b", "persona": "You are Arty.",
          "system_prompt": "Use your tools.", "options": {"num_ctx": 24576}, "tool_groups": {}, "approval": {}}
QWEN = {"name": "qwen3:14b", "billions": 14.0, "tools": True, "vision": False, "embedding": False, "thinking": True,
        "context_length": 40960}
CODER = {**QWEN, "name": "coder:9b", "billions": 9.0, "context_length": 16384}
LOOKER = {**QWEN, "name": "looker:7b", "billions": 7.0, "vision": True, "context_length": 0}
INSTALLED = [QWEN, CODER, LOOKER]
PICTURE_PART = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}}
STORED = {"vid1": {"kind": "video"}, "aud1": {"kind": "audio"}, "img1": {"kind": "image"}, "doc1": {"kind": "document"}}
ENGINE_COUNTS = {"prompt_eval_count": 1200, "prompt_eval_cached_count": 1100, "eval_count": 60, "eval_duration": 2_000_000_000}

CODE_ASKS = ["write a python function that reverses a string", "Traceback (most recent call last):\n  File \"a.py\"\nNameError: x"]
AGENT_ASKS = ["remind me to call the dentist at 3pm", "what's the weather like tomorrow?", "turn off the lights in the lounge",
              "play some music in the kitchen", "what's on my calendar today?", "text mum that I'm running late",
              "any news headlines this morning?", "set a timer for ten minutes"]
CHAT_ASKS = ["tell me a story about a fox", "what's the capital of Norway?", "how are you today?", ""]


def found(name):
    return next((model for model in INSTALLED if model["name"] == name or model["name"] == f"{name}:latest"), None)


def user(content):
    return [{"role": "user", "content": content}]


def file_part(file_id):
    return {"type": "file", "file": {"file_id": file_id, "filename": file_id}}


def role_of(said, messages):
    return auto_choice.role_for(said, messages, auto_choice.asks_for_code(said))


class RoleRules(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.object(media, "get", side_effect=STORED.get)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_picture_or_video_in_the_latest_message_goes_to_the_vision_role(self):
        for content in ([{"type": "text", "text": "write a python function"}, PICTURE_PART], [file_part("vid1")], [file_part("img1")]):
            with self.subTest(content=content):
                self.assertEqual(role_of("write a python function", user(content)), auto_choice.VISION)

    def test_a_picture_in_an_earlier_message_does_not_count(self):
        messages = user([PICTURE_PART]) + [{"role": "assistant", "content": "A cat."}] + user("tell me a story")
        self.assertEqual(role_of("tell me a story", messages), auto_choice.CHAT)

    def test_a_voice_note_goes_to_the_agent_role(self):
        self.assertEqual(role_of("", user([file_part("aud1")])), auto_choice.AGENT)

    def test_code_heavy_asks_go_to_the_code_role(self):
        for said in CODE_ASKS:
            with self.subTest(said=said):
                self.assertEqual(role_of(said, user(said)), auto_choice.CODE)

    def test_phone_home_reminder_calendar_music_weather_and_news_asks_go_to_the_agent_role(self):
        for said in AGENT_ASKS:
            with self.subTest(said=said):
                self.assertEqual(role_of(said, user(said)), auto_choice.AGENT)

    def test_everything_else_goes_to_the_chat_role(self):
        for said in CHAT_ASKS:
            with self.subTest(said=said):
                self.assertEqual(role_of(said, user(said)), auto_choice.CHAT)

    def test_a_document_is_not_a_picture(self):
        self.assertEqual(role_of("summarise this", user([file_part("doc1")])), auto_choice.CHAT)

    def test_only_the_auto_id_asks_for_auto(self):
        self.assertTrue(auto_choice.asked_for("auto"))
        for requested in ("Arty", "", "arty/qwen3:14b", "Auto", "solo/x"):
            with self.subTest(requested=requested):
                self.assertFalse(auto_choice.asked_for(requested))


class Numbers(unittest.TestCase):
    def test_usage_numbers_come_from_the_engine_counts(self):
        self.assertEqual(auto_choice.usage_numbers(ENGINE_COUNTS), {
            "prompt_tokens": 1200, "completion_tokens": 60, "context_tokens": 1260, "cached_tokens": 1100, "tokens_per_second": 30.0})

    def test_unknown_numbers_are_left_out(self):
        self.assertEqual(auto_choice.usage_numbers({}), {})
        self.assertEqual(auto_choice.usage_numbers(None), {})
        self.assertEqual(auto_choice.usage_numbers({"prompt_eval_count": 10, "eval_count": 0}),
                         {"prompt_tokens": 10, "completion_tokens": 0, "context_tokens": 10})

    def test_the_trained_context_is_read_from_the_model_info(self):
        self.assertEqual(models.trained_context({"general.architecture": "qwen3", "qwen3.context_length": 40960}), 40960)
        self.assertEqual(models.trained_context({"qwen3.context_length": "big"}), 0)
        self.assertEqual(models.trained_context({}), 0)

    def test_the_window_is_the_smaller_of_the_asked_and_the_trained_size(self):
        with unittest.mock.patch.object(models, "installed", return_value=INSTALLED):
            self.assertEqual(auto_choice.context_window(CONFIG, "qwen3:14b"), 24576)
            self.assertEqual(auto_choice.context_window(CONFIG, "coder:9b"), 16384)
            self.assertEqual(auto_choice.context_window(CONFIG, "looker:7b"), 24576)

    def test_the_report_holds_the_pick_steps_and_usage(self):
        chosen = {"model": "qwen3:14b", "role": "chat", "auto": True, "context_window": 24576}
        report = auto_choice.turn_report(chosen, ENGINE_COUNTS, ["weather", "calendar"])
        self.assertEqual(report["steps"], 2)
        self.assertEqual(report["usage"]["context_tokens"], 1260)
        self.assertEqual({key: report[key] for key in chosen}, chosen)
        self.assertEqual(auto_choice.turn_report(None, {}, None), {"steps": 0})

    def test_the_listing_names_the_models_for_each_job(self):
        listed = auto_choice.listing(CONFIG)
        self.assertEqual((listed["id"], listed["name"]), ("auto", "Arty Auto"))
        self.assertIn("looker:7b", listed["description"])
        self.assertIn("coder:9b", listed["description"])
        self.assertEqual(listed["roles"], ["vision", "code", "agent", "chat"])


class CountingEngine:
    def __init__(self):
        self.bodies = []

    def __call__(self, url, body=None, **_):
        if not url.endswith("/api/chat"):
            return {}
        self.bodies.append(body)
        return {"message": {"role": "assistant", "content": "Here you go."}, "done_reason": "stop", **ENGINE_COUNTS}


def run_offline(case):
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    case.config = {**CONFIG, "conversations_dir": folder.name}
    case.engine = CountingEngine()
    case.make_room = unittest.mock.Mock(return_value=[])
    patches = [
        unittest.mock.patch.object(run_requests, "judge_says_run", return_value=False),
        unittest.mock.patch.object(connections, "load_config", side_effect=lambda: dict(case.config)),
        unittest.mock.patch.object(connections, "http_json", side_effect=case.engine),
        unittest.mock.patch.object(connections.hub, "refresh"),
        unittest.mock.patch.object(connections.hub, "tools", []),
        unittest.mock.patch.object(connections.hub, "routes", {}),
        unittest.mock.patch.object(brain, "archive_turn"),
        unittest.mock.patch.object(brain, "extract_memories"),
        unittest.mock.patch.object(brain, "look_up_first", return_value=([], "", None)),
        unittest.mock.patch.object(brain, "be_honest", side_effect=lambda answer, *_: answer),
        unittest.mock.patch.object(brain, "expand_attachments", side_effect=lambda messages, *_: (messages, False)),
        unittest.mock.patch.object(brain, "researched_answer", side_effect=AssertionError("went to web research")),
        unittest.mock.patch.object(model_choice, "schedule_main_reload"),
        unittest.mock.patch.object(models, "make_room", case.make_room),
        unittest.mock.patch.object(models, "find", side_effect=found),
        unittest.mock.patch.object(models, "installed", return_value=INSTALLED),
        unittest.mock.patch.object(models, "arty_models", return_value=[]),
        unittest.mock.patch.object(models, "is_loaded", return_value=True),
        unittest.mock.patch.object(media, "get", side_effect=STORED.get),
    ]
    for patch in patches:
        patch.start()
    case.addCleanup(unittest.mock.patch.stopall)


class AutoTurns(unittest.TestCase):
    def setUp(self):
        run_offline(self)

    def turn(self, content, requested="auto"):
        picks = []
        answer, tools_used, raw, _ = brain.run_turn(user(content), requested, on_model=picks.append)
        return picks, raw

    def test_each_role_runs_on_its_model_and_is_reported(self):
        cases = (("write a python function that reverses a string", "coder:9b", "code"),
                 ([{"type": "text", "text": "what is this?"}, PICTURE_PART], "looker:7b", "vision"),
                 ([{"type": "text", "text": "what happens here?"}, file_part("vid1")], "looker:7b", "vision"),
                 ("tell me a story about a fox", "qwen3:14b", "chat"),
                 ("play some music in the kitchen", "qwen3:14b", "agent"))
        for content, model, role in cases:
            with self.subTest(role=role, content=content):
                self.engine.bodies.clear()
                picks, raw = self.turn(content)
                self.assertEqual(self.engine.bodies[-1]["model"], model)
                self.assertEqual(picks, [{"model": model, "role": role, "auto": True,
                                          "context_window": auto_choice.context_window(self.config, model)}])
                self.assertEqual(raw["eval_count"], ENGINE_COUNTS["eval_count"])

    def test_the_main_entry_is_reported_without_a_role_and_routes_as_before(self):
        picks, _ = self.turn("tell me a story about a fox", requested="Arty")
        self.assertEqual(picks, [{"model": "qwen3:14b", "role": None, "auto": False, "context_window": 24576}])
        self.assertEqual(self.engine.bodies[-1]["model"], "qwen3:14b")

    def test_auto_keeps_to_the_brains_routes_when_they_are_switched_off(self):
        self.config.update(route_coding_chats=False, route_picture_chats=False)
        picks, _ = self.turn("write a python function that reverses a string")
        self.assertEqual((picks[0]["model"], picks[0]["role"]), ("qwen3:14b", "code"))
        picks, _ = self.turn([{"type": "text", "text": "what is this?"}, PICTURE_PART])
        self.assertEqual((picks[0]["model"], picks[0]["role"]), ("qwen3:14b", "vision"))

    def test_auto_never_runs_a_model_outside_the_allowed_list(self):
        with unittest.mock.patch.object(models, "arty_models", return_value=["qwen3:14b"]):
            picks, _ = self.turn("write a python function that reverses a string")
        self.assertEqual(picks[0]["model"], "qwen3:14b")
        self.assertEqual(self.engine.bodies[-1]["model"], "qwen3:14b")

    def test_a_short_coding_question_keeps_the_brains_no_swap_rule(self):
        with unittest.mock.patch.object(models, "is_loaded", return_value=False):
            picks, _ = self.turn("write a python function that reverses a string")
        self.assertEqual((picks[0]["model"], picks[0]["role"]), ("qwen3:14b", "code"))
        self.make_room.assert_not_called()

    def test_an_auto_chat_still_drafts_and_researches_like_the_main_entry(self):
        with unittest.mock.patch.object(brain, "researched_answer", return_value=("Oslo [1]", ["web_search"])) as research, \
                unittest.mock.patch.object(brain.research, "is_quick_question", return_value=True), \
                unittest.mock.patch.object(brain, "matches_lookup", return_value=False):
            answer, tools_used, _, _ = brain.run_turn(user("what's the best pizza in Oslo?"), "auto")
        research.assert_called_once()
        self.assertEqual(tools_used, ["web_search"])


def fake_turn(*args, on_model=None, on_text=None, **kwargs):
    if on_model:
        on_model({"model": "coder:9b", "role": "code", "auto": True, "context_window": 16384})
    if on_text:
        on_text("Here ")
    return "Here you go.", ["read_file"], dict(ENGINE_COUNTS), None


class Api(Server):
    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.object(brain, "run_turn", side_effect=fake_turn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def chat(self, stream):
        body = {"model": "auto", "stream": stream, "messages": user("write a python function")}
        return self.request("POST", "/v1/chat/completions", body=body)

    def test_a_plain_reply_carries_the_pick_and_the_numbers(self):
        status, body = self.chat(False)
        reply = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(reply["arty"], {"model": "coder:9b", "role": "code", "auto": True, "context_window": 16384, "steps": 1,
                                         "usage": auto_choice.usage_numbers(ENGINE_COUNTS)})
        self.assertEqual(reply["usage"]["prompt_tokens_details"], {"cached_tokens": 1100})
        self.assertEqual(reply["model"], "Arty")

    def test_the_last_streamed_chunk_carries_the_pick_and_the_numbers(self):
        status, body = self.chat(True)
        chunks = [json.loads(line[6:]) for line in body.decode().splitlines() if line.startswith("data: {")]
        self.assertEqual(status, 200)
        self.assertEqual([chunk for chunk in chunks if "arty" in chunk], [chunks[-1]])
        self.assertEqual(chunks[-1]["arty"]["role"], "code")
        self.assertEqual(chunks[-1]["arty"]["usage"]["tokens_per_second"], 30.0)
        self.assertEqual(chunks[-1]["usage"]["total_tokens"], 1260)

    def test_a_chat_job_reports_the_pick_and_the_numbers(self):
        status, body = self.request("POST", "/v1/chat/jobs", body={"model": "auto", "messages": user("write a python function")})
        job_id = json.loads(body)["id"]
        for _ in range(100):
            job = json.loads(self.request("GET", f"/v1/chat/jobs/{job_id}")[1])
            if job["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(status, 202)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["arty"]["model"], "coder:9b")
        self.assertEqual(job["arty"]["role"], "code")
        self.assertEqual(job["arty"]["steps"], 1)
        self.assertEqual(job["arty"]["usage"]["cached_tokens"], 1100)
        self.assertEqual(job["tools_used"], ["read_file"])

    def test_a_running_job_shows_the_pick_before_the_answer(self):
        jobs = brain.ChatJobs()
        jobs.jobs["j"] = {"status": "running", "created": time.time()}
        jobs._set_model("j", {}, {"model": "looker:7b", "role": "vision", "auto": True, "context_window": 24576})
        self.assertEqual(jobs.get("j")["arty"]["role"], "vision")


class ModelList(Server):
    def test_the_list_offers_auto_beside_the_unchanged_models(self):
        with unittest.mock.patch.object(models, "installed", return_value=INSTALLED), \
                unittest.mock.patch.object(models, "arty_models", return_value=[]), \
                unittest.mock.patch.object(models, "toolless_allowed", return_value=False), \
                unittest.mock.patch.object(models, "solo_models", return_value=[]), \
                unittest.mock.patch.object(brain, "local_addon", None), \
                unittest.mock.patch.object(connections, "load_config", return_value=dict(CONFIG)):
            status, body = self.request("GET", "/v1/models")
        listing = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(listing["auto"]["id"], "auto")
        self.assertEqual(listing["auto"]["name"], "Arty Auto")
        self.assertNotIn("auto", [entry["id"] for entry in listing["data"]])
        self.assertEqual(listing["data"][0]["id"], "Arty")


QUICK_PICK_SECONDS = 1.0
MIXED_TEXT_CHARS = 470_000
MIXED_PIECES = ("Here is a question about the garden and the weather this week.\n", "hi" + "\n" * 3000 + "there\n",
                "a." * 2000 + "\n", " \n" * 1500, "what " * 800 + "\n", "how in " * 500 + "\n", "def grow(x):\n    return x\n",
                "Traceback (most recent call last):\n", "src/app.py:12:5: error expected\n", "    at run (main.js:3:9)\n")


def mixed_text(size):
    block = "".join(MIXED_PIECES)
    return (block * (size // len(block) + 1))[:size]


LONG_ODD_MESSAGES = {"blank lines between two words": "hi" + "\n" * 64000 + "there", "a dotted run": "a." * 32000,
                     "470 KB of mixed text": mixed_text(MIXED_TEXT_CHARS)}
CHATTY_PADDING = "Tell me how the week went and what we planned for the garden. " * 120
FENCED_ASK = "```python\nprint(1)\n```\n"


def cpu_seconds(work):
    started = time.process_time()
    work()
    return time.process_time() - started


class LongMessagesArePickedQuickly(unittest.TestCase):
    def setUp(self):
        run_offline(self)

    def test_arty_auto_picks_a_role_and_model_quickly(self):
        for name, said in LONG_ODD_MESSAGES.items():
            with self.subTest(name):
                self.assertLess(cpu_seconds(lambda: auto_choice.choose(self.config, said, user(said))), QUICK_PICK_SECONDS)

    def test_the_arty_entry_picks_its_model_quickly(self):
        for name, said in LONG_ODD_MESSAGES.items():
            with self.subTest(name):
                self.assertLess(cpu_seconds(lambda: model_choice.chat_model(self.config, "Arty", said)), QUICK_PICK_SECONDS)

    def test_the_code_check_is_quick_on_a_whole_long_message(self):
        for name, said in LONG_ODD_MESSAGES.items():
            with self.subTest(name):
                self.assertLess(cpu_seconds(lambda: coding.is_request(said)), QUICK_PICK_SECONDS)

    def test_auto_checks_for_code_once_per_message(self):
        said = "write a python function that reverses a string"
        with unittest.mock.patch.object(coding, "is_request", wraps=coding.is_request) as check:
            self.assertEqual(auto_choice.choose(self.config, said, user(said)), (auto_choice.CODE, "arty/coder:9b"))
        self.assertEqual(check.call_count, 1)

    def test_only_the_start_and_end_of_a_long_message_are_classified(self):
        cases = ((FENCED_ASK + CHATTY_PADDING, auto_choice.CODE), (CHATTY_PADDING + FENCED_ASK, auto_choice.CODE),
                 (CHATTY_PADDING + FENCED_ASK + CHATTY_PADDING, auto_choice.CHAT))
        for said, role in cases:
            with self.subTest(role=role, starts=said[:10]):
                self.assertEqual(auto_choice.choose(self.config, said, user(said))[0], role)


if __name__ == "__main__":
    unittest.main()
