import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "pin-desktop-host-key"
ADDRESS = "198.51.100.7"


def host_key(folder: Path, name: str) -> str:
    path = folder / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    kind, key, *_ = (folder / f"{name}.pub").read_text().split()
    return f"{kind} {key}"


def fingerprint(line: str) -> str:
    done = subprocess.run(["ssh-keygen", "-lf", "-"], input=f"x {line}\n", capture_output=True, text=True, check=True)
    return done.stdout.split()[1]


class PinDesktopHostKey(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory()))
        self.real = host_key(self.folder, "real")
        self.other = host_key(self.folder, "other")
        tools = self.folder / "tools"
        tools.mkdir()
        (tools / "ssh-keyscan").write_text(f"#!/bin/sh\necho '{ADDRESS} {self.real}'\n")
        (tools / "ssh").write_text("#!/bin/sh\ncat \"$FAKE_SERVER_KNOWN\" 2>/dev/null\n")
        for tool in tools.iterdir():
            tool.chmod(0o755)
        self.laptop_known = self.folder / "known_hosts"
        self.server_known = self.folder / "server_known_hosts"
        self.pinned = self.folder / "pinned"
        hosts = self.folder / "hosts.env"
        hosts.write_text(f"DESKTOP_ADDRESS={ADDRESS}\n")
        self.env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "ARTY_HOSTS_FILE": str(hosts),
                    "ARTY_LAPTOP_KNOWN_HOSTS": str(self.laptop_known), "ARTY_DESKTOP_KNOWN_HOSTS": str(self.pinned),
                    "FAKE_SERVER_KNOWN": str(self.server_known)}

    def run_script(self, *arguments):
        return subprocess.run([str(SCRIPT), *arguments], env=self.env, capture_output=True, text=True)

    def test_a_key_the_laptop_already_trusts_is_pinned_under_the_alias(self):
        self.laptop_known.write_text(f"{ADDRESS} {self.real}\n")
        subprocess.run(["ssh-keygen", "-q", "-H", "-f", str(self.laptop_known)], check=True, capture_output=True)
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.pinned.read_text(), f"arty-desktop {self.real}\n")

    def test_a_key_the_server_already_trusts_is_pinned(self):
        self.server_known.write_text(f"{ADDRESS} {self.real}\n")
        self.assertEqual(self.run_script().returncode, 0)
        self.assertIn(self.real, self.pinned.read_text())

    def test_a_changed_key_is_refused_and_nothing_is_written(self):
        self.laptop_known.write_text(f"{ADDRESS} {self.other}\n")
        done = self.run_script()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("CHANGED", done.stderr)
        self.assertFalse(self.pinned.exists())

    def test_a_changed_key_says_how_to_clear_the_old_record(self):
        self.laptop_known.write_text(f"{ADDRESS} {self.other}\n")
        done = self.run_script()
        self.assertIn(f"ssh-keygen -R {ADDRESS} -f {self.laptop_known}", done.stderr)
        self.assertIn(f"--trust-fingerprint {fingerprint(self.real)}", done.stderr)

    def test_a_rebuilt_vm_is_pinned_once_its_new_fingerprint_is_confirmed(self):
        self.laptop_known.write_text(f"{ADDRESS} {self.other}\n")
        done = self.run_script("--trust-fingerprint", fingerprint(self.real))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.pinned.read_text(), f"arty-desktop {self.real}\n")
        self.assertIn("ssh-keygen -R", done.stdout)

    def test_a_wrong_fingerprint_does_not_override_a_changed_key(self):
        self.laptop_known.write_text(f"{ADDRESS} {self.other}\n")
        done = self.run_script("--trust-fingerprint", fingerprint(self.other))
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(self.pinned.exists())

    def test_with_no_record_it_asks_for_the_fingerprint_from_the_console(self):
        done = self.run_script()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("--trust-fingerprint", done.stderr)
        self.assertIn(fingerprint(self.real), done.stderr)
        self.assertFalse(self.pinned.exists())

    def test_a_fingerprint_checked_on_the_console_pins_the_key(self):
        self.assertEqual(self.run_script("--trust-fingerprint", fingerprint(self.real)).returncode, 0)
        self.assertEqual(self.pinned.read_text(), f"arty-desktop {self.real}\n")

    def test_a_wrong_fingerprint_pins_nothing(self):
        done = self.run_script("--trust-fingerprint", fingerprint(self.other))
        self.assertNotEqual(done.returncode, 0)
        self.assertFalse(self.pinned.exists())


if __name__ == "__main__":
    unittest.main()
