import io
import json
import os
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import test_review_brain  # noqa: E402,F401
import connections  # noqa: E402
import main_calls  # noqa: E402
import models  # noqa: E402
import screen_review  # noqa: E402
import starfire_rules  # noqa: E402

MAIN = "hf.co/owner/big-a3b-gguf:iq4_xs"
SIDE = "hf.co/owner/side-a3b-gguf:iq4_xs"
SMALL = "qwen3:14b"
FILE_CONFIG = {"model": MAIN, "arty_models": [MAIN, SMALL], "keep_alive": -1, "options": {"num_ctx": 24576}}
TURN_CONFIG = {**FILE_CONFIG, "model": SIDE}
SET_UP = main_calls.MainModel(MAIN, (SMALL,))
CHAT_URL = f"{connections.ARTY_ENGINE_URL}/api/chat"


class Engine:
    def __init__(self, failure=None):
        self.failure = failure
        self.models = []

    def json(self, url, body=None, headers=None, timeout=600):
        self.models.append(body["model"])
        if body["model"] == MAIN and self.failure is not None:
            raise self.failure
        return {"message": {"content": json.dumps({screen_review.VERDICT_FIELD: False})}, "done_reason": "stop"}


class TheSafetyCheckRunsOnTheMain(unittest.TestCase):
    def setUp(self):
        for patch in (unittest.mock.patch.object(main_calls, "read_main", return_value=SET_UP),
                      unittest.mock.patch.object(main_calls, "_card_mates", [lambda: {SIDE}]),
                      unittest.mock.patch.object(models, "is_loaded", side_effect=lambda name: name == SIDE),
                      unittest.mock.patch.object(connections, "load_config", return_value=dict(FILE_CONFIG)),
                      unittest.mock.patch.object(screen_review, "gpu_is_free_in_time", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def looked(self, engine):
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json):
            return screen_review.closer_look(dict(TURN_CONFIG), "a message", "")

    def test_the_check_uses_the_main_while_the_side_model_holds_the_card(self):
        engine = Engine()
        self.assertIs(self.looked(engine), False)
        self.assertEqual(engine.models, [MAIN])

    def test_the_check_never_goes_to_the_side_model_or_the_fallback_when_the_main_fails(self):
        for failure in (urllib.error.HTTPError(CHAT_URL, 500, "error", {}, io.BytesIO(b'{"error": "load failed"}')),
                        urllib.error.URLError(ConnectionRefusedError(111, "refused"))):
            with self.subTest(failure=type(failure).__name__):
                engine = Engine(failure)
                self.assertIsNone(self.looked(engine))
                self.assertNotIn(SIDE, engine.models)
                self.assertNotIn(SMALL, engine.models)

    def test_the_check_gets_time_for_a_whole_model_swap_unscaled(self):
        waits = []

        def timed(url, body=None, headers=None, timeout=600):
            waits.append(timeout)
            return Engine().json(url, body)

        scaled_file = {**FILE_CONFIG, "side_job_seconds_scale": 3}
        with unittest.mock.patch.object(connections, "load_config", return_value=scaled_file), \
                unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=timed):
            for turn, expected in ((TURN_CONFIG, screen_review.SAFETY_CHECK_SECONDS),
                                   ({**TURN_CONFIG, screen_review.SAFETY_CHECK_SETTING: 450}, 450),
                                   ({**TURN_CONFIG, screen_review.SAFETY_CHECK_SETTING: "soon"}, 300)):
                screen_review.closer_look(dict(turn), "a message", "")
                self.assertAlmostEqual(waits[-1], expected, delta=1)
        self.assertEqual(screen_review.SAFETY_CHECK_SECONDS, 300)

    def test_a_check_that_times_out_refuses_what_needed_it(self):
        def slow(url, body=None, headers=None, timeout=600):
            raise TimeoutError("timed out")

        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=slow), \
                unittest.mock.patch.object(screen_review.content_rules, "is_unrestricted", return_value=True), \
                unittest.mock.patch.object(screen_review.content_screen, "needs_a_closer_look", return_value=True), \
                unittest.mock.patch.object(screen_review.content_screen, "unsafe_without_a_judge", return_value=False):
            decided = screen_review.declined(dict(TURN_CONFIG), SIDE, "a message")
        self.assertEqual(decided, ("minors", screen_review.content_screen.CANT_CHECK_REPLY))

    def test_other_judging_jobs_still_share_the_card(self):
        engine = Engine()
        with unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=engine.json), \
                main_calls.sharing_the_card():
            connections.http_json(CHAT_URL, body={"model": MAIN, "messages": [], "format": "json"})
        self.assertEqual(engine.models, [SIDE])


if __name__ == "__main__":
    unittest.main()


class OnlyRealVerdictsAreKept(unittest.TestCase):
    CANT_CHECK = ("minors", screen_review.content_screen.CANT_CHECK_REPLY)
    REFUSED = ("minors", screen_review.content_screen.MINORS_REPLY)

    def setUp(self):
        self.store = starfire_rules.JudgedRequests(starfire_rules.VERDICTS_KEPT)

    def decided(self, *outcomes):
        with unittest.mock.patch.object(screen_review, "declined", side_effect=list(outcomes)) as declined:
            found = [self.store.verdict({}, SIDE, "the same message", "") for _ in outcomes]
        return found, declined.call_count

    def test_a_cant_check_answer_is_checked_again_on_the_next_try(self):
        self.assertEqual(self.decided(self.CANT_CHECK, None), ([self.CANT_CHECK, None], 2))

    def test_a_real_verdict_is_kept(self):
        with unittest.mock.patch.object(screen_review, "declined", side_effect=[self.REFUSED]) as declined:
            found = [self.store.verdict({}, SIDE, "the same message", "") for _ in range(2)]
        self.assertEqual((found, declined.call_count), ([self.REFUSED, self.REFUSED], 1))


