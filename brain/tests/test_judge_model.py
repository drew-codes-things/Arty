import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402
import connections  # noqa: E402

MAIN = {"model": "qwen3:14b", "keep_alive": -1}


class JudgeModel(unittest.TestCase):
    def setUp(self):
        patcher = unittest.mock.patch.object(connections, "load_config", return_value=MAIN)
        patcher.start()
        self.addCleanup(patcher.stop)

    def judged_models(self, **be_honest_options):
        def judge(url, body):
            verdict = {"kind": "A"} if body["messages"][0]["content"].startswith("The person said") else \
                {"false_claims": [{"text": "Done.", "correction": ""}]}
            return {"message": {"content": brain.json.dumps(verdict)}}
        with unittest.mock.patch.object(connections, "http_json", side_effect=judge) as call:
            brain.be_honest("Done.", [], [], request="Turn the lights off.", **be_honest_options)
        return [(c.kwargs["body"]["model"], c.kwargs["body"]["keep_alive"]) for c in call.call_args_list]

    def test_the_judge_runs_on_the_model_that_wrote_the_turn(self):
        used = self.judged_models(model="qwen3.5:9b", keep_alive="3m")
        self.assertEqual(used, [("qwen3.5:9b", "3m"), ("qwen3.5:9b", "3m")])

    def test_without_a_turn_model_the_judge_runs_on_the_main_model(self):
        self.assertEqual(self.judged_models(), [("qwen3:14b", -1), ("qwen3:14b", -1)])


if __name__ == "__main__":
    unittest.main()
