import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import test_review_brain  # noqa: E402,F401
from test_main_calls import ANSWER, CHAT_URL, MAIN, NOT_ANSWERED, SET_UP, SMALL, Engine, http_error  # noqa: E402

import agent_loop  # noqa: E402
import auto_choice  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import engine_guard  # noqa: E402
import main_calls  # noqa: E402
import models  # noqa: E402
import room  # noqa: E402
import room_text  # noqa: E402

SIDE = "hf.co/owner/side-a3b-gguf:iq4_xs"
GENERATE_URL = f"{connections.ARTY_ENGINE_URL}/api/generate"
FILE_CONFIG = {"model": MAIN, "arty_models": [MAIN, SMALL], "keep_alive": -1, "options": {"num_ctx": 24576}}
ENGINE_WAIT_FOR_IDLE_SECONDS = 600
BUSY = ("no room on the GPU for {name}: every loaded model is busy",
        "{name} is still answering another request with different settings; try again when it is done")


class Rules(unittest.TestCase):
    def setUp(self):
        self.pauses, self.now = [], 0.0
        for patch in (unittest.mock.patch.object(main_calls, "read_main", return_value=SET_UP),
                      unittest.mock.patch.object(main_calls, "pause", side_effect=self.paused),
                      unittest.mock.patch.object(main_calls, "clock", side_effect=lambda: self.now),
                      unittest.mock.patch.object(connections, "load_config", return_value=dict(FILE_CONFIG))):
            patch.start()
            self.addCleanup(patch.stop)

    def paused(self, seconds):
        self.pauses.append(seconds)
        self.now += seconds

    def asked(self, engine, url=CHAT_URL, body=None):
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json):
            return connections.http_json(url, body=body or {"model": MAIN, "messages": []})


class TheCardMateIsOptIn(Rules):
    def setUp(self):
        super().setUp()
        for patch in (unittest.mock.patch.object(main_calls, "_card_mates", [lambda: {SIDE}]),
                      unittest.mock.patch.object(models, "is_loaded", side_effect=lambda name: name == SIDE)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_plain_call_and_the_main_reload_stay_on_the_main(self):
        engine = Engine()
        self.asked(engine)
        self.asked(engine, GENERATE_URL, {"model": MAIN, "keep_alive": -1})
        self.assertEqual(engine.models, [MAIN])

    def test_only_a_judging_job_shares_the_card(self):
        engine = Engine()
        with main_calls.sharing_the_card():
            self.asked(engine)
        self.assertEqual(engine.models, [SIDE])

    def test_a_reply_or_a_room_turn_never_shares_the_card(self):
        engine = Engine()
        with main_calls.sharing_the_card(), main_calls.writing_the_reply():
            self.asked(engine)
        with main_calls.sharing_the_card(), \
                unittest.mock.patch.object(engine_guard, "current", return_value=object()):
            self.asked(engine)
        self.assertEqual(engine.models, [MAIN, MAIN])

    def test_a_plain_text_job_stays_on_the_main(self):
        engine = Engine()
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json):
            connections.complete_text("sum up the unread email")
        self.assertEqual(engine.models, [MAIN])


