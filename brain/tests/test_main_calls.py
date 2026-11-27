import io
import json
import os
import re
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_round_timings import WARM, offline_turns  # noqa: E402

import auto_choice  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import main_calls  # noqa: E402

MAIN = "hf.co/owner/big-a3b-gguf:iq4_xs"
SMALL = "qwen3:14b"
CHAT_URL = f"{connections.ARTY_ENGINE_URL}/api/chat"
SET_UP = main_calls.MainModel(MAIN, (SMALL,), {MAIN: "Big 35B"})
ANSWER = "Here is the answer."
NOT_ANSWERED = ("The main model couldn't answer just now (the engine answered 500: load failed). Try again in a moment, "
                "or pick qwen3:14b in the model list if you want a different model.")


def http_error(status: int, text: str) -> urllib.error.HTTPError:
    body = json.dumps({"error": text}).encode()
    return urllib.error.HTTPError(CHAT_URL, status, "error", {}, io.BytesIO(body))


class Engine:
    def __init__(self, failure=None, answer=ANSWER, text_before_failure=""):
        self.failure = failure
        self.answer = answer
        self.text_before_failure = text_before_failure
        self.models = []

    def reply(self, body):
        self.models.append(body["model"])
        if body["model"] == MAIN and self.failure is not None:
            raise self.failure
        return {"message": {"role": "assistant", "content": self.answer}, "done_reason": "stop", **WARM}

    def json(self, url, body=None, headers=None, timeout=600):
        if not url.endswith("/api/chat"):
            return {}
        return self.reply(body)

    def stream(self, body, on_text, timeout=600, on_thinking=None):
        if body["model"] == MAIN and self.text_before_failure:
            on_text(self.text_before_failure)
        reply = self.reply(body)
        for piece in re.findall(r"\S+\s*", reply["message"]["content"]):
            on_text(piece)
        return reply


class FailsOnce(Engine):
    def reply(self, body):
        try:
            return super().reply(body)
        finally:
            self.failure = None


class SetUp(unittest.TestCase):
    def setUp(self):
        patch = unittest.mock.patch.object(main_calls, "read_main", return_value=SET_UP)
        patch.start()
        self.addCleanup(patch.stop)


