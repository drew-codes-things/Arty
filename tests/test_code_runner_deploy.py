import os
import re
import stat
import subprocess
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "code-runner" / "deploy.sh"
SERVICE = ROOT / "code-runner" / "code-runner.service"
STARTS_THE_RUNNER = "systemctl restart code-runner"
NEEDS_THE_FIREWALL = "nft list table inet code_runner >/dev/null ||"
NEEDS_THE_TOKEN = "[ -s /etc/code-runner/runner.env ] ||"
WRITES_THE_TOKEN = "> /etc/code-runner/runner.env.new"
MAKES_THE_BRAIN_TOKEN = "CODE_RUNNER_TOKEN=%s"
REMOTE_BLOCK = re.compile(r"<<'REMOTE'\n(.*?)^REMOTE$", re.S | re.M)
RUNNER_CT = "111"
BRAIN_CT = "104"
BRAIN_PID = "4242"
SECRETS = "srv/ai/secrets/brain.env"
BUNDLED = ("runner.py", "languages.py", "checks.py", "parse.ps1", "setup.sh", "code-runner.service")
CONTAINER_FOLDERS = ("srv/ai/secrets", "etc/systemd/system", "opt", "root", f"proc/{BRAIN_PID}")
FAKE_PCT = r"""#!/bin/bash
set -euo pipefail
inside() {
  printf '%s' "$2" | sed -E -e "s#(^|[^A-Za-z0-9_.-])/(srv|etc|opt|root|proc)([^A-Za-z0-9_.-]|\$)#\1$FAKE_ROOT/$1/\2\3#g" \
    -e "s#export PATH=#export PATH=$FAKE_BIN:#g"
}
case "$1" in
  push) cp "$3" "$FAKE_ROOT/$2$4" ;;
  exec)
    container=$2; shift 3; command=()
    for word in "$@"; do command+=("$(inside "$container" "$word")"); done
    for word in "${command[@]}"; do
      if printf '%s' "$word" | grep -Eq '(^|[^A-Za-z0-9_.-])/(srv|etc|opt|root|proc)([^A-Za-z0-9_.-]|$)'; then
        echo "fake pct: a path was not moved into the fake container: $word" >&2; exit 99
      fi
    done
    cd "$FAKE_ROOT/$container" && CONTAINER=$container exec "${command[@]}" ;;
  *) echo "fake pct: $1 is not faked" >&2; exit 98 ;;
esac
"""
FAKE_SYSTEMCTL = r"""#!/bin/bash
echo "$CONTAINER systemctl $*" >> "$FAKE_ROOT/calls"
case "$1" in
  show) cat "$FAKE_ROOT/$CONTAINER/main-pid" 2>/dev/null || echo 0 ;;
  is-active) echo active ;;
  --version) cat "$FAKE_ROOT/$CONTAINER/systemd-version" 2>/dev/null || echo "systemd 257 (257.13-1)" ;;
esac
"""
FAKE_NFT = r"""#!/bin/bash
echo "$CONTAINER nft $*" >> "$FAKE_ROOT/calls"
case "$*" in
  "list table inet code_runner") [ -f "$FAKE_ROOT/$CONTAINER/firewall-up" ] ;;
  "list ruleset") echo accept ;;
esac
"""
FAKE_CURL = "#!/bin/bash\necho '{\"status\": \"ok\"}'\n"
FAKE_SLEEP = "#!/bin/bash\nexit 0\n"


class CodeRunnerDeployTest(unittest.TestCase):
    def test_the_runner_only_starts_behind_its_firewall_and_with_its_token(self):
        text = DEPLOY.read_text()
        for needed in (NEEDS_THE_FIREWALL, NEEDS_THE_TOKEN, WRITES_THE_TOKEN, MAKES_THE_BRAIN_TOKEN):
            with self.subTest(needed=needed):
                self.assertIn(needed, text)
                self.assertLess(text.index(needed), text.index(STARTS_THE_RUNNER))

    def test_setup_puts_the_firewall_up_before_the_runner_starts(self):
        text = DEPLOY.read_text()
        self.assertRegex(text, re.compile(r"^\s*setup\)[^\n]*setup\.sh all; apply_firewall", re.M))
        self.assertRegex(text, re.compile(r"^\s*files\)[^\n]*\|\| apply_firewall", re.M))

    def test_the_token_is_never_echoed(self):
        text = DEPLOY.read_text()
        self.assertNotRegex(text, r"echo[^\n]*\$\{?(?:token|RUNNER_TOKEN|CODE_RUNNER_TOKEN)\b|cat /etc/code-runner|cat /srv/ai/secrets")

    def test_the_service_reads_the_token_file(self):
        self.assertIn("EnvironmentFile=/etc/code-runner/runner.env", SERVICE.read_text())

    def test_the_service_only_starts_after_the_firewall_has_loaded(self):
        text = SERVICE.read_text()
        self.assertRegex(text, r"(?m)^After=.*\bnftables\.service\b")
        self.assertRegex(text, r"(?m)^Requires=.*\bnftables\.service\b")


def write_program(folder: Path, name: str, text: str):
    program = folder / name
    program.write_text(text)
    program.chmod(program.stat().st_mode | stat.S_IXUSR)


