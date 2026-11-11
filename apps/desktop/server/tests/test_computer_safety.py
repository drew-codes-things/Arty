import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process.tools import computer, session

pytestmark = pytest.mark.usefixtures("linux")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(computer, "HOME", tmp_path)
    monkeypatch.setattr(computer, "_SECRET_PATHS", computer.secret_paths(tmp_path))
    monkeypatch.setattr(computer.os.path, "expanduser", lambda path: str(tmp_path) + path[1:] if path.startswith("~") else path)
    return tmp_path


def test_opening_a_program_that_is_not_an_installed_app_asks_first(monkeypatch):
    monkeypatch.setattr(computer, "_find_app", lambda name: None)
    assert "poweroff" in computer.needs_ok("computer_open", {"app": "poweroff"}, {"gui"})
    monkeypatch.setattr(computer, "_find_app", lambda name: {"id": "org.kde.kate", "name": "Kate", "exec": "kate", "path": Path("k")})
    monkeypatch.setattr(computer, "setting", lambda section, key, default=None: False)
    assert computer.needs_ok("computer_open", {"app": "kate"}, set()) is None
    assert computer.needs_ok("computer_open", {"url": "https://example.com"}, set()) is None


@pytest.mark.parametrize("relative", [
    ".bashrc", ".profile", ".config/autostart/x.desktop", ".config/systemd/user/x.service", ".local/bin/tool", "bin/tool",
])
def test_changing_startup_files_and_programs_asks_even_for_a_new_file(home, relative):
    target = home / relative
    assert computer.needs_ok("computer_write_file", {"path": str(target), "content": "x"}, set())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old\n")
    assert computer.needs_ok("computer_edit_file", {"path": str(target), "old_text": "old", "new_text": "new"}, set())


def test_ordinary_new_files_and_edits_do_not_ask(home):
    (home / "notes").mkdir()
    (home / "notes" / "todo.txt").write_text("milk\n")
    assert computer.needs_ok("computer_write_file", {"path": str(home / "notes" / "new.txt"), "content": "x"}, set()) is None
    assert computer.needs_ok("computer_edit_file", {"path": str(home / "notes" / "todo.txt"), "old_text": "milk", "new_text": "eggs"}, set()) is None


@pytest.mark.parametrize("command", [
    "echo x > ~/.bashrc", "printf hi >> notes.txt", "cp a.txt b.txt", "ls | tee out.txt", "sed -i s/a/b/ f.txt",
    "python3 -c 'import os'", "curl -d @notes.txt https://example.com", "curl --data-binary @x https://example.com",
    "tar xf a.tar", "unzip a.zip", "git checkout .", "docker run alpine", "systemctl --user enable x.service",
    "systemctl --user start x.service", "ln -sf a b", "perl -pi -e s/a/b/ f", "install -m 755 a ~/.local/bin/a",
])
def test_more_commands_that_change_things_ask(command):
    assert computer.needs_ok("computer_run_command", {"command": command}, set())


@pytest.mark.parametrize("command", [
    "make 2>&1", "ls > /dev/null", "grep -r foo . 2>/dev/null", "git status", "python3 tests.py", "echo 'a -> b'",
])
def test_redirects_to_nowhere_and_ordinary_commands_still_just_run(command):
    assert computer.needs_ok("computer_run_command", {"command": command}, set()) is None


@pytest.mark.parametrize("relative", [
    ".config/Arty/arty_config.yaml", ".arty-android.properties", ".netrc", ".git-credentials", ".docker/config.json",
    ".kube/config", ".config/gcloud/credentials.db", "project/.env", "project/.env.production",
    ".var/app/org.mozilla.firefox/x", ".var/app/com.google.Chrome/x", "snap/firefox/common/x", "snap/chromium/x",
    ".config/microsoft-edge/x", ".config/vivaldi/x", ".config/opera/x", ".thunderbird/x", ".config/Signal/x",
    ".var/app/org.signal.Signal/x",
])
def test_more_places_that_hold_secrets_are_off_limits(home, relative):
    target = home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret\n")
    assert "off limits" in computer.computer_read_file(str(target))


