import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps" / "desktop"
APPIMAGE_SCRIPT = DESKTOP / "build-app.sh"
INSTALLER_SCRIPT = DESKTOP / "build-installer.ps1"
LEFT_OUT = ("__pycache__", "tests", ".pytest_cache", ".mypy_cache", ".ruff_cache")
ROBOCOPY_SERVER = re.compile(r"^& robocopy server .*?/XD (?P<names>.+?) /NFL", re.M)


def appimage_server_copy_line() -> str:
    lines = [line for line in APPIMAGE_SCRIPT.read_text().splitlines()
             if line.startswith("rsync ") and line.rstrip().endswith('server "$STAGE/"')]
    if len(lines) != 1:
        raise AssertionError(f"expected one rsync line copying server in build-app.sh, found {lines}")
    return lines[0]


def make_fake_server(workdir: Path) -> None:
    server = workdir / "server"
    for name in LEFT_OUT:
        (server / name).mkdir(parents=True)
        (server / name / "kept-out.txt").write_text("x")
    (server / "process" / ".ruff_cache").mkdir(parents=True)
    (server / "process" / "brain.py").write_text("x")
    (server / "arty_server.py").write_text("x")


@unittest.skipUnless(shutil.which("rsync"), "needs rsync, as build-app.sh does")
class AppImageBackendCopy(unittest.TestCase):
    def test_the_server_copy_leaves_out_tests_and_tool_caches(self):
        workdir = Path(tempfile.mkdtemp(prefix="arty-backend-copy-"))
        self.addCleanup(shutil.rmtree, workdir, True)
        make_fake_server(workdir)
        stage = workdir / "stage"
        stage.mkdir()
        result = subprocess.run(["bash", "-c", appimage_server_copy_line()], cwd=workdir,
                                env={"PATH": "/usr/bin:/bin", "STAGE": str(stage)},
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        copied = sorted(str(path.relative_to(stage)) for path in stage.rglob("*"))
        self.assertEqual(copied, ["server", "server/arty_server.py", "server/process", "server/process/brain.py"])


class WindowsBackendCopy(unittest.TestCase):
    def test_the_robocopy_of_server_leaves_out_tests_and_tool_caches(self):
        match = ROBOCOPY_SERVER.search(INSTALLER_SCRIPT.read_text())
        self.assertIsNotNone(match, "no robocopy of server with /XD in build-installer.ps1")
        self.assertEqual(sorted(match.group("names").split()), sorted(LEFT_OUT))


if __name__ == "__main__":
    unittest.main()