class TheMainIsNeverHandedToAnotherModel(SetUp):
    def sent(self, engine, model=MAIN):
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json):
            return connections.http_json(CHAT_URL, body={"model": model, "messages": [], "keep_alive": -1})

    def replied(self, engine):
        with main_calls.writing_the_reply():
            return self.sent(engine)

    def test_a_side_job_on_an_engine_that_cannot_be_reached_gets_the_engine_s_error(self):
        engine = Engine(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
        with self.assertRaises(urllib.error.URLError) as raised:
            self.sent(engine)
        self.assertNotIsInstance(raised.exception, main_calls.MainModelDown)
        self.assertEqual(engine.models, [MAIN])

    def test_a_reply_that_fails_to_load_says_the_main_could_not_answer(self):
        engine = Engine(http_error(500, "load failed"))
        with self.assertRaises(main_calls.MainModelDown) as raised:
            self.replied(engine)
        self.assertEqual(str(raised.exception), NOT_ANSWERED)
        self.assertEqual(engine.models, [MAIN])

    def test_a_reply_on_an_engine_that_cannot_be_reached_names_that_reason(self):
        engine = Engine(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
        with self.assertRaises(main_calls.MainModelDown) as raised:
            self.replied(engine)
        self.assertIn("(the AI engine could not be reached)", str(raised.exception))
        self.assertEqual(engine.models, [MAIN])

    def test_an_engine_that_keeps_resetting_the_call_is_asked_twice_then_said_so(self):
        engine = Engine(ConnectionResetError(104, "Connection reset by peer"))
        with self.assertRaises(main_calls.MainModelDown):
            self.replied(engine)
        self.assertEqual(engine.models, [MAIN, MAIN])

    def test_one_reset_is_tried_again_on_the_main_before_any_text(self):
        calls = []

        def reset_once(url, body=None, headers=None, timeout=600):
            calls.append(body["model"])
            if len(calls) == 1:
                raise ConnectionResetError(104, "Connection reset by peer")
            return {"message": {"role": "assistant", "content": ANSWER}, "done_reason": "stop"}
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=reset_once):
            reply = connections.http_json(CHAT_URL, body={"model": MAIN, "messages": []})
        self.assertEqual(calls, [MAIN, MAIN])
        self.assertEqual(reply["message"]["content"], ANSWER)

    def test_a_reset_after_streamed_text_is_never_sent_again(self):
        engine = Engine(ConnectionResetError(104, "Connection reset by peer"), text_before_failure="Half ")
        heard = []
        with unittest.mock.patch.object(connections, "engine_stream", side_effect=engine.stream), \
                main_calls.writing_the_reply(), self.assertRaises(ConnectionResetError):
            connections.stream_chat({"model": MAIN, "messages": []}, heard.append)
        self.assertEqual(engine.models, [MAIN])

    def test_other_models_are_never_redirected(self):
        engine = Engine(http_error(500, "boom"))
        self.sent(engine, model="side-model:9b")
        self.assertEqual(engine.models, ["side-model:9b"])

    def test_a_picked_small_model_that_fails_keeps_its_own_error(self):
        def broken(url, body=None, headers=None, timeout=600):
            raise http_error(500, "boom")
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=broken), \
                main_calls.writing_the_reply(), self.assertRaises(urllib.error.HTTPError) as raised:
            connections.http_json(CHAT_URL, body={"model": SMALL, "messages": []})
        self.assertNotIsInstance(raised.exception, main_calls.MainModelDown)

    def test_after_a_failure_the_next_call_asks_the_main_again(self):
        engine = Engine(http_error(500, "load failed"))
        with self.assertRaises(main_calls.MainModelDown):
            self.replied(engine)
        engine.failure = None
        self.replied(engine)
        self.assertEqual(engine.models, [MAIN, MAIN])

    def test_a_setting_still_naming_a_fallback_is_ignored(self):
        found = main_calls.main_from({"model": MAIN, "fallback_model": SMALL})
        engine = Engine(http_error(500, "load failed"))
        with unittest.mock.patch.object(main_calls, "read_main", return_value=found), \
                self.assertRaises(main_calls.MainModelDown) as raised:
            self.replied(engine)
        self.assertEqual(engine.models, [MAIN])
        self.assertTrue(str(raised.exception).endswith("Try again in a moment."), str(raised.exception))

    def test_tool_and_hub_calls_never_read_the_settings(self):
        with unittest.mock.patch.object(connections, "engine_or_plain_json", return_value={"ok": True}), \
                unittest.mock.patch.object(main_calls, "read_main") as read:
            connections.http_json("http://127.0.0.1:8000/memory/read_graph", body={})
        read.assert_not_called()


class NothingIsSaidForSlownessOrAWrongRequest(SetUp):
    def sent(self, engine):
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json), \
                main_calls.writing_the_reply():
            return connections.http_json(CHAT_URL, body={"model": MAIN, "messages": []})

    def test_a_slow_first_token_is_waited_for_not_replaced(self):
        for slow in (TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))):
            with self.subTest(slow=slow):
                engine = Engine(slow)
                with self.assertRaises((TimeoutError, urllib.error.URLError)) as raised:
                    self.sent(engine)
                self.assertNotIsInstance(raised.exception, main_calls.MainModelDown)
                self.assertEqual(engine.models, [MAIN])

    def test_a_message_too_long_for_the_window_keeps_its_readable_error(self):
        engine = Engine(http_error(500, "the request exceeds the available context size"))
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.sent(engine)
        self.assertEqual(engine.models, [MAIN])
        self.assertIn("exceeds the available context", json.loads(raised.exception.read())["error"])

    def test_a_refused_request_is_not_a_model_failure(self):
        engine = Engine(http_error(400, "bad request"))
        with self.assertRaises(urllib.error.HTTPError):
            self.sent(engine)
        self.assertEqual(engine.models, [MAIN])


