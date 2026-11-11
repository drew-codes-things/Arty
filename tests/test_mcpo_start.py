import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin" / "mcpo-start"
UNIT = ROOT / "services" / "mcpo.service"
FAKE_MCPO = '''import json, os, sys
def app(**options):
    print(json.dumps({"options": options, "argv": sys.argv[1:], "key_in_env": os.environ.get("MCPO_API_KEY")}))
'''


class McpoStart(unittest.TestCase):
    def test_the_key_reaches_mcpo_through_the_environment_not_the_command_line(self):
        with TemporaryDirectory() as folder:
            package = Path(folder) / "mcpo"
            package.mkdir()
            (package / "__init__.py").write_text(FAKE_MCPO)
            launcher = Path(folder) / "mcpo-bin"
            launcher.write_text(f"#!{sys.executable}\n")
            env = {**os.environ, "MCPO_BIN": str(launcher), "PYTHONPATH": folder, "MCPO_API_KEY": "sekret-key"}
            done = subprocess.run(["sh", str(WRAPPER), "--host", "127.0.0.1", "--port", "8000"], env=env,
                                  capture_output=True, text=True, check=True)
        self.assertIn('"auto_envvar_prefix": "MCPO"', done.stdout)
        self.assertIn('"argv": ["--host", "127.0.0.1", "--port", "8000"]', done.stdout)
        self.assertNotIn("sekret-key", done.stdout.split('"key_in_env"')[0])

    def test_the_unit_no_longer_puts_the_key_on_the_command_line(self):
        unit = UNIT.read_text()
        self.assertNotIn("--api-key", unit)
        self.assertIn("ExecStart=/usr/local/bin/mcpo-start ", unit)


if __name__ == "__main__":
    unittest.main()
