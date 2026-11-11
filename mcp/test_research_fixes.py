import io
import os
import shlex
import subprocess
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))

import pytest

pytest.importorskip("mcp.server.fastmcp")
import research_server as research


@pytest.fixture
def no_remote(monkeypatch):
    monkeypatch.setattr(research, "remote_text", lambda *a, **k: pytest.fail("git was run"))


@pytest.mark.parametrize("action, args", [
    ("grep", "-Oxdg-open needle"),
    ("grep", "-iOless needle"),
    ("grep", "--open-files-in-pager=less x"),
    ("grep", "--open-files x"),
    ("clone", "-u'touch /tmp/pwned'"),
    ("clone", "-ccore.sshCommand=evil"),
    ("clone", "--upload=evil"),
    ("clone", "--template=/tmp/hooks"),
    ("clone", "--config=core.fsmonitor=evil"),
    ("log", "--output=/home/ai/.bashrc"),
    ("log", "--out=/home/ai/.bashrc"),
    ("diff", "--ext-diff"),
    ("branch", "-D main"),
    ("branch", "-m old new"),
    ("branch", "--delete main"),
    ("branch", "-f main HEAD~3"),
    ("branch", "--set-upstream-to=origin/x"),
    ("pull", "--upload-pack=evil"),
    ("clone", "--u=evil"),
    ("clone", "--up=evil"),
    ("clone", "--ex=x"),
    ("log", "--ou=/home/ai/.bashrc"),
    ("grep", "--op x"),
    ("diff", "--ex"),
    ("pull", "--rebase=interactive"),
    ("clone", "--recurse-submodules"),
])
def test_writing_or_running_options_are_refused(no_remote, action, args):
    assert research.git("myapp", action, args) == "Those git options aren't allowed here."


@pytest.mark.parametrize("action, args", [
    ("pull", "https://example.com/evil.git"),
    ("pull", "origin main"),
    ("branch", "new-branch"),
    ("branch", "new-branch HEAD~2"),
    ("clone", "https://example.com/other.git"),
    ("branch", "-- -weird"),
    ("pull", "--depth https://example.com/evil.git"),
])
def test_pull_branch_and_clone_take_no_extra_names(no_remote, action, args):
    assert research.git("https://example.com/x.git" if action == "clone" else "myapp", action, args) == \
        "Those git options aren't allowed here."


@pytest.mark.parametrize("action, args", [
    ("log", "--author Sam --since=2.weeks"),
    ("grep", "-A3 -e todo"),
    ("diff", "--stat HEAD~1 -- src"),
    ("branch", "--list 'feat*'"),
    ("branch", "--contains HEAD~1"),
    ("clone", "--depth 1"),
    ("pull", "--ff-only"),
    ("status", "--short --branch"),
    ("log", "-p -3"),
])
def test_more_reading_forms_are_allowed(monkeypatch, action, args):
    commands = []
    monkeypatch.setattr(research, "remote_text", lambda command, **k: commands.append(command) or (0, "ok"))
    repo = "https://example.com/x.git" if action == "clone" else "myapp"
    assert research.git(repo, action, args).startswith("exit code 0"), args
    assert commands


@pytest.mark.parametrize("action, args", [
    ("log", "--oneline -20"),
    ("status", "-uno"),
    ("grep", "-n -i needle"),
    ("grep", "-c needle"),
    ("branch", "-a"),
    ("branch", "--list -v"),
    ("show", "HEAD~1 --stat"),
    ("grep", "-- -O"),
])
def test_ordinary_reading_options_are_allowed(monkeypatch, action, args):
    commands = []
    monkeypatch.setattr(research, "remote_text", lambda command, **k: commands.append(command) or (0, "ok"))
    assert research.git("myapp", action, args).startswith("exit code 0")
    assert commands


def test_git_tool_text_says_clone_and_pull_change_files():
    assert "change files" in research.git.__doc__ or "write" in research.git.__doc__


class FakeConnectionResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "LIBRARY_DB", str(tmp_path / "library.db"))
    listing = "1.0\t10\t/home/ai/library/bad.pdf\n2.0\t10\t/home/ai/library/good.txt\n"
    monkeypatch.setattr(research, "remote_text", lambda command, **k: (0, listing))
    monkeypatch.setattr(research, "_extract", lambda path: (0, "bad text" if "bad" in path else "good text"))
    return tmp_path


def refuse_bad(texts):
    if any("bad text" in text for text in texts):
        raise research.EmbedRefused("Ollama said 500: input too long")
    return [[1.0, 0.0] for _ in texts]


def test_a_file_that_cannot_be_embedded_is_recorded_not_retried(library, monkeypatch):
    calls = []
    monkeypatch.setattr(research, "_embed", lambda texts: calls.append(texts) or refuse_bad(texts))
    status = research._index_library(force=True)
    assert "1 unreadable" in status
    calls.clear()
    research._index_library(force=True)
    assert calls == []


