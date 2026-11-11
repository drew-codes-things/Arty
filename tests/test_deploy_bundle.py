import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_SSH = """#!/bin/bash
printf '%s\\n' "$*" >> "$LOG_DIR/ssh.log"
case "$*" in
  *mktemp*) echo /tmp/tmp.Fake1234.tgz ;;
esac
cat > /dev/null
exit 0
"""
FAKE_SCP = """#!/bin/bash
count=$(ls "$LOG_DIR" | grep -c '^copy' || true)
for argument in "$@"; do
  case "$argument" in -*) ;; *) sources+=("$argument") ;; esac
done
destination=${sources[-1]}
printf '%s\\n' "$destination" >> "$LOG_DIR/scp.log"
cp "${sources[0]}" "$LOG_DIR/copy$count"
exit 0
"""


@unittest.skipUnless((ROOT / "desktop_known_hosts").is_file(), "needs the pinned desktop key file deploy.sh checks for")
class DeployBundle(unittest.TestCase):
    def setUp(self):
        self.logs = Path(tempfile.mkdtemp(prefix="arty-deploy-test-"))
        fake_bin = self.logs / "bin"
        fake_bin.mkdir()
        for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
            (fake_bin / name).write_text(body)
            (fake_bin / name).chmod(0o755)
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "LOG_DIR": str(self.logs),
               "ARTY_HOSTS_FILE": "/dev/null", "DESKTOP_ADDRESS": "192.0.2.9", "ARTY_DISCORD_ID": "",
               "ARTY_TELEGRAM_ID": ""}
        self.run_result = subprocess.run([str(ROOT / "deploy.sh")], env=env, capture_output=True, text=True, timeout=120)

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.logs)], check=False)

    def bundle_members(self) -> list[str]:
        first_copy = self.logs / "copy0"
        with tarfile.open(first_copy) as bundle:
            return bundle.getnames()

    def test_the_host_only_egress_unit_is_not_shipped_to_the_container(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        members = self.bundle_members()
        self.assertIn("services/brain.service", members)
        self.assertNotIn("services/arty-desktop-egress.service", members)
        self.assertFalse([name for name in members if name.startswith("mcp/test_")], members)

    def test_the_bundle_goes_to_a_fresh_temporary_path_on_the_host(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        first_destination = (self.logs / "scp.log").read_text().splitlines()[0]
        self.assertEqual(first_destination, "proxmox:/tmp/tmp.Fake1234.tgz")
        self.assertNotIn("/tmp/ai-deploy.tgz", (self.logs / "ssh.log").read_text())


if __name__ == "__main__":
    unittest.main()
