import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "quest-code"


def run_script(tmp_path, token):
    bin_folder = tmp_path / "bin"
    bin_folder.mkdir()
    curl = bin_folder / "curl"
    curl.write_text('#!/bin/bash\nprintf "%s\\n" "$*" > "$CAPTURE_ARGS"\ncat > "$CAPTURE_STDIN"\necho K7Q9-XM3P\n')
    curl.chmod(0o755)
    secrets = tmp_path / "secrets.env"
    secrets.write_text(f"HEADSET_TOKEN='{token}'\n")
    env = {**os.environ, "PATH": f"{bin_folder}:{os.environ['PATH']}", "ARTY_VOICE_SECRETS": str(secrets),
           "CAPTURE_ARGS": str(tmp_path / "args"), "CAPTURE_STDIN": str(tmp_path / "stdin")}
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
    return result, (tmp_path / "args").read_text(), (tmp_path / "stdin").read_text()


def test_the_key_goes_to_curl_on_stdin_and_never_in_its_arguments(tmp_path):
    result, args, stdin = run_script(tmp_path, "plain-key-123")
    assert result.returncode == 0 and result.stdout.strip() == "K7Q9-XM3P"
    assert "plain-key-123" not in args
    assert args.strip() == "-fsS --config - http://127.0.0.1:8790/pair/new?format=text"
    assert stdin == 'header = "Authorization: Bearer plain-key-123"\n'


def test_a_key_with_a_quote_or_backslash_reaches_curl_unchanged(tmp_path):
    _, _, stdin = run_script(tmp_path, 'a"b\\c')
    assert stdin == 'header = "Authorization: Bearer a\\"b\\\\c"\n'


def test_a_missing_key_is_named_in_the_error(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text("OTHER=1\n")
    result = subprocess.run([str(SCRIPT)], env={**os.environ, "ARTY_VOICE_SECRETS": str(secrets)}, capture_output=True, text=True)
    assert result.returncode != 0 and "HEADSET_TOKEN is not set" in result.stderr