class AReservedGpuIsWaitedFor(unittest.TestCase):
    TRAINER = "http://trainer.test:8642"

    def setUp(self):
        self.now, self.events, self.open_requests, self.failing = 0.0, [], set(), {}
        for patch in (unittest.mock.patch.object(screen_review, "gpu_is_reserved", side_effect=self.held_until_asked),
                      unittest.mock.patch.object(screen_review.time, "monotonic", side_effect=lambda: self.now),
                      unittest.mock.patch.object(screen_review.time, "sleep", side_effect=self.slept),
                      unittest.mock.patch.object(screen_review.urllib.request, "urlopen", side_effect=self.told)):
            patch.start()
            self.addCleanup(patch.stop)

    def held_until_asked(self, config):
        return not self.open_requests

    def slept(self, seconds):
        self.now += seconds

    def told(self, request, timeout=None):
        path = request.full_url.removeprefix(self.TRAINER)
        request_id = json.loads(request.data)["id"]
        self.events.append((path, request_id.split("-")[0], timeout))
        if path == "/busy":
            self.open_requests.add(request_id)
        else:
            self.open_requests.discard(request_id)
        if path in self.failing:
            raise self.failing[path]
        return io.BytesIO(b"{}")

    def config(self, **jobs):
        return {**TURN_CONFIG, "jobs": {"gpu_lock": "/nonexistent/lock", **jobs}}

    def judged(self, config, engine=None):
        engine = engine or Engine()

        def asked(url, body=None, headers=None, timeout=600):
            self.events.append(("judge", body["model"], None))
            return engine.json(url, body)

        with unittest.mock.patch.object(connections, "load_config", return_value=dict(FILE_CONFIG)), \
                unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=asked):
            return screen_review.closer_look(config, "a message", "")

    def test_the_trainer_hears_busy_for_the_whole_wait_and_the_check_and_done_after_it(self):
        verdict = self.judged(self.config(trainer_url=f"{self.TRAINER}/"))
        self.assertIs(verdict, False)
        seconds = screen_review.TRAINER_SECONDS
        self.assertEqual(self.events, [("/busy", "safety", seconds), ("judge", MAIN, None), ("/done", "safety", seconds)])
        self.assertLess(self.now, 5)

    def test_a_holder_that_never_yields_hears_busy_once_and_done_after_the_check_gives_up(self):
        with unittest.mock.patch.object(screen_review, "gpu_is_reserved", return_value=True):
            verdict = self.judged(self.config(trainer_url=self.TRAINER))
        self.assertIsNone(verdict)
        self.assertGreaterEqual(self.now, screen_review.SAFETY_CHECK_SECONDS)
        self.assertEqual([event[0] for event in self.events], ["/busy", "/done"])
        self.assertEqual(self.open_requests, set())

    def test_done_is_sent_even_when_busy_timed_out(self):
        self.failing["/busy"] = TimeoutError("timed out")
        with self.assertLogs("brain", "WARNING"):
            verdict = self.judged(self.config(trainer_url=self.TRAINER))
        self.assertIs(verdict, False)
        self.assertEqual([event[0] for event in self.events], ["/busy", "judge", "/done"])
        self.assertEqual(self.open_requests, set())

    def test_a_done_that_fails_is_logged_as_an_error_and_the_verdict_stands(self):
        self.failing["/done"] = urllib.error.URLError("refused")
        with self.assertLogs("brain", "ERROR") as logged:
            verdict = self.judged(self.config(trainer_url=self.TRAINER))
        self.assertIs(verdict, False)
        self.assertIn("check is over", logged.output[0])

    def test_done_is_sent_when_the_check_itself_breaks(self):
        with unittest.mock.patch.object(screen_review.honesty, "ask_judge", side_effect=RuntimeError("broken")), \
                self.assertRaises(RuntimeError):
            self.judged(self.config(trainer_url=self.TRAINER))
        self.assertEqual([event[0] for event in self.events], ["/busy", "/done"])

    def test_a_free_card_never_calls_the_trainer(self):
        with unittest.mock.patch.object(screen_review, "gpu_is_reserved", return_value=False):
            verdict = self.judged(self.config(trainer_url=self.TRAINER))
        self.assertIs(verdict, False)
        self.assertEqual(self.events, [("judge", MAIN, None)])

    def test_without_a_trainer_address_a_held_lock_refuses_without_the_engine(self):
        verdict = self.judged(self.config())
        self.assertIsNone(verdict)
        self.assertGreaterEqual(self.now, screen_review.SAFETY_CHECK_SECONDS)
        self.assertEqual(self.events, [])

    def test_the_wait_and_the_check_share_one_time_limit(self):
        waits = []

        def timed(url, body=None, headers=None, timeout=600):
            waits.append(timeout)
            return Engine().json(url, body)

        def held_for_a_minute(config):
            return self.now < 60

        with unittest.mock.patch.object(screen_review, "gpu_is_reserved", side_effect=held_for_a_minute), \
                unittest.mock.patch.object(connections, "load_config", return_value=dict(FILE_CONFIG)), \
                unittest.mock.patch.object(connections, "engine_or_plain_json", side_effect=timed):
            screen_review.closer_look(self.config(), "a message", "")
        self.assertEqual(waits, [screen_review.SAFETY_CHECK_SECONDS - 60])
