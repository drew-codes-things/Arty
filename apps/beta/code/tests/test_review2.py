import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arty_code import brain, cli, permissions, settings, tools, webfetch
from tests.test_brain import FakeBrain, Identity, home_brain  # noqa: F401
from tests.test_permissions import Asker


@pytest.fixture
def linked(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("root:x\n")
    (project / "a.txt").write_text("hello\n")
    (project / "linkout").symlink_to(outside)
    return tools.Workspace(project)


def asks(workspace, command, windows=False, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(permissions, "ON_WINDOWS", windows)
    ask = Asker()
    permissions.Gate(workspace, ask).check("run_command", {"command": command})
    return bool(ask.questions)


@pytest.mark.parametrize("command", ["cat */secret", "cat l*/secret", "grep -R root .", "rg -L root", "find -L .",
                                     "ls -L linkout", "du -L .", "tree -l", "cat ~-/secret", "cat ~+/../outside/secret",
                                     "grep -rf/etc/passwd x", "cat ~root/x", "grep --dereference-recursive root ."])
def test_look_only_commands_that_reach_outside_through_links_wildcards_or_tildes_ask(linked, command):
    assert asks(linked, command)


@pytest.mark.parametrize("command", ["cat a.txt", "cat *.txt", "grep -r hello .", "ls", "cat ./a.txt", "rg hello"])
def test_look_only_commands_that_stay_inside_still_run_without_asking(linked, command):
    assert not asks(linked, command)


@pytest.mark.parametrize("command", [r"cat C:\Users\me\.ssh\id_rsa", r"type ..\..\secret.txt",
                                     r"Get-Content ~\.ssh\id_rsa", "Get-Content -Path:C:/Users/me/x", "ls Env:",
                                     "dir HKLM:"])
def test_windows_paths_and_drives_ask(linked, command, monkeypatch):
    assert asks(linked, command, windows=True, monkeypatch=monkeypatch)


def test_a_backslash_path_asks_on_linux_too(linked, monkeypatch):
    assert asks(linked, r"cat ..\outside\secret", windows=False, monkeypatch=monkeypatch)


@pytest.mark.parametrize("command", ["tree -R -L 1 -H .", "tree -H . -o x", "file -C -m NAME", "file --compile x"])
def test_tree_html_and_file_compile_write_files_so_they_ask(command):
    assert not permissions.is_read_only_command(command)


def test_a_file_name_that_looks_like_an_option_makes_wildcards_ask(linked):
    (linked.root / "--pre=run.sh").write_text("")
    assert asks(linked, "rg x *")


def test_git_looks_ask_when_the_repo_config_can_run_programs(linked):
    git = linked.root / ".git"
    git.mkdir()
    (git / "config").write_text("[core]\n\tbare = false\n")
    assert not asks(linked, "git diff")
    (git / "config").write_text("[diff]\n\texternal = ./evil.sh\n")
    assert asks(linked, "git diff")
    (git / "config").write_text("[core]\n\tfsmonitor = ./evil.sh\n")
    assert asks(linked, "git status")


def test_a_mixed_line_end_file_can_still_be_edited(tmp_path):
    workspace = tools.Workspace(tmp_path)
    (tmp_path / "m.txt").write_bytes(b"one\r\ntwo\nthree\n")
    tools.edit_file(workspace, "m.txt", "two\nthree", "2\n3")
    assert (tmp_path / "m.txt").read_bytes() == b"one\r\n2\n3\n"


def test_a_pure_windows_file_keeps_its_line_ends(tmp_path):
    workspace = tools.Workspace(tmp_path)
    (tmp_path / "w.txt").write_bytes(b"one\r\ntwo\r\n")
    tools.edit_file(workspace, "w.txt", "one\ntwo", "uno\ndos")
    assert (tmp_path / "w.txt").read_bytes() == b"uno\r\ndos\r\n"


def test_writing_a_mixed_file_keeps_the_text_as_given(tmp_path):
    workspace = tools.Workspace(tmp_path)
    (tmp_path / "m.txt").write_bytes(b"one\r\ntwo\n")
    tools.write_file(workspace, "m.txt", "a\nb\n")
    assert (tmp_path / "m.txt").read_bytes() == b"a\nb\n"


class OddCharset(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=no-such-charset")
        self.end_headers()
        self.wfile.write("caf\u00e9".encode())

    def log_message(self, *args):
        pass


def test_an_unknown_charset_falls_back_instead_of_crashing():
    server = ThreadingHTTPServer(("127.0.0.1", 0), OddCharset)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        text = webfetch.fetch(f"http://127.0.0.1:{server.server_address[1]}/x")
    finally:
        server.shutdown()
    assert "caf" in text


def test_an_unexpected_tool_error_is_reported_not_raised(tmp_path, monkeypatch):
    def broken(workspace, **arguments):
        raise LookupError("no such codec")

    monkeypatch.setitem(tools.HANDLERS, "web_fetch", broken)
    assert tools.run("web_fetch", {"url": "http://x"}, tools.Workspace(tmp_path)).startswith("Error:")


def test_ctrl_d_at_a_permission_question_means_no(monkeypatch):
    monkeypatch.setattr(cli, "is_interactive", lambda: True)

    def eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    terminal = cli.Terminal(out=io.StringIO(), err=io.StringIO())
    assert terminal.ask("Run: rm x") == permissions.NO


def test_ctrl_c_inside_a_slash_command_keeps_the_chat_open(monkeypatch):
    calls = []

    def handle(session, line):
        calls.append(line)
        if line == "!sleep 9":
            raise KeyboardInterrupt
        return line != "/exit"

    lines = iter(["!sleep 9", "/exit"])
    monkeypatch.setattr(cli, "handle", handle)
    monkeypatch.setattr(cli, "read_request", lambda: next(lines))

    class Session:
        terminal = cli.Terminal(out=io.StringIO(), err=io.StringIO())
        client = type("C", (), {"model": "m"})()
        gate = type("G", (), {"mode": permissions.DEFAULT})()
        workspace = type("W", (), {"root": "/x"})()

    cli.chat(Session())
    assert calls == ["!sleep 9", "/exit"]


def test_the_summary_request_trims_big_tool_results():
    seen = []

    class Client:
        def reply(self, messages, tools, on_text):
            seen.append(messages)
            return {"content": "summary"}

    session = cli.Session.__new__(cli.Session)
    session.client = Client()
    big = [{"role": "user", "content": "go"}, {"role": "tool", "tool_call_id": "c", "content": "x" * 50_000}]
    assert session.summary_of(big) == "summary"
    sent_tool = [m for m in seen[0] if m["role"] == "tool"][0]["content"]
    assert len(sent_tool) <= cli.SUMMARY_TOOL_CHARS + 100
    assert big[1]["content"] == "x" * 50_000


def test_the_proof_is_asked_again_after_a_while(home_brain, monkeypatch):  # noqa: F811
    now = [1000.0]
    monkeypatch.setattr(brain.time, "monotonic", lambda: now[0])
    client = brain.Brain(settings.Settings(base_url=home_brain, api_key="key"))
    client.reply([{"role": "user", "content": "hi"}], tools=[], on_text=lambda text: None)
    client.reply([{"role": "user", "content": "hi"}], tools=[], on_text=lambda text: None)
    assert len(Identity.seen) == 1
    now[0] += brain.PROOF_SECONDS + 1
    client.reply([{"role": "user", "content": "hi"}], tools=[], on_text=lambda text: None)
    assert len(Identity.seen) == 2


class Redirecting(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:9/v1/chat/completions")
        self.end_headers()

    def log_message(self, *args):
        pass


def test_a_redirect_from_the_brain_is_not_followed_with_the_key():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirecting)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = brain.Brain(settings.Settings(remote_url=f"http://127.0.0.1:{server.server_address[1]}/v1", api_key="key"))
        with pytest.raises(brain.BrainError, match="redirect"):
            client.reply([{"role": "user", "content": "hi"}], tools=[], on_text=lambda text: None)
    finally:
        server.shutdown()
