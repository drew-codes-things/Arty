import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ADDRESS = re.compile(r"\b(192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
CODE_SUFFIXES = {".py", ".sh", ".js", ".html", ".kt", ".java", ".json", ".yaml", ".yml", ".service", ".conf"}
SKIPPED_NAMES = {"package-lock.json"}
TEST_FOLDERS = {"tests", "test"}
UNTRACKED_FOLDERS = {"node_modules", "__pycache__", "dist"}


def is_real_code(path: Path) -> bool:
    if path.name in SKIPPED_NAMES or path.name.endswith("Test.kt") or path.name.startswith("test_"):
        return False
    return path.suffix in CODE_SUFFIXES or path.name.startswith("homelab-")


def is_untracked_folder(name: str) -> bool:
    return name.startswith(".") or name in UNTRACKED_FOLDERS


def walked_files() -> set[Path]:
    found = set()
    for folder, subfolders, names in os.walk(ROOT):
        subfolders[:] = [name for name in subfolders if not is_untracked_folder(name)]
        found |= {Path(folder) / name for name in names}
    return found


def git_listed_files() -> set[Path] | None:
    listing = ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    try:
        listed = subprocess.run(listing, capture_output=True)
    except FileNotFoundError:
        return None
    if listed.returncode != 0:
        return None
    return {ROOT / name for name in listed.stdout.decode().split("\0") if name}


def project_files() -> list[Path]:
    paths = git_listed_files()
    if paths is None:
        paths = walked_files()
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

    def test_without_git_the_files_are_found_by_walking_the_tree(self):
        no_git = subprocess.CompletedProcess(["git"], 128, b"", b"fatal: not a git repository")
        with mock.patch.object(subprocess, "run", return_value=no_git):
            walked = project_files()
        self.assertIn(Path(__file__).resolve(), walked)
        self.assertIn(ROOT / "provision.sh", walked)
        self.assertFalse([path for path in walked if any(part.startswith(".") for part in path.relative_to(ROOT).parts[:-1])])

    def test_without_a_git_program_the_tree_is_walked_too(self):
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertIn(ROOT / "provision.sh", project_files())


if __name__ == "__main__":
    unittest.main()
