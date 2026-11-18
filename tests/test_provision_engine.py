import os
import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from test_ai_scripts import PULLABLE, TOOLS_MODEL, FakeEngine

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision.sh"
EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_STEP = re.compile(r'^echo "== CT \$CT: the embedding model[^\n]*\npct exec \$CT -- (?:env [^\n]*? )?bash -s <<\'INNER\''
                            r'[^\n]*\n(.*?)^INNER$', re.S | re.M)
FAKE_PCT = '#!/bin/bash\nshift 3\nexec env -i PATH="$PATH" ARTY_ENGINE_URL="$ARTY_ENGINE_URL" "$@"\n'
SEARCH_STEP = re.compile(r'^echo "== CT \$CT: SearXNG[^\n]*\npct exec \$CT -- bash -s <<\'INNER\'\n(.*?)^INNER$', re.S | re.M)
SEARXNG_FOLDER = "/srv/ai/searxng"
DOWN_ENGINE_URL = "http://127.0.0.1:9"


def step_body(pattern: re.Pattern) -> str:
    found = pattern.search(SCRIPT.read_text())
    if not found:
        raise AssertionError(f"provision.sh has no step matching {pattern.pattern[:40]}")
    return found.group(1)


class EmbeddingModelStep(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine([TOOLS_MODEL])
        self.addCleanup(self.engine.stop)
        self.env = {**os.environ, "ARTY_ENGINE_URL": self.engine.url}
        PULLABLE.setdefault(EMBEDDING_MODEL, {"name": EMBEDDING_MODEL, "size": 270_000_000, "capabilities": ["embedding"]})

    def run_step(self) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-s"], input=step_body(EMBEDDING_STEP), env=self.env, capture_output=True,
                              text=True, timeout=60)

    def test_a_missing_model_is_pulled_once(self):
        finished = self.run_step()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual([EMBEDDING_MODEL], [body["model"] for body in self.engine.posted("/api/pull")])
        self.assertIn(EMBEDDING_MODEL, self.engine.models)

    def test_an_installed_model_is_not_pulled_again(self):
        self.engine.models[f"{EMBEDDING_MODEL}:latest"] = {**PULLABLE[EMBEDDING_MODEL], "name": f"{EMBEDDING_MODEL}:latest"}
        finished = self.run_step()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual([], self.engine.posted("/api/pull"))
        self.assertIn("already installed", finished.stdout)

    def test_an_engine_that_is_down_is_named_and_the_run_goes_on(self):
        self.env["ARTY_ENGINE_URL"] = DOWN_ENGINE_URL
        finished = self.run_step()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("isn't answering", finished.stdout)
        self.assertIn(f"ai-pull {EMBEDDING_MODEL}", finished.stdout)

    def test_a_busy_engine_is_named_and_the_run_goes_on(self):
        self.engine.pull_mode = "busy"
        finished = self.run_step()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("another pull is running", finished.stdout)
        self.assertIn(f"ai-pull {EMBEDDING_MODEL}", finished.stdout)

    def test_a_pull_past_its_time_limit_says_the_engine_may_still_be_downloading(self):
        self.engine.pull_mode = "slow"
        self.env["EMBEDDING_PULL_SECONDS"] = "1"
        finished = self.run_step()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("may still be downloading", finished.stdout)
        self.assertIn(f"ai-pull {EMBEDDING_MODEL}", finished.stdout)

    def test_an_offline_engine_gives_its_reason_and_the_run_goes_on(self):
        PULLABLE.pop(EMBEDDING_MODEL)
        finished = self.run_step()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("was not found in the model library", finished.stdout)

    def test_the_time_limit_set_on_the_host_reaches_the_container(self):
        self.engine.pull_mode = "slow"
        with TemporaryDirectory() as folder:
            tools = Path(folder)
            (tools / "pct").write_text(FAKE_PCT)
            (tools / "pct").chmod(0o755)
            step = EMBEDDING_STEP.search(SCRIPT.read_text()).group(0)
            finished = subprocess.run(["bash", "-s"], input=f"CT=104\n{step}\n", capture_output=True, text=True, timeout=60,
                                      env={**self.env, "PATH": f"{tools}:{os.environ['PATH']}", "EMBEDDING_PULL_SECONDS": "1"})
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("may still be downloading", finished.stdout)

    def test_the_step_has_its_own_heading_and_a_failure_never_stops_provisioning(self):
        text = SCRIPT.read_text()
        self.assertRegex(text, r"(?m)^pct exec \$CT -- (?:env [^\n]*? )?bash -s <<'INNER' \|\| echo \"   WARNING: [^\n]*ai-pull "
                               r"nomic-embed-text")
        self.assertNotIn("/api/pull", step_body(SEARCH_STEP))


class SearchStepNeedsTheDockerBridge(unittest.TestCase):
    def test_an_empty_bridge_address_stops_before_writing_the_settings(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            tools = root / "bin"
            tools.mkdir()
            (tools / "ip").write_text("#!/bin/sh\nexit 0\n")
            (tools / "docker").write_text("#!/bin/sh\nexit 0\n")
            for tool in tools.iterdir():
                tool.chmod(0o755)
            body = step_body(SEARCH_STEP).replace(SEARXNG_FOLDER, str(root / "searxng"))
            finished = subprocess.run(["bash", "-s"], input=body, capture_output=True, text=True, timeout=60,
                                      env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"})
            self.assertNotEqual(0, finished.returncode)
            self.assertIn("docker0 has no IPv4 address", finished.stderr)
            self.assertFalse((root / "searxng" / "settings.yml").exists())


if __name__ == "__main__":
    unittest.main()