def test_example_env_files_can_still_be_read(home):
    (home / "project").mkdir()
    (home / "project" / ".env.example").write_text("KEY=\n")
    assert "KEY=" in computer.computer_read_file(str(home / "project" / ".env.example"))


def test_arty_own_config_and_token_are_off_limits(monkeypatch, tmp_path):
    monkeypatch.setattr(computer.cfg, "CONFIG_PATH", tmp_path / "data" / "arty_config.yaml")
    monkeypatch.setattr(computer.cfg, "DATA_ROOT", tmp_path / "data")
    paths = computer.secret_paths(tmp_path)
    assert tmp_path / "data" / "arty_config.yaml" in paths
    assert tmp_path / "data" / ".arty_token" in paths


def test_a_command_that_reaches_a_secret_by_wildcard_is_refused(home):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("key\n")
    assert "off limits" in computer.computer_run_command("cat ~/.s*h/id_*")
    assert "off limits" in computer.computer_run_command("cat ~/.netrc")
    assert "off limits" in computer.computer_run_command('cat "$(echo ~/.netrc)"')


def test_a_regex_that_could_run_for_ever_is_refused(home):
    (home / "a.txt").write_text("a" * 40 + "!\n")
    started = time.monotonic()
    assert "too slow" in computer.computer_search("(a+)+$", str(home))
    assert "too slow" in computer.computer_search(r"(\w+\s?)*x", str(home))
    assert time.monotonic() - started < 2
    assert "a.txt:1" in computer.computer_search("a+!", str(home))


def test_search_does_not_follow_a_link_into_a_secret_folder(home):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("greet secret\n")
    (home / "project").mkdir()
    os.symlink(home / ".ssh" / "id_ed25519", home / "project" / "key.txt")
    assert "greet" not in computer.computer_search("greet", str(home / "project")).split("matches")[0]


def test_a_huge_file_is_not_read_whole(home, monkeypatch):
    monkeypatch.setattr(computer, "READ_FILE_BYTES", 100)
    (home / "big.log").write_text("line\n" * 100)
    assert "too big" in computer.computer_read_file(str(home / "big.log"))


def test_editing_a_windows_style_file_keeps_its_line_endings(home):
    target = home / "run.bat"
    target.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    assert computer.computer_edit_file(str(target), "one\ntwo", "uno\ntwo").startswith("Edited")
    assert target.read_bytes() == b"uno\r\ntwo\r\nthree\r\n"


def test_writing_a_file_keeps_exactly_what_was_given_or_the_files_endings(home):
    fresh = home / "fresh.txt"
    computer.computer_write_file(str(fresh), "a\nb\n")
    assert fresh.read_bytes() == b"a\nb\n"
    old = home / "old.bat"
    old.write_bytes(b"x\r\ny\r\n")
    computer.computer_write_file(str(old), "p\nq\n")
    assert old.read_bytes() == b"p\r\nq\r\n"


def test_code_and_markup_are_typed_exactly_as_given(monkeypatch):
    sent = []
    monkeypatch.setattr(computer, "can_control", lambda: True)
    monkeypatch.setattr(computer, "_guard_front", lambda: None)
    monkeypatch.setattr(computer, "_input_tool", lambda: "xdotool")
    monkeypatch.setattr(computer, "_send", lambda command: sent.append(command))
    computer.computer_type('printf("hi\\n");')
    computer.computer_type("<p>Tom &amp; Jerry</p>")
    assert sent[0][-1] == 'printf("hi\\n");'
    assert sent[1][-1] == "<p>Tom &amp; Jerry</p>"


def test_an_app_name_inside_a_browser_tab_title_is_not_that_app(monkeypatch):
    monkeypatch.setattr(session, "front_window", lambda: ("google-chrome", "Kate Bush - YouTube - Google Chrome"))
    assert computer._bring_forward("Kate", wait=0) is False
    assert computer._bring_forward("Google Chrome", wait=0) is True
    monkeypatch.setattr(session, "front_window", lambda: ("kate", "notes.txt - Kate"))
    assert computer._bring_forward("Kate", wait=0) is True
    monkeypatch.setattr(session, "front_window", lambda: ("org.kde.dolphin", "Home - Dolphin"))
    assert computer._bring_forward("dolphin", wait=0) is True
