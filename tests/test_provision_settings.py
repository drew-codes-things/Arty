import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "provision.sh"
FAKE_SSH = """#!/bin/bash
if [ "$2" = bash ] && [ "$3" = -s ] && [ "$4" = -- ]; then
  shift 4
  printf '%s\\n' "$@" > "$LOG_DIR/remote-args"
fi
cat > /dev/null
exit 0
"""
FAKE_SCP = "#!/bin/sh\nexit 0\n"
OWNER_SETTINGS = ("HEAL_GUESTS", "NEXTCLOUD_CT", "SET_BACKUP_JOBS", "HOST_FACTS_FILE", "BACKUP_JOB", "CT",
                  "DESKTOP_VMID", "TIMEZONE")
ADDRESSES = {"AI_ADDRESS": "192.0.2.13", "DESKTOP_ADDRESS": "192.0.2.14", "HOST_ADDRESS": "192.0.2.253",
             "NETWORK": "192.0.2.0/24"}
REMOTE_ARGUMENTS = ("CT", "DESKTOP_VMID", "AI_ADDRESS", "DESKTOP_ADDRESS", "HOST_ADDRESS", "NETWORK", "HEAL_GUESTS",
                    "NEXTCLOUD_CT", "TIMEZONE", "SET_BACKUP_JOBS", "HOST_FACTS_FILE", "BACKUP_JOB")


class ProvisionSettings(unittest.TestCase):
    def provision(self, **settings) -> tuple[subprocess.CompletedProcess, dict]:
        with TemporaryDirectory(prefix="arty-provision-test-") as folder:
            logs = Path(folder)
            tools = logs / "bin"
            tools.mkdir()
            for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
                (tools / name).write_text(body)
                (tools / name).chmod(0o755)
            env = {key: value for key, value in os.environ.items() if key not in OWNER_SETTINGS}
            env.update({"PATH": f"{tools}:{os.environ['PATH']}", "LOG_DIR": str(logs), "ARTY_HOSTS_FILE": "/dev/null",
                        **ADDRESSES, **settings})
            result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
            sent = logs / "remote-args"
            values = sent.read_text().split("\n")[:len(REMOTE_ARGUMENTS)] if sent.exists() else []
            return result, dict(zip(REMOTE_ARGUMENTS, values))

    def test_a_plain_run_heals_no_guests_and_skips_nextcloud_host_facts_and_backup_jobs(self):
        result, remote = self.provision()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((remote["HEAL_GUESTS"], remote["NEXTCLOUD_CT"], remote["HOST_FACTS_FILE"], remote["BACKUP_JOB"]),
                         ("", "", "", ""))

    def test_the_settings_reach_the_host(self):
        result, remote = self.provision(HEAL_GUESTS="100 105", NEXTCLOUD_CT="100",
                                        HOST_FACTS_FILE="/tank/ai/state/host-facts.json", BACKUP_JOB="backup-daily")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((remote["HEAL_GUESTS"], remote["NEXTCLOUD_CT"], remote["HOST_FACTS_FILE"], remote["BACKUP_JOB"]),
                         ("100 105", "100", "/tank/ai/state/host-facts.json", "backup-daily"))

    def test_a_host_facts_file_that_is_not_a_plain_absolute_path_is_refused(self):
        for path in ("state/host-facts.json", "/tank/a b.json", "/tank/$(reboot).json"):
            result, remote = self.provision(HOST_FACTS_FILE=path)
            self.assertNotEqual(result.returncode, 0, path)
            self.assertIn("HOST_FACTS_FILE", result.stderr)
            self.assertEqual(remote, {}, path)

    def test_a_backup_job_id_with_other_characters_is_refused(self):
        for job in ("backup daily", "backup';reboot;'", "../jobs"):
            result, remote = self.provision(BACKUP_JOB=job)
            self.assertNotEqual(result.returncode, 0, job)
            self.assertIn("BACKUP_JOB", result.stderr)
            self.assertEqual(remote, {}, job)


if __name__ == "__main__":
    unittest.main()
