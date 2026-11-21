import io
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import native_host

CODE = "0123456789abcdef0123456789abcdef"
CHROME_ARGV = ["host.py", native_host.EXTENSION_ORIGIN]
FIREFOX_ARGV = ["host.py", "/home/u/.mozilla/native-messaging-hosts/app.arty.desktop.json", native_host.FIREFOX_ADDON_ID]


class WatchedInput(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def frame(message):
    body = json.dumps(message).encode()
    return struct.pack("=I", len(body)) + body


def answer_of(output):
    data = output.getvalue()
    (length,) = struct.unpack("=I", data[:4])
    assert len(data) == 4 + length
    return json.loads(data[4:])


@pytest.fixture
def data_root(tmp_path):
    (tmp_path / native_host.PAIRING_FILE_NAME).write_text(CODE)
    return tmp_path


@pytest.mark.parametrize("argv", [["host.py"], ["host.py", "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"],
                                  ["host.py", "manifest.json", "arty-browser@evil.example"],
                                  ["host.py", "x", native_host.EXTENSION_ORIGIN]])
def test_unknown_callers_are_refused_before_anything_is_read(argv, data_root):
    stdin, stdout = WatchedInput(frame({"type": "pair"})), io.BytesIO()
    assert native_host.serve(argv, stdin, stdout, data_root) == native_host.REFUSED_CALLER_EXIT
    assert stdin.read_sizes == []
    assert stdout.getvalue() == b""


@pytest.mark.parametrize("argv", [CHROME_ARGV, CHROME_ARGV + ["--parent-window=0"], FIREFOX_ARGV])
def test_the_arty_extension_gets_the_pairing_code(argv, data_root):
    stdout = io.BytesIO()
    assert native_host.serve(argv, WatchedInput(frame({"type": "pair"})), stdout, data_root) == 0
    assert answer_of(stdout) == {"type": "pairing", "code": CODE}


def test_an_oversized_message_is_refused_without_reading_its_body(data_root):
    stdin = WatchedInput(struct.pack("=I", native_host.MAX_MESSAGE_BYTES + 1) + b"x" * 10)
    stdout = io.BytesIO()
    assert native_host.serve(CHROME_ARGV, stdin, stdout, data_root) == native_host.BAD_MESSAGE_EXIT
    assert stdin.read_sizes == [native_host.LENGTH_BYTES]
    assert stdout.getvalue() == b""


@pytest.mark.parametrize("raw", [b"", b"\x05\x00", struct.pack("=I", 10) + b"{}", struct.pack("=I", 3) + b"abc",
                                 frame(["pair"]), struct.pack("=I", 2) + b"\xff\xfe"])
def test_broken_messages_are_refused(raw, data_root):
    stdout = io.BytesIO()
    assert native_host.serve(CHROME_ARGV, WatchedInput(raw), stdout, data_root) == native_host.BAD_MESSAGE_EXIT
    assert stdout.getvalue() == b""


@pytest.mark.parametrize("stored", [None, "", "not-a-code", CODE.upper(), CODE + "00"])
def test_no_usable_code_is_reported_as_unavailable(stored, tmp_path):
    if stored is not None:
        (tmp_path / native_host.PAIRING_FILE_NAME).write_text(stored)
    stdout = io.BytesIO()
    native_host.serve(CHROME_ARGV, WatchedInput(frame({"type": "pair"})), stdout, tmp_path)
    assert answer_of(stdout) == {"type": "unavailable"}


def test_other_requests_get_nothing(data_root):
    stdout = io.BytesIO()
    native_host.serve(CHROME_ARGV, WatchedInput(frame({"type": "token"})), stdout, data_root)
    assert answer_of(stdout) == {"type": "unknown"}


def installed_copy(data_root):
    folder = data_root / "native-host"
    folder.mkdir()
    shutil.copy(native_host.__file__, folder / "host.py")
    return folder / "host.py"


def test_the_copied_script_runs_alone_and_answers(data_root):
    script = installed_copy(data_root)
    finished = subprocess.run([sys.executable, "-I", str(script), native_host.EXTENSION_ORIGIN],
                              input=frame({"type": "pair"}), capture_output=True, timeout=30)
    assert finished.returncode == 0, finished.stderr
    assert answer_of(io.BytesIO(finished.stdout)) == {"type": "pairing", "code": CODE}


def test_the_copied_script_refuses_a_stranger(data_root):
    script = installed_copy(data_root)
    finished = subprocess.run([sys.executable, "-I", str(script), "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"],
                              input=frame({"type": "pair"}), capture_output=True, timeout=30)
    assert finished.returncode == native_host.REFUSED_CALLER_EXIT
    assert finished.stdout == b""


def test_the_host_only_uses_the_standard_library():
    source = Path(native_host.__file__).read_text()
    imported = {line.split()[1].split(".")[0] for line in source.splitlines() if line.startswith(("import ", "from "))}
    assert imported <= {"json", "re", "struct", "sys", "pathlib"}
