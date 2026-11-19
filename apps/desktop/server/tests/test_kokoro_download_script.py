import os
import shutil
import subprocess
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[2]
FAKE_CURL = """#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
echo "$url" >> "$ASKED_FILE"
echo model > "$output"
"""


@pytest.fixture
def folder(tmp_path):
    shutil.copy(DESKTOP / "download-kokoro.sh", tmp_path / "download-kokoro.sh")
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "curl").write_text(FAKE_CURL)
    (stub / "curl").chmod(0o755)
    return tmp_path


def run_download(folder, *arguments):
    asked = folder / "asked.txt"
    environment = {**os.environ, "PATH": f"{folder / 'bin'}:{os.environ['PATH']}", "ASKED_FILE": str(asked)}
    subprocess.run(["bash", str(folder / "download-kokoro.sh"), *arguments], cwd=folder, env=environment, check=True,
                   capture_output=True)
    return asked.read_text().split() if asked.exists() else []


def test_the_default_download_is_the_full_model_and_the_voices(folder):
    asked = run_download(folder)
    assert [url.rsplit("/", 1)[-1] for url in asked] == ["kokoro-v1.0.onnx", "voices-v1.0.bin"]
    assert sorted(path.name for path in (folder / "models" / "kokoro").iterdir()) == ["kokoro-v1.0.onnx", "voices-v1.0.bin"]


def test_light_downloads_the_small_int8_model_instead_of_the_full_one(folder):
    asked = run_download(folder, "light")
    assert [url.rsplit("/", 1)[-1] for url in asked] == ["kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]
    assert not (folder / "models" / "kokoro" / "kokoro-v1.0.onnx").exists()
    assert (folder / "models" / "kokoro" / "kokoro-v1.0.int8.onnx").exists()


def test_files_already_there_are_not_fetched_again(folder):
    first = run_download(folder, "light")
    assert run_download(folder, "light") == first
