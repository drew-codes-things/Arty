import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "arty-pc" / "install-yt-dlp"
FAKE_PYTHON = """#!/bin/sh
venv="$3"
mkdir -p "$venv/bin"
cat > "$venv/bin/pip" <<'PIP'
#!/bin/sh
echo "$*" >> "$PIP_LOG"
touch "$(dirname "$0")/yt-dlp"
chmod +x "$(dirname "$0")/yt-dlp"
PIP
chmod +x "$venv/bin/pip"
"""


class InstallYtDlp(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(TemporaryDirectory()))
        tools = self.home / "tools"
        tools.mkdir()
        (tools / "python3").write_text(FAKE_PYTHON)
        (tools / "python3").chmod(0o755)
        self.log = self.home / "pip.log"
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{tools}:{os.environ['PATH']}", "PIP_LOG": str(self.log)}

    def install(self, pinned=""):
        return subprocess.run(["sh", "-s"], stdin=SCRIPT.open(), env={**self.env, "pinned": pinned},
                              capture_output=True, text=True)

    def pip_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_a_pinned_version_is_installed_exactly(self):
        self.assertEqual(self.install("2026.10.22").returncode, 0)
        self.assertEqual(self.pip_calls(), ["install -q yt-dlp[default]==2026.10.22"])
        self.assertTrue((self.home / ".local/bin/yt-dlp").is_symlink())

    def test_without_a_pin_it_installs_once_and_never_upgrades_on_reruns(self):
        self.install()
        self.install()
        self.assertEqual(self.pip_calls(), ["install -q yt-dlp[default]"])

    def test_a_version_that_is_not_digits_and_dots_is_refused(self):
        done = self.install("1.0; rm -rf ~")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.pip_calls(), [])

    def test_provision_and_deploy_both_use_the_shared_script(self):
        for name in ("provision.sh", "deploy.sh"):
            text = (ROOT / name).read_text()
            self.assertIn("arty-pc/install-yt-dlp", text, name)
            self.assertNotIn('--upgrade "yt-dlp', text, name)


if __name__ == "__main__":
    unittest.main()
