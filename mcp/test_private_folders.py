import os
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

pytest.importorskip("mcp.server.fastmcp")
import homelab_server as homelab
import remote
import research_server as research
import workspace_server as ws

DISGUISED_COMMANDS = [
    "ls ~/.g*-chrome",
    "cat $HOME/.gmail''-chrome/Default/Cookies",
    'cat "$HOME"/.browser"-profile"/Default/Cookies',
    "ls .{gmail,x}-chrome",
    "cat ~/.local/*/token",
    "cat ~/.ssh/id_ed25519",
    "cd ~/.local && cat browser-agent/token",
    "cat</home/ai/.gmail-chrome/Default/Cookies",
]
ORDINARY_COMMANDS = ["ls *", "ls -la ~", "cat notes/gmail-chrome-tips.md", "grep profile app.py", "ls .config", "ls x,y"]


def refuse_remote(*_arguments, **_keywords):
    pytest.fail("the desktop was asked")


@pytest.mark.parametrize("command", DISGUISED_COMMANDS)
def test_disguised_mentions_of_private_folders_are_caught(command):
    assert remote.touches_private_folders(command)
    assert ws.refusal_reason(command) == remote.PRIVATE_REFUSAL


@pytest.mark.parametrize("command", ORDINARY_COMMANDS)
def test_ordinary_commands_are_not_caught(command):
    assert not remote.touches_private_folders(command)


@pytest.mark.parametrize("path", ["~/.ssh/config", "projects/../.gmail-chrome/x", "/home/ai/.local/browser-agent"])
def test_private_paths_are_caught_after_normalising(path):
    assert remote.private_path(path)


def test_nextcloud_upload_refuses_a_private_path_without_asking(monkeypatch):
    monkeypatch.setattr(homelab, "ssh", refuse_remote)
    monkeypatch.setattr(homelab, "_dav", refuse_remote)
    assert homelab.nextcloud_upload("~/.gmail-chrome/Default/Cookies") == remote.PRIVATE_REFUSAL


def test_nextcloud_upload_refuses_a_link_into_a_private_folder(monkeypatch):
    commands = []
    monkeypatch.setattr(homelab, "ssh", lambda command, **k: commands.append(command) or (remote.PRIVATE_EXIT, b""))
    monkeypatch.setattr(homelab, "_dav", refuse_remote)
    assert homelab.nextcloud_upload("cookies-link") == remote.PRIVATE_REFUSAL
    assert "realpath" in commands[0]


def test_read_document_refuses_a_private_path_without_asking(monkeypatch):
    monkeypatch.setattr(research, "remote_text", refuse_remote)
    assert research.read_document("~/.local/browser-agent/token") == remote.PRIVATE_REFUSAL


def test_read_document_refuses_a_link_into_a_private_folder(monkeypatch):
    commands = []
    monkeypatch.setattr(research, "remote_text", lambda command, **k: commands.append(command) or (remote.PRIVATE_EXIT, ""))
    assert research.read_document("notes/link.pdf") == remote.PRIVATE_REFUSAL
    assert "realpath" in commands[0]


def test_git_refuses_a_private_repo_folder(monkeypatch):
    monkeypatch.setattr(research, "remote_text", refuse_remote)
    assert research.git("~/.gmail-chrome", "status") == remote.PRIVATE_REFUSAL


def test_git_resolves_the_repo_folder_before_entering_it(monkeypatch):
    commands = []
    monkeypatch.setattr(research, "remote_text", lambda command, **k: commands.append(command) or (remote.PRIVATE_EXIT, ""))
    assert research.git("linked-repo", "status") == remote.PRIVATE_REFUSAL
    assert "realpath" in commands[0]


def test_workspace_read_refuses_a_link_into_a_private_folder(monkeypatch):
    monkeypatch.setattr(ws, "remote_text", lambda command, **k: (remote.PRIVATE_EXIT, ""))
    assert ws.read_file("cookies-link") == remote.PRIVATE_REFUSAL
    assert ws.list_files("profile-link") == remote.PRIVATE_REFUSAL


def test_workspace_edit_refuses_a_link_into_a_private_folder(monkeypatch):
    monkeypatch.setattr(ws, "ssh", lambda command, **k: (remote.PRIVATE_EXIT, b""))
    assert ws.edit_file("prefs-link", "a", "b") == remote.PRIVATE_REFUSAL


@pytest.fixture
def home_with_profile(tmp_path):
    (tmp_path / ".gmail-chrome").mkdir()
    (tmp_path / ".gmail-chrome" / "Cookies").write_text("secret\n")
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "notes.txt").write_text("hello\n")
    (tmp_path / "projects" / "cookies-link").symlink_to(tmp_path / ".gmail-chrome" / "Cookies")
    (tmp_path / "projects" / "profile-link").symlink_to(tmp_path / ".gmail-chrome")
    return tmp_path


def run_guarded(home, name: str, must_exist: bool = True) -> subprocess.CompletedProcess:
    script = remote.resolve_outside_private_folders(remote.quoted(name), must_exist) + ' cat -- "$target"'
    return subprocess.run(["bash", "-c", script], env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)


@pytest.mark.parametrize("name", ["cookies-link", "profile-link/Cookies"])
def test_the_remote_guard_stops_links_into_a_private_folder(home_with_profile, name):
    done = run_guarded(home_with_profile, name)
    assert done.returncode == remote.PRIVATE_EXIT
    assert "secret" not in done.stdout


def test_the_remote_guard_lets_ordinary_files_through(home_with_profile):
    done = run_guarded(home_with_profile, "notes.txt")
    assert (done.returncode, done.stdout) == (0, "hello\n")


def test_writing_through_a_link_into_a_private_folder_is_refused(home_with_profile):
    script = ws.atomic_write_script(remote.quoted("cookies-link"))
    done = subprocess.run(["bash", "-c", script], input=b"planted", capture_output=True,
                          env={**os.environ, "HOME": str(home_with_profile)})
    assert done.returncode == remote.PRIVATE_EXIT
    assert (home_with_profile / ".gmail-chrome" / "Cookies").read_text() == "secret\n"


def test_writing_onto_a_folder_fails_and_leaves_nothing_behind(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    done = subprocess.run(["bash", "-c", ws.atomic_write_script(str(folder))], input=b"x", capture_output=True)
    assert done.returncode != 0
    assert list(folder.iterdir()) == []


def test_a_new_file_gets_the_usual_mode_not_the_temporary_one(tmp_path):
    target = tmp_path / "new.txt"
    script = f"umask 022; {ws.atomic_write_script(str(target))}"
    done = subprocess.run(["bash", "-c", script], input=b"x", capture_output=True)
    assert done.returncode == 0, done.stderr
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644


def test_writes_replace_with_mv_that_never_enters_a_folder(monkeypatch):
    assert "mv -fT" in ws.atomic_write_script("x")