class OnlyRealFailuresStartTheRest(Rules):
    def test_a_busy_card_that_answers_at_once_is_asked_again_and_never_falls_back(self):
        for text in BUSY:
            with self.subTest(text=text[:20]):
                self.pauses.clear()
                failures = [http_error(500, text.format(name=MAIN))]
                engine = Engine()

                def busy_once(url, body=None, headers=None, timeout=600):
                    if failures:
                        engine.models.append(body["model"])
                        raise failures.pop()
                    return engine.json(url, body)

                with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=busy_once):
                    answer = connections.http_json(CHAT_URL, body={"model": MAIN, "messages": []})
                self.assertEqual(answer["message"]["content"], ANSWER)
                self.assertEqual(engine.models, [MAIN, MAIN])
                self.assertEqual(self.pauses, [main_calls.BUSY_PAUSE_SECONDS])

    def test_a_card_that_answers_busy_at_once_is_asked_again_until_the_limit(self):
        asked = []

        def always_busy(url, body=None, headers=None, timeout=600):
            asked.append(body["model"])
            raise http_error(500, BUSY[0].format(name=MAIN))

        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=always_busy), \
                self.assertRaises(Exception):
            connections.http_json(CHAT_URL, body={"model": MAIN, "messages": []})
        self.assertEqual(len(asked), main_calls.BUSY_WAIT_SECONDS // main_calls.BUSY_PAUSE_SECONDS + 1)
        self.assertLessEqual(self.now, main_calls.BUSY_WAIT_SECONDS)

    def test_a_busy_answer_raised_again_by_the_same_error_is_still_read(self):
        engine = Engine(http_error(500, BUSY[0].format(name=MAIN)))
        with self.assertRaises(Exception):
            self.asked(engine)
        self.assertEqual(engine.models, [MAIN] * (main_calls.BUSY_WAIT_SECONDS // main_calls.BUSY_PAUSE_SECONDS + 1))

    def test_a_main_request_meeting_a_long_side_reply_errors_after_the_engine_s_own_ten_minutes(self):
        asked = []

        def busy_after_the_engine_s_own_wait(url, body=None, headers=None, timeout=600):
            asked.append((body["model"], self.now))
            self.now += ENGINE_WAIT_FOR_IDLE_SECONDS
            raise http_error(500, BUSY[0].format(name=MAIN))

        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=busy_after_the_engine_s_own_wait), \
                self.assertRaises(Exception) as raised:
            connections.http_json(CHAT_URL, body={"model": MAIN, "messages": []})
        self.assertTrue(main_calls.is_busy_text(main_calls.said_by(raised.exception)))
        self.assertEqual((asked, self.pauses, self.now), ([(MAIN, 0.0)], [], 600.0))

    def test_a_client_that_has_gone_stops_the_wait(self):
        asked, here = [], [True]

        def busy(url, body=None, headers=None, timeout=600):
            asked.append(body["model"])
            here[0] = len(asked) < 3
            raise http_error(500, BUSY[1].format(name=MAIN))

        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=busy), \
                main_calls.answering_while(lambda: here[0]), self.assertRaises(Exception):
            connections.http_json(CHAT_URL, body={"model": MAIN, "messages": []})
        self.assertEqual(len(asked), 3)

    def test_a_refused_room_call_is_not_a_failure_but_a_server_fault_is(self):
        refused = RuntimeError("model error: the engine answered 404 b'{\"error\":\"model not found\"}'")
        broken = RuntimeError("model error: the engine answered 500 b'{\"error\":\"load failed\"}'")
        busy = RuntimeError("model error: the engine answered 500 b'every loaded model is busy'")
        self.assertEqual([main_calls.failure_of(error)[0] for error in (refused, broken, busy)], [False, True, False])


class TextRepliesSayTheMainCouldNotAnswer(Rules):
    def test_a_plain_text_reply_says_so_and_asks_no_other_model(self):
        engine = Engine(http_error(500, "load failed"))
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json), \
                self.assertRaises(main_calls.MainModelDown) as raised:
            connections.complete_text("sum up the unread email")
        self.assertEqual(engine.models, [MAIN])
        self.assertEqual(str(raised.exception), NOT_ANSWERED)

    def test_the_report_names_the_model_that_was_chosen(self):
        report = auto_choice.turn_report({"model": MAIN, "role": None}, {"answered_by": SMALL}, [])
        self.assertEqual(report["model"], MAIN)
        self.assertNotIn("fallback", report)

    def test_a_reply_after_steps_ran_lists_them_with_the_reason(self):
        engine = Engine(http_error(500, "load failed"))
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json):
            reply = agent_loop.model_reply({"model": MAIN, "messages": []}, dict(FILE_CONFIG), None, None, None, False,
                                           [("calculate", "4")])
        self.assertEqual(engine.models, [MAIN])
        self.assertIn("(the engine answered 500: load failed)", reply["message"]["content"])
        self.assertIn("- calculate: 4", reply["message"]["content"])

    def test_a_room_turn_whose_main_cannot_answer_passes(self):
        down = main_calls.MainModelDown(SET_UP, "load failed")
        with unittest.mock.patch.object(room, "answered_turn", side_effect=down):
            reply = room.room_turn([{"role": "user", "content": "(It's your turn, Arty. Write your next message only, or PASS.)"}])
        self.assertEqual(reply.answer, room_text.PASS)


class AChatJobKnowsWhenItsAppHasGone(unittest.TestCase):
    def test_a_job_nobody_polls_any_more_counts_as_gone(self):
        jobs, now = brain.ChatJobs(), [1000.0]
        with unittest.mock.patch.object(brain.time, "time", side_effect=lambda: now[0]):
            jobs.jobs["job"] = {"status": "running", "created": now[0]}
            jobs.get("job")
            seen_while_polled = jobs.is_watched("job")
            now[0] += brain.CLIENT_GONE_SECONDS + 1
            seen_after_the_app_went = jobs.is_watched("job")
            jobs.get("job")
            seen_once_it_polls_again = jobs.is_watched("job")
        self.assertEqual((seen_while_polled, seen_after_the_app_went, seen_once_it_polls_again), (True, False, True))
        self.assertNotIn("polled", jobs.get("job"))

    def test_the_turn_runs_while_the_job_is_watched(self):
        jobs, seen = brain.ChatJobs(), []

        def turn(*_args, **_named):
            seen.append(main_calls.client_has_gone())
            return "Done.", [], {}, None

        with unittest.mock.patch.object(brain, "run_turn", side_effect=turn), \
                unittest.mock.patch.object(jobs, "is_watched", return_value=False):
            jobs._run("job", [], "Arty", "")
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