class RemoteBlockAgainstFakeContainers(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        for name, text in (("pct", FAKE_PCT), ("systemctl", FAKE_SYSTEMCTL), ("nft", FAKE_NFT), ("curl", FAKE_CURL),
                           ("sleep", FAKE_SLEEP)):
            write_program(self.fake_bin, name, text)
        for container in (RUNNER_CT, BRAIN_CT):
            for inner in CONTAINER_FOLDERS:
                (self.root / container / inner).mkdir(parents=True)
        (self.root / BRAIN_CT / SECRETS).write_text("ARTY_API_KEY=kept\n")
        (self.root / BRAIN_CT / "main-pid").write_text(BRAIN_PID + "\n")
        (self.root / RUNNER_CT / "firewall-up").write_text("")
        self.running_brain_has("")

    def running_brain_has(self, environment: str):
        (self.root / BRAIN_CT / "proc" / BRAIN_PID / "environ").write_text(environment.replace("\n", "\0"))

    def saved_token(self) -> str:
        for line in (self.root / BRAIN_CT / SECRETS).read_text().splitlines():
            if line.startswith("CODE_RUNNER_TOKEN="):
                return line.split("=", 1)[1]
        return ""

    def bundle(self) -> Path:
        bundle = self.root / "bundle.tgz"
        with tarfile.open(bundle, "w:gz") as archive:
            for name in BUNDLED:
                archive.add(DEPLOY.parent / name, arcname=name)
            nftables = self.root / "nftables.conf"
            nftables.write_text("table inet code_runner {}\n")
            archive.add(nftables, arcname="nftables.conf")
        return bundle

    def deploy(self, stage: str = "files") -> subprocess.CompletedProcess:
        block = REMOTE_BLOCK.search(DEPLOY.read_text()).group(1)
        environment = {**os.environ, "PATH": f"{self.fake_bin}:{os.environ['PATH']}", "FAKE_ROOT": str(self.root),
                       "FAKE_BIN": str(self.fake_bin)}
        return subprocess.run(["bash", "-s", "--", RUNNER_CT, str(self.bundle()), stage, BRAIN_CT], input=block,
                              env=environment, capture_output=True, text=True, timeout=60)

    def calls(self) -> list[str]:
        calls = self.root / "calls"
        return calls.read_text().splitlines() if calls.exists() else []

    def test_a_first_deploy_makes_one_token_for_both_and_restarts_the_brain(self):
        finished = self.deploy()
        self.assertEqual(0, finished.returncode, finished.stderr)
        token = self.saved_token()
        self.assertEqual(64, len(token))
        self.assertIn("ARTY_API_KEY=kept", (self.root / BRAIN_CT / SECRETS).read_text())
        self.assertEqual(f"RUNNER_TOKEN={token}\n", (self.root / RUNNER_CT / "etc/code-runner/runner.env").read_text())
        self.assertIn(f"{RUNNER_CT} systemctl restart code-runner", self.calls())
        self.assertIn(f"{BRAIN_CT} systemctl restart brain", self.calls())
        self.assertNotIn(token, finished.stdout + finished.stderr)

    def test_a_brain_still_running_without_the_saved_token_is_restarted(self):
        (self.root / BRAIN_CT / SECRETS).write_text("ARTY_API_KEY=kept\nCODE_RUNNER_TOKEN=" + "a" * 64 + "\n")
        self.running_brain_has("ARTY_API_KEY=kept\n")
        finished = self.deploy()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual("a" * 64, self.saved_token())
        self.assertIn(f"{BRAIN_CT} systemctl restart brain", self.calls())

    def test_a_brain_that_already_has_the_token_is_left_running(self):
        token = "b" * 64
        (self.root / BRAIN_CT / SECRETS).write_text(f"CODE_RUNNER_TOKEN={token}\n")
        self.running_brain_has(f"CODE_RUNNER_TOKEN={token}\n")
        finished = self.deploy()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn(f"{RUNNER_CT} systemctl restart code-runner", self.calls())
        self.assertNotIn(f"{BRAIN_CT} systemctl restart brain", self.calls())

    def test_a_stopped_brain_is_not_started_by_the_runner_deploy(self):
        (self.root / BRAIN_CT / "main-pid").write_text("0\n")
        finished = self.deploy()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertNotIn(f"{BRAIN_CT} systemctl restart brain", self.calls())

    def test_the_runner_is_not_started_when_its_firewall_table_is_missing(self):
        (self.root / RUNNER_CT / "firewall-up").unlink()
        finished = self.deploy()
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("no code_runner firewall table", finished.stderr)
        self.assertNotIn(f"{RUNNER_CT} systemctl restart code-runner", self.calls())

    def test_the_runner_is_not_started_on_a_systemd_too_old_for_its_run_options(self):
        (self.root / RUNNER_CT / "systemd-version").write_text("systemd 252 (252.39-1~deb12u1)\n")
        finished = self.deploy()
        self.assertNotEqual(0, finished.returncode)
        self.assertIn("systemd 254", finished.stderr)
        self.assertNotIn(f"{RUNNER_CT} systemctl restart code-runner", self.calls())


if __name__ == "__main__":
    unittest.main()
