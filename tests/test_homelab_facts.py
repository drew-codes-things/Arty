import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "homelab-facts"
FAKE_TOOLS = {
    "apt": "#!/bin/sh\necho 'Listing...'\necho 'openssl/stable-security 3.0.1 amd64 [upgradable from: 3.0.0]'\n",
    "pvesm": "#!/bin/sh\necho 'Name Type Status Total Used Available %'\n",
    "pvesh": "#!/bin/sh\nexit 0\n",
}


class HostFacts(unittest.TestCase):
    def run_facts(self, folder: Path, **settings) -> subprocess.CompletedProcess:
        tools = folder / "bin"
        tools.mkdir(exist_ok=True)
        for name, body in FAKE_TOOLS.items():
            (tools / name).write_text(body)
            (tools / name).chmod(0o755)
        env = {key: value for key, value in os.environ.items() if key != "HOST_FACTS_FILE"}
        env.update({"PATH": f"{tools}:{os.environ['PATH']}", **settings})
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)

    def test_the_facts_go_to_the_configured_file(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            target = folder / "state" / "host-facts.json"
            result = self.run_facts(folder, HOST_FACTS_FILE=str(target))
            self.assertEqual(result.returncode, 0, result.stderr)
            facts = json.loads(target.read_text())
        self.assertEqual(facts["updates"], {"count": 1, "security": 1})
        self.assertEqual(facts["backups"], {})

    def test_without_a_configured_file_nothing_is_written(self):
        with TemporaryDirectory() as name:
            result = self.run_facts(Path(name))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HOST_FACTS_FILE", result.stderr)


if __name__ == "__main__":
    unittest.main()
