import io
import json
import os
import sys
import time
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_engine_errors import APPEND, turn_with  # noqa: E402
from test_review_brain import Server  # noqa: E402
from test_review_tonight import FakeHub, calls  # noqa: E402
from test_round_timings import asked  # noqa: E402
from test_window_overflow import ENGINE_CONTEXT_OVERFLOW, WINDOW, engine_refusal, with_window  # noqa: E402

import agent_loop  # noqa: E402
import background_tasks  # noqa: E402
import brain  # noqa: E402
import connections  # noqa: E402
import store  # noqa: E402

ENTRIES = ("auto", "Arty")
OLDER_ENGINE_WORDING = "the request exceeds the context size"
PROMPT_TOKENS_SHOWN = "30,120"
WINDOW_SHOWN = "24,576"
LONG_MESSAGE = "summarise this for me: " + "a. " * 400
JOB_POLLS = 200
JOB_POLL_SECONDS = 0.05


def refusing(message, status=400):
    def refuse(*args, **kwargs):
        raise engine_refusal(message, status)
    return refuse


def refusing_mid_stream(message):
    def refuse(*args, **kwargs):
        raise RuntimeError(f"model error: {message}")
    return refuse


def tracebacks(records):
    return [record for record in records if record.exc_info]


class TooLongOverHttp(Server):
    def setUp(self):
        super().setUp()
        with_window(self)

    def engine_refuses(self, message=ENGINE_CONTEXT_OVERFLOW, status=400, streamed=None):
        unittest.mock.patch.object(connections, "http_json", side_effect=refusing(message, status)).start()
        unittest.mock.patch.object(connections, "stream_chat", side_effect=streamed or refusing(message, status)).start()

    def chat(self, model, stream=False):
        body = {"model": model, "stream": stream, "messages": asked(LONG_MESSAGE)}
        return self.request("POST", "/v1/chat/completions", body=body)

    def streamed_text(self, raw):
        chunks = [json.loads(line[6:]) for line in raw.decode().split("\n\n") if line.startswith("data: {")]
        return "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks), chunks

    def job_outcome(self, model):
        status, body = self.request("POST", "/v1/chat/jobs", body={"model": model, "messages": asked(LONG_MESSAGE)})
        self.assertEqual(status, 202)
        job_id = json.loads(body)["id"]
        for _ in range(JOB_POLLS):
            job = json.loads(self.request("GET", f"/v1/chat/jobs/{job_id}")[1])
            if job["status"] != "running":
                return job
            time.sleep(JOB_POLL_SECONDS)
        self.fail("the chat job never finished")

    def assert_says_too_long(self, text):
        self.assertIn("too long for me to read in one go", text)
        self.assertIn(PROMPT_TOKENS_SHOWN, text)
        self.assertIn(WINDOW_SHOWN, text)
        self.assertIn("shorter part", text)
        self.assertNotIn(brain.GENERIC_ERROR, text)

    def test_a_plain_chat_on_auto_and_on_the_arty_entry_says_the_message_is_too_long(self):
        self.engine_refuses()
        for model in ENTRIES:
            with self.subTest(model=model):
                status, raw = self.chat(model)
                self.assertEqual(status, 200)
                self.assert_says_too_long(json.loads(raw)["choices"][0]["message"]["content"])

    def test_a_streamed_chat_says_it_and_ends_the_stream_properly(self):
        self.engine_refuses()
        for model in ENTRIES:
            with self.subTest(model=model):
                status, raw = self.chat(model, stream=True)
                text, chunks = self.streamed_text(raw)
                self.assertEqual(status, 200)
                self.assert_says_too_long(text)
                self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
                self.assertTrue(raw.endswith(b"data: [DONE]\n\n"))

    def test_a_chat_job_finishes_with_the_clear_answer(self):
        self.engine_refuses()
        for model in ENTRIES:
            with self.subTest(model=model):
                job = self.job_outcome(model)
                self.assertEqual(job["status"], "done")
                self.assert_says_too_long(job["answer"])

    def test_the_log_gets_one_warning_line_and_no_traceback(self):
        self.engine_refuses()
        with self.assertLogs("brain", level="WARNING") as logged:
            self.chat("Arty")
            self.job_outcome("auto")
        self.assertEqual(tracebacks(logged.records), [])
        too_long = [record for record in logged.records if "30120" in record.getMessage()]
        self.assertEqual(len(too_long), 2)
        self.assertTrue(all(record.levelname == "WARNING" for record in too_long))

    def test_a_message_the_model_never_read_is_not_archived_or_mined_for_memories(self):
        self.engine_refuses()
        self.chat("Arty")
        brain.archive_turn.assert_not_called()
        brain.extract_memories.assert_not_called()

    def test_the_older_engine_wording_names_the_window_the_brain_asked_for(self):
        self.engine_refuses(OLDER_ENGINE_WORDING)
        status, raw = self.chat("Arty")
        text = json.loads(raw)["choices"][0]["message"]["content"]
        self.assertEqual(status, 200)
        self.assertIn("too long for me to read in one go", text)
        self.assertIn(WINDOW_SHOWN, text)
        self.assertNotIn("None", text)

    def test_a_refusal_sent_inside_the_stream_counts_too(self):
        self.engine_refuses(streamed=refusing_mid_stream(ENGINE_CONTEXT_OVERFLOW))
        status, raw = self.chat("auto", stream=True)
        self.assertEqual(status, 200)
        self.assert_says_too_long(self.streamed_text(raw)[0])

    def test_a_turn_handled_by_the_local_add_on_gets_the_same_answer(self):
        too_long = agent_loop.MessageTooLong(WINDOW, 30120)
        with unittest.mock.patch.object(brain, "local_addon", unittest.mock.Mock(**{"turn.side_effect": too_long})):
            status, raw = self.chat("Arty")
        self.assertEqual(status, 200)
        self.assert_says_too_long(json.loads(raw)["choices"][0]["message"]["content"])

    def test_other_engine_refusals_keep_their_current_handling(self):
        self.engine_refuses("invalid JSON: Expecting value", status=400)
        status, raw = self.chat("Arty")
        self.assertEqual(status, 500)
        self.assertIn(brain.GENERIC_ERROR, raw.decode())
        self.assertNotIn("too long", raw.decode())


