import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ADDRESS = re.compile(r"\b(192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
CODE_SUFFIXES = {".py", ".sh", ".js", ".html", ".kt", ".java", ".json", ".yaml", ".yml", ".service", ".conf"}
SKIPPED_NAMES = {"package-lock.json"}
TEST_FOLDERS = {"tests", "test"}


def is_real_code(path: Path) -> bool:
    if path.name in SKIPPED_NAMES or path.name.endswith("Test.kt") or path.name.startswith("test_"):
        return False
    return path.suffix in CODE_SUFFIXES or path.name.startswith("homelab-")


def project_files() -> list[Path]:
    listing = ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    listed = subprocess.run(listing, check=True, capture_output=True).stdout
    paths = {ROOT / name for name in listed.decode().split("\0") if name}
    return sorted(path for path in paths if path.is_file())


def offenders() -> list[str]:
    found = []
    for path in project_files():
        relative = path.relative_to(ROOT)
        if not is_real_code(path) or TEST_FOLDERS & set(relative.parts):
            continue
        for number, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
            if PRIVATE_ADDRESS.search(line):
                found.append(f"{relative}:{number}: {line.strip()[:110]}")
    return found


class NoHardcodedAddresses(unittest.TestCase):
    def test_real_code_has_no_private_network_address(self):
        self.assertEqual(offenders(), [], "private addresses found in code; read them from settings instead")


if __name__ == "__main__":
    unittest.main()