class StreamedReplies(SetUp):
    def streamed(self, engine):
        heard = []
        with unittest.mock.patch.object(connections, "engine_stream", side_effect=engine.stream), \
                main_calls.writing_the_reply():
            reply = connections.stream_chat({"model": MAIN, "messages": []}, heard.append)
        return reply, "".join(heard)

    def test_an_error_line_before_any_text_says_the_main_could_not_answer(self):
        engine = Engine(RuntimeError("model error: the runner exited while loading"))
        with self.assertRaises(main_calls.MainModelDown) as raised:
            self.streamed(engine)
        self.assertIn("(the runner exited while loading)", str(raised.exception))
        self.assertEqual(engine.models, [MAIN])

    def test_after_text_has_streamed_the_error_is_raised_not_replaced(self):
        engine = Engine(RuntimeError("model error: runner stopped"), text_before_failure="Half an ")
        with self.assertRaises(RuntimeError) as raised:
            self.streamed(engine)
        self.assertNotIsInstance(raised.exception, main_calls.MainModelDown)
        self.assertEqual(engine.models, [MAIN])


class TheReplySaysTheMainCouldNotAnswer(SetUp):
    def setUp(self):
        super().setUp()
        offline_turns(self)

    def turn(self, engine, on_text=lambda _: None):
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json), \
                unittest.mock.patch.object(connections, "engine_stream", side_effect=engine.stream), \
                unittest.mock.patch.object(brain, "chat_model", return_value=f"arty/{MAIN}"), \
                unittest.mock.patch.object(brain, "use_requested_model",
                                           side_effect=lambda config, model, *_, **__: config.update(model=MAIN)):
            return brain.run_turn([{"role": "user", "content": "what is the capital of France?"}], "Arty", on_text=on_text)

    def test_a_turn_whose_main_fails_answers_plainly_and_asks_no_other_model(self):
        engine = Engine(http_error(500, "load failed"))
        reply, tools_used, raw, calls = self.turn(engine)
        self.assertEqual((reply, tools_used, calls), (NOT_ANSWERED, [], None))
        self.assertEqual(set(engine.models), {MAIN})
        self.assertNotIn("fallback", auto_choice.turn_report({"model": MAIN}, raw, tools_used))

    def test_one_server_fault_from_the_main_is_asked_again_on_the_main(self):
        engine = FailsOnce(http_error(500, "load failed"))
        reply, _, _, _ = self.turn(engine)
        self.assertIn(ANSWER, reply)
        self.assertEqual(engine.models, [MAIN, MAIN])

    def test_an_engine_that_cannot_be_reached_is_not_asked_again(self):
        engine = Engine(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
        reply, _, _, _ = self.turn(engine)
        self.assertTrue(reply.startswith("The main model couldn't answer just now"), reply)
        self.assertEqual(engine.models, [MAIN])

    def test_a_reply_from_the_main_is_unchanged(self):
        reply, _, _, _ = self.turn(Engine())
        self.assertIn(ANSWER, reply)
        self.assertNotIn("couldn't answer", reply)

    def test_a_chat_job_shows_the_plain_answer(self):
        jobs = brain.ChatJobs()
        engine = Engine(http_error(500, "load failed"))
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json), \
                unittest.mock.patch.object(connections, "engine_stream", side_effect=engine.stream), \
                unittest.mock.patch.object(brain, "chat_model", return_value=f"arty/{MAIN}"), \
                unittest.mock.patch.object(brain, "use_requested_model",
                                           side_effect=lambda config, model, *_, **__: config.update(model=MAIN)):
            jobs.jobs["job"] = {"status": "running", "created": 0}
            jobs._run("job", [{"role": "user", "content": "what is the capital of France?"}], "Arty", "")
        self.assertEqual((jobs.jobs["job"]["status"], jobs.jobs["job"]["answer"]), ("done", NOT_ANSWERED))


SIDE = "hf.co/owner/side-a3b-gguf:iq4_xs"


