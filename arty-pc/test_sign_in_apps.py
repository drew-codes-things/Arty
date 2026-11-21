import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("sign-in-apps")
FAKE_CHROME = """#!/bin/sh
printf '%s\\n' "$@" > "$HOME/chrome-args"
cat "$HOME/.local/browser-agent/apps-signing-in" > "$HOME/flag-while-open"
"""


def run_sign_in(tmp_path, listed) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir()
    chrome = tmp_path / "google-chrome"
    chrome.write_text(FAKE_CHROME)
    chrome.chmod(0o755)
    apps = tmp_path / "own-apps.json"
    apps.write_text(json.dumps(listed))
    script = tmp_path / "sign-in-apps"
    script.write_text(SCRIPT.read_text().replace("/usr/bin/google-chrome", str(chrome))
                      .replace("/etc/arty/own-apps.json", str(apps)))
    script.chmod(0o755)
    return subprocess.run([str(script)], env={**os.environ, "HOME": str(home)}, capture_output=True, text=True,
                          timeout=60)


def test_it_opens_the_apps_profile_on_the_starfire_drive_and_calendar_pages(tmp_path):
    done = run_sign_in(tmp_path, {"drive": "https://drive.example.org", "calendar": "https://calendar.example.org",
                                  "starfire": "https://starfire.example.org"})
    assert done.returncode == 0, done.stderr
    home = tmp_path / "home"
    arguments = (home / "chrome-args").read_text().split()
    assert f"--user-data-dir={home}/.apps-browser-profile" in arguments
    assert not any("remote-debugging" in argument for argument in arguments)
    assert arguments[-3:] == ["https://starfire.example.org", "https://drive.example.org", "https://calendar.example.org"]


def test_the_agent_is_told_while_the_window_is_open_and_not_after(tmp_path):
    done = run_sign_in(tmp_path, {"drive": "https://drive.example.org"})
    assert done.returncode == 0, done.stderr
    home = tmp_path / "home"
    assert (home / "flag-while-open").read_text().strip().isdigit()
    assert not (home / ".local" / "browser-agent" / "apps-signing-in").exists()


@pytest.mark.parametrize("listed", [{}, {"drive": "http://drive.example.org"}, {"drive": "https://drive.example.org/x"},
                                    {"drive": "javascript:alert(1)"}, ["https://drive.example.org"]])
def test_nothing_opens_without_a_plain_https_address(tmp_path, listed):
    done = run_sign_in(tmp_path, listed)
    assert done.returncode == 1
    assert not (tmp_path / "home" / "chrome-args").exists()