def test_ollama_down_does_not_mark_files_as_failed(library, monkeypatch):
    def down(texts):
        raise research.OllamaUnreachable("could not reach Ollama: refused")
    monkeypatch.setattr(research, "_embed", down)
    with pytest.raises(research.OllamaUnreachable):
        research._index_library(force=True)
    connection = research._db()
    try:
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    finally:
        connection.close()


def test_embed_errors_are_sorted(monkeypatch):
    def missing(*args, **kwargs):
        raise urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b'{"error":"model \\"x\\" not found"}'))
    monkeypatch.setattr(research.urllib.request, "urlopen", missing)
    with pytest.raises(research.EmbedModelMissing):
        research._embed(["x"])

    def refused(*args, **kwargs):
        raise urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b"input too long"))
    monkeypatch.setattr(research.urllib.request, "urlopen", refused)
    with pytest.raises(research.EmbedRefused):
        research._embed(["x"])

    def down(*args, **kwargs):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(research.urllib.request, "urlopen", down)
    with pytest.raises(research.OllamaUnreachable):
        research._embed(["x"])


def test_reindex_reports_errors_instead_of_raising(library, monkeypatch):
    def down(texts):
        raise research.OllamaUnreachable("could not reach Ollama: refused")
    monkeypatch.setattr(research, "_embed", down)
    research.reindex_library()
    research.index_thread_done(timeout=5)
    assert "could not reach Ollama" in research.reindex_library()
    research.index_thread_done(timeout=5)


def test_reindex_returns_at_once_and_indexes_in_the_background(library, monkeypatch):
    release = research.threading.Event()

    def slow(texts):
        release.wait(5)
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(research, "_embed", slow)
    started = research.reindex_library()
    busy = research.reindex_library()
    release.set()
    research.index_thread_done(timeout=5)
    assert "background" in started
    assert "Already" in busy
    monkeypatch.setattr(research, "_index_in_background", lambda: "up to date")
    monkeypatch.setattr(research, "_embed", lambda texts: [[1.0, 0.0]])
    assert "good.txt" in research.search_library("good")


def test_search_does_not_wait_for_indexing(library, monkeypatch):
    started = []
    monkeypatch.setattr(research, "_index_in_background", lambda: started.append(True) or "indexing")
    monkeypatch.setattr(research, "_embed", lambda texts: [[1.0, 0.0]])
    result = research.search_library("anything")
    assert started == [True]
    assert "being indexed" in result or "empty" in result


def test_background_indexing_runs_once_at_a_time(library, monkeypatch):
    research.index_lock.acquire()
    try:
        assert research._index_in_background() == "already indexing"
    finally:
        research.index_lock.release()


def test_background_indexing_fills_the_library(library, monkeypatch):
    monkeypatch.setattr(research, "_embed", lambda texts: [[1.0, 0.0] for _ in texts])
    research._index_in_background()
    research.index_thread_done(timeout=5)
    monkeypatch.setattr(research, "_index_in_background", lambda: "up to date")
    result = research.search_library("good")
    assert "good.txt" in result


def test_search_says_when_the_model_is_missing(library, monkeypatch):
    monkeypatch.setattr(research, "_index_in_background", lambda: "up to date")

    def missing(texts):
        raise research.EmbedModelMissing("not installed")
    monkeypatch.setattr(research, "_embed", missing)
    assert "ai-pull" in research.search_library("x")


def test_downloaded_documents_never_overwrite_an_existing_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Downloads" / "report.pdf").write_text("mine")
    (home / "Downloads" / "report (1).pdf").write_text("mine too")
    script = research.download_script("https://example.com/files/report.pdf")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && { shift; printf downloaded > "$1"; }; shift; done\n')
    os.chmod(fake_bin / "curl", 0o755)
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          env={"HOME": str(home), "PATH": f"{fake_bin}:/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "report (2).pdf"
    assert (home / "Downloads" / "report.pdf").read_text() == "mine"
    assert (home / "Downloads" / "report (2).pdf").read_text() == "downloaded"


@pytest.mark.parametrize("url, expected", [
    ("https://example.com/", "document"),
    ("https://example.com/..", "document"),
    ("https://example.com/a%20b.pdf", "a b.pdf"),
    ("https://example.com/-rf", "rf"),
])
def test_download_names_are_safe(url, expected):
    assert research.download_name(url) == expected


def test_read_document_uses_the_name_the_download_got(monkeypatch):
    commands = []

    def fake_remote(command, **kwargs):
        commands.append(command)
        if "doc2text" in command:
            return 0, "text of the document"
        return 0, "report (2).pdf\n"
    monkeypatch.setattr(research, "remote_text", fake_remote)
    assert research.read_document("https://example.com/report.pdf") == "text of the document"
    assert "realpath -e -- \"$HOME\"/Downloads/" + shlex.quote("report (2).pdf") + ")" in commands[-1]
    assert commands[-1].endswith('doc2text "$target"')
