import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy.sh"
LIVE_FOLDER = "/srv/ai/brain"
SETTINGS_BLOCK = re.compile(r"^live_model=.*?^fi$", re.MULTILINE | re.DOTALL)
BIG = "hf.co/owner/big-a3b-gguf:iq4_xs"


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
class LiveSettingsSurviveADeploy(unittest.TestCase):
    def deployed(self, live: dict, shipped: dict) -> dict:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / "live").mkdir()
        (folder / "deploy" / "brain").mkdir(parents=True)
        (folder / "live" / "brain.json").write_text(json.dumps(live))
        (folder / "deploy" / "brain" / "brain.json").write_text(json.dumps(shipped))
        block = SETTINGS_BLOCK.search(DEPLOY.read_text()).group(0)
        block = "\n".join(line for line in block.splitlines() if "own-apps.json" not in line)
        script = block.replace(LIVE_FOLDER, str(folder / "live"))
        subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=folder, check=True)
        return json.loads((folder / "live" / "brain.json").read_text())

    def test_live_labels_are_kept_over_the_shipped_ones(self):
        live = {"model": BIG, "model_labels": {BIG: "Live name", "side:35b": "Side name"}}
        shipped = {"model": "qwen3:14b", "model_labels": {BIG: "Shipped name", "other:9b": "Other"}}
        result = self.deployed(live, shipped)
        self.assertEqual(result["model"], BIG)
        self.assertEqual(result["model_labels"], {BIG: "Live name", "side:35b": "Side name", "other:9b": "Other"})

    def test_a_live_file_without_labels_takes_the_shipped_ones(self):
        result = self.deployed({"model": BIG}, {"model": "qwen3:14b", "model_labels": {BIG: "Shipped name"}})
        self.assertEqual(result["model_labels"], {BIG: "Shipped name"})


if __name__ == "__main__":
    unittest.main()