class SideJobsUseWhicheverBigModelIsLoaded(SetUp):
    def setUp(self):
        super().setUp()
        self.loaded = set()
        for patch in (unittest.mock.patch.object(main_calls, "_card_mates", [lambda: {SIDE}]),
                      unittest.mock.patch.object(main_calls.models, "is_loaded", side_effect=lambda name: name in self.loaded)):
            patch.start()
            self.addCleanup(patch.stop)

    def sent(self, engine):
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json):
            connections.http_json(CHAT_URL, body={"model": MAIN, "messages": []})

    def test_a_judging_job_runs_on_the_side_model_while_it_holds_the_card(self):
        self.loaded = {SIDE}
        engine = Engine()
        with main_calls.sharing_the_card():
            self.sent(engine)
        self.assertEqual(engine.models, [SIDE])

    def test_a_side_job_stays_on_the_main_while_the_main_is_loaded(self):
        self.loaded = {MAIN, SIDE}
        engine = Engine()
        self.sent(engine)
        self.assertEqual(engine.models, [MAIN])

    def test_with_nothing_loaded_the_main_is_loaded(self):
        engine = Engine()
        self.sent(engine)
        self.assertEqual(engine.models, [MAIN])

    def test_a_reply_to_the_owner_on_the_main_always_uses_the_main(self):
        self.loaded = {SIDE}
        engine = Engine()
        with main_calls.writing_the_reply():
            self.sent(engine)
        self.assertEqual(engine.models, [MAIN])

    def test_a_broken_card_mate_list_is_logged_and_skipped(self):
        def broken():
            raise ValueError("bad config")
        self.loaded = {SIDE}
        with unittest.mock.patch.object(main_calls, "_card_mates", [broken]):
            engine = Engine()
            self.sent(engine)
        self.assertEqual(engine.models, [MAIN])


class PicturesNeverGoToAnotherModel(SetUp):
    def test_a_picture_the_main_cannot_take_is_not_sent_to_the_picture_helper(self):
        found = main_calls.main_from({"model": MAIN, "arty_models": [MAIN, SMALL], "vision_model": "looker:7b"})
        engine = Engine(http_error(500, "load failed"))
        picture = {"model": MAIN, "messages": [{"role": "user", "content": "what is this?", "images": ["aW1n"]}]}
        with unittest.mock.patch.object(main_calls, "read_main", return_value=found), \
                unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json), \
                main_calls.writing_the_reply(), self.assertRaises(main_calls.MainModelDown):
            connections.http_json(CHAT_URL, body=picture)
        self.assertEqual(engine.models, [MAIN])


class MainFromSettings(unittest.TestCase):
    def test_the_main_needs_a_name(self):
        self.assertIsNone(main_calls.main_from({"model": "  "}))
        self.assertIsNone(main_calls.main_from({"model": 7}))
        self.assertIsNone(main_calls.main_from(["model"]))
        self.assertEqual(main_calls.main_from({"model": MAIN}).name, MAIN)

    def test_the_other_arty_models_are_offered_by_their_picker_names(self):
        found = main_calls.main_from({"model": MAIN, "arty_models": [MAIN, SMALL, "big:9b:latest", 4, SMALL],
                                      "model_labels": {"big:9b:latest": "Big 9B", MAIN: "Main"}})
        self.assertEqual(found.others, (SMALL, "big:9b:latest"))
        self.assertIn("pick qwen3:14b or Big 9B in the model list", main_calls.not_answered_text(found, "x"))

    def test_odd_labels_are_ignored(self):
        found = main_calls.main_from({"model": MAIN, "model_labels": {MAIN: 5}})
        self.assertEqual(main_calls.label(found, MAIN), MAIN)

    def test_the_main_is_matched_with_or_without_latest(self):
        found = main_calls.MainModel("big:35b")
        self.assertTrue(main_calls.names_the_main(found, "big:35b:latest"))
        self.assertFalse(main_calls.names_the_main(found, "big:35"))

    def test_a_long_or_odd_reason_is_cut_to_one_plain_line(self):
        reason = main_calls.reason_of(RuntimeError("model error: line one\nline\ttwo " + "x" * 400))
        self.assertTrue(reason.startswith("line one line two "))
        self.assertLessEqual(len(reason), main_calls.REASON_CHARS)


if __name__ == "__main__":
    unittest.main()