class TooLongAfterStepsRan(unittest.TestCase):
    def test_the_answer_names_the_steps_that_ran_instead_of_failing_the_turn(self):
        outcome, hub, _ = turn_with([calls(APPEND), engine_refusal(ENGINE_CONTEXT_OVERFLOW)])
        self.assertEqual(len(hub.calls), 1)
        self.assertEqual(outcome["status"], "done")
        self.assertIn(APPEND[0], outcome["answer"])
        self.assertIn("did not fit", outcome["answer"])
        self.assertNotIn("too long for me to read in one go", outcome["answer"])


class TooLongBackgroundTask(unittest.TestCase):
    def setUp(self):
        store.init()

    def test_the_task_fails_with_the_clear_reason_and_no_traceback(self):
        task = store.create_task("Summarise", LONG_MESSAGE, "api")
        config = {"model": "m", "options": {"num_ctx": WINDOW}}
        with unittest.mock.patch.object(connections, "hub", FakeHub()), \
                unittest.mock.patch.object(connections, "http_json", side_effect=refusing(ENGINE_CONTEXT_OVERFLOW)), \
                unittest.mock.patch.object(connections, "load_config", return_value=config), \
                unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
                unittest.mock.patch.object(brain.models, "find", return_value=None), \
                unittest.mock.patch.object(agent_loop, "system_prompt", return_value=""), \
                unittest.mock.patch.object(agent_loop, "current_context", return_value=""), \
                unittest.mock.patch.object(store, "claim_next_task", return_value=store.get_task(task["id"])), \
                unittest.mock.patch.object(background_tasks.notify, "send") as sent, \
                self.assertLogs("brain", level="WARNING") as logged:
            background_tasks.worker_step()
        failed = store.get_task(task["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("too long for me to read in one go", failed["result"])
        self.assertIn(PROMPT_TOKENS_SHOWN, failed["result"])
        self.assertEqual(tracebacks(logged.records), [])
        self.assertIn("too long for me to read in one go", sent.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
