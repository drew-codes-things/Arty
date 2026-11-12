import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
JOBS = """vzdump: backup-daily
\tschedule 02:00
\tcomment daily
\tvmid 100
\tprune-backups keep-last=7

vzdump: backup-weekly
\tschedule sun 03:00
\tvmid 101,204,205
"""
SOMEONE_ELSES_JOB = """vzdump: backup-family
\tschedule 01:00
\tcomment photos and documents
\tvmid 100
\tprune-backups keep-last=14,keep-monthly=6
"""
EVERY_GUEST = """vzdump: backup-all
\tschedule 03:00
\tall 1
\tprune-backups keep-last=5
"""


def backup_job_rewrite() -> str:
    text = (ROOT / "provision.sh").read_text()
    return re.search(r"python3 - <<'PY'\n(.*?)\nPY\n", text, re.S).group(1)


def rewrite(jobs_path: Path, ct: str, desktop: str, job: str = "backup-daily") -> subprocess.CompletedProcess:
    env = {**os.environ, "JOBS_CFG": str(jobs_path), "CT": ct, "DESKTOP_VMID": desktop, "BACKUP_JOB": job}
    return subprocess.run([sys.executable, "-c", backup_job_rewrite()], env=env, capture_output=True, text=True)


class BackupJobsTest(unittest.TestCase):
    def rewritten(self, jobs: str, job: str = "backup-daily") -> tuple[subprocess.CompletedProcess, str]:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "jobs.cfg"
            path.write_text(jobs)
            result = rewrite(path, "204", "205", job)
            return result, path.read_text()

    def test_the_named_job_gains_the_brain_and_desktop_and_keeps_everything_else(self):
        result, text = self.rewritten(JOBS)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(text, JOBS.replace("\tvmid 100\n", "\tvmid 100,204,205\n"))

    def test_jobs_it_was_not_given_are_left_exactly_as_they_were(self):
        jobs = SOMEONE_ELSES_JOB + "\n" + JOBS
        result, text = self.rewritten(jobs, "backup-weekly")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(text, jobs)

    def test_a_job_that_already_has_them_is_not_rewritten(self):
        jobs = JOBS.replace("\tvmid 100\n", "\tvmid 100,205,204\n")
        result, text = self.rewritten(jobs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(text, jobs)

    def test_a_job_that_backs_up_every_guest_is_left_alone(self):
        result, text = self.rewritten(EVERY_GUEST, "backup-all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(text, EVERY_GUEST)

    def test_an_unknown_job_is_an_error_and_changes_nothing(self):
        result, text = self.rewritten(JOBS, "backup-nightly")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backup-nightly", result.stderr)
        self.assertEqual(text, JOBS)

    def test_a_host_without_backup_jobs_is_left_alone(self):
        with TemporaryDirectory() as folder:
            result = rewrite(Path(folder) / "jobs.cfg", "204", "205")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
