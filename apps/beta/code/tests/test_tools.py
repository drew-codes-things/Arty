import sys

import pytest

from arty_code import tools


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n")
    return tools.Workspace(tmp_path)


def test_reading_a_file_numbers_its_lines(workspace):
    assert tools.run("read_file", {"path": "src/app.py"}, workspace) == "1\tdef add(a, b):\n2\t    return a - b"


def test_reading_part_of_a_file(workspace):
    assert tools.run("read_file", {"path": "src/app.py", "offset": 2, "limit": 1}, workspace) == "2\t    return a - b"


def test_editing_replaces_the_one_matching_text(workspace):
    result = tools.run("edit_file", {"path": "src/app.py", "old_text": "a - b", "new_text": "a + b"}, workspace)
    assert "src/app.py" in result
    assert (workspace.root / "src" / "app.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_editing_refuses_text_that_is_not_there(workspace):
    result = tools.run("edit_file", {"path": "src/app.py", "old_text": "a * b", "new_text": "x"}, workspace)
    assert result.startswith("Error")
    assert "not found" in result


def test_editing_refuses_text_that_matches_twice_unless_asked(workspace):
    (workspace.root / "twice.txt").write_text("x\nx\n")
    refused = tools.run("edit_file", {"path": "twice.txt", "old_text": "x", "new_text": "y"}, workspace)
    assert refused.startswith("Error") and "2 places" in refused
    tools.run("edit_file", {"path": "twice.txt", "old_text": "x", "new_text": "y", "replace_all": True}, workspace)
    assert (workspace.root / "twice.txt").read_text() == "y\ny\n"


def test_writing_creates_missing_folders(workspace):
    tools.run("write_file", {"path": "docs/notes/a.md", "content": "hello\n"}, workspace)
    assert (workspace.root / "docs" / "notes" / "a.md").read_text() == "hello\n"


def test_listing_skips_version_control_folders(workspace):
    listed = tools.run("list_files", {"pattern": "**/*"}, workspace).splitlines()
    assert "src/app.py" in listed
    assert not any(line.startswith(".git") for line in listed)


def test_searching_names_file_and_line(workspace):
    assert tools.run("search", {"pattern": r"return a"}, workspace) == "src/app.py:2:    return a - b"


def test_a_bad_search_pattern_is_reported(workspace):
    assert tools.run("search", {"pattern": "("}, workspace).startswith("Error")


def test_a_missing_file_is_an_error_not_a_crash(workspace):
    assert tools.run("read_file", {"path": "nope.py"}, workspace).startswith("Error")


def test_an_unknown_tool_is_an_error(workspace):
    assert tools.run("format_disk", {}, workspace).startswith("Error")


def test_running_a_command_reports_output_and_exit_code(workspace):
    command = f'"{sys.executable}" -c "print(6 * 7); raise SystemExit(3)"'
    result = tools.run("run_command", {"command": command}, workspace)
    assert "42" in result
    assert "exit code 3" in result


def test_a_command_that_runs_too_long_is_stopped(workspace):
    command = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    result = tools.run("run_command", {"command": command, "timeout_seconds": 1}, workspace)
    assert "stopped after 1 s" in result


def test_paths_outside_the_project_are_recognised(workspace, tmp_path):
    assert workspace.contains("src/app.py")
    assert not workspace.contains("../elsewhere.txt")
    assert not workspace.contains(str(tmp_path.parent / "x"))


def test_the_change_an_edit_would_make_can_be_previewed(workspace):
    path, before, after = tools.planned_change("edit_file", {"path": "src/app.py", "old_text": "a - b", "new_text": "a + b"}, workspace)
    assert path.name == "app.py"
    assert "a - b" in before and "a + b" in after
    assert (workspace.root / "src" / "app.py").read_text().endswith("a - b\n")


def test_every_tool_has_a_definition_the_model_can_read():
    names = {definition["function"]["name"] for definition in tools.DEFINITIONS}
    assert names == set(tools.HANDLERS)
    assert all(definition["type"] == "function" for definition in tools.DEFINITIONS)


def test_the_todo_list_is_kept_and_shown_as_a_checklist(workspace):
    items = [{"text": "read the parser", "status": "done"}, {"text": "fix the bug", "status": "in_progress"},
             {"text": "run the tests", "status": "pending"}]
    result = tools.run("todo_write", {"items": items}, workspace)
    assert result.splitlines() == ["[x] read the parser", "[~] fix the bug", "[ ] run the tests"]
    assert workspace.todos == items


def test_a_todo_with_an_unknown_status_is_refused(workspace):
    assert tools.run("todo_write", {"items": [{"text": "x", "status": "maybe"}]}, workspace).startswith("Error")


def test_a_background_command_can_be_read_and_stopped(workspace):
    command = f'"{sys.executable}" -u -c "import time; print(\'ready\'); time.sleep(30)"'
    started = tools.run("run_command", {"command": command, "background": True}, workspace)
    job = started.split()[1]
    output = ""
    for _ in range(50):
        output += tools.run("command_output", {"job": job}, workspace)
        if "ready" in output:
            break
        __import__("time").sleep(0.1)
    assert "ready" in output and "still running" in output
    assert "stopped" in tools.run("stop_command", {"job": job}, workspace)


def test_an_unknown_background_job_is_an_error(workspace):
    assert tools.run("command_output", {"job": "9"}, workspace).startswith("Error")


def test_editing_a_windows_file_keeps_its_line_endings(workspace):
    target = workspace.root / "run.bat"
    target.write_bytes(b"@echo off\r\necho one\r\necho two\r\n")
    tools.run("edit_file", {"path": "run.bat", "old_text": "echo one\necho two", "new_text": "echo uno\necho two"},
              workspace)
    assert target.read_bytes() == b"@echo off\r\necho uno\r\necho two\r\n"


def test_editing_a_unix_file_keeps_its_line_endings(workspace):
    target = workspace.root / "run.sh"
    target.write_bytes(b"one\ntwo\n")
    tools.run("edit_file", {"path": "run.sh", "old_text": "one", "new_text": "uno\nmore"}, workspace)
    assert target.read_bytes() == b"uno\nmore\ntwo\n"


def test_rewriting_a_windows_file_keeps_its_line_endings(workspace):
    target = workspace.root / "run.bat"
    target.write_bytes(b"a\r\nb\r\n")
    tools.run("write_file", {"path": "run.bat", "content": "a\nc\n"}, workspace)
    assert target.read_bytes() == b"a\r\nc\r\n"


def test_a_new_file_is_written_exactly_as_given(workspace):
    tools.run("write_file", {"path": "new.txt", "content": "a\nb\n"}, workspace)
    assert (workspace.root / "new.txt").read_bytes() == b"a\nb\n"


def test_reading_a_windows_file_shows_clean_lines(workspace):
    (workspace.root / "w.txt").write_bytes(b"one\r\ntwo\r\n")
    assert tools.run("read_file", {"path": "w.txt"}, workspace) == "1\tone\n2\ttwo"


def test_listing_with_dot_dot_reaches_the_folder_above_only_when_run(workspace):
    (workspace.root.parent / "beside.txt").write_text("x")
    listed = tools.run("list_files", {"pattern": "../*.txt"}, workspace)
    assert str(workspace.root.parent / "beside.txt") in listed


def test_dot_dot_after_a_wildcard_is_refused(workspace):
    assert tools.run("list_files", {"pattern": "*/../*"}, workspace).startswith("Error")


def test_listing_patterns_match_like_globs(workspace):
    (workspace.root / "top.py").write_text("")
    (workspace.root / "src" / "deep").mkdir()
    (workspace.root / "src" / "deep" / "x.py").write_text("")
    assert tools.run("list_files", {"pattern": "*.py"}, workspace).splitlines() == ["top.py"]
    assert tools.run("list_files", {"pattern": "src/*.py"}, workspace).splitlines() == ["src/app.py"]
    assert tools.run("list_files", {"pattern": "**/*.py"}, workspace).splitlines() == [
        "src/app.py", "src/deep/x.py", "top.py"]


def test_skipped_folders_are_never_walked(workspace, monkeypatch):
    (workspace.root / "node_modules" / "pkg").mkdir(parents=True)
    (workspace.root / "node_modules" / "pkg" / "a.js").write_text("")
    entered = []
    real_scandir = tools.os.scandir

    def watched(path="."):
        entered.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr(tools.os, "scandir", watched)
    tools.run("search", {"pattern": "x"}, workspace)
    assert entered
    assert not any("node_modules" in path or ".git" in path for path in entered)


def test_search_skips_files_linked_from_outside_the_project(workspace, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("secret")
    (elsewhere / "token.txt").write_text("password = hunter2\n")
    (workspace.root / "leak.txt").symlink_to(elsewhere / "token.txt")
    assert "hunter2" not in tools.run("search", {"pattern": "password"}, workspace)


def test_windows_runs_powershell_by_its_full_path(monkeypatch):
    monkeypatch.setattr(tools.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    arguments, _options = tools.shell_command("dir")
    assert arguments[0].lower().endswith("windowspowershell\\v1.0\\powershell.exe")
    assert arguments[0].lower().startswith("c:\\windows\\system32")


def test_background_output_keeps_only_the_tail(workspace, monkeypatch):
    monkeypatch.setattr(tools, "MAX_JOB_OUTPUT_CHARS", 100)
    command = f'"{sys.executable}" -u -c "print(\'\\n\'.join(str(n) for n in range(200)))"'
    job = tools.run("run_command", {"command": command, "background": True}, workspace).split()[1]
    running = workspace.jobs[job]
    running.process.wait()
    for _ in range(50):
        if "199" in "".join(running.output):
            break
        __import__("time").sleep(0.05)
    assert sum(len(line) for line in running.output) <= 100
    output = tools.run("command_output", {"job": job}, workspace)
    assert "199" in output and "earlier lines dropped" in output


def test_stopping_every_job_ends_background_commands(workspace):
    command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    job = tools.run("run_command", {"command": command, "background": True}, workspace).split()[1]
    workspace.stop_jobs()
    assert workspace.jobs[job].process.poll() is not None


def test_ctrl_c_during_a_command_stops_it(workspace, monkeypatch):
    command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    started = []
    real_start = tools.start_process

    def interrupted(workspace, command):
        process = real_start(workspace, command)
        started.append(process)
        monkeypatch.setattr(process, "communicate", lambda timeout=None: (_ for _ in ()).throw(KeyboardInterrupt))
        return process

    monkeypatch.setattr(tools, "start_process", interrupted)
    with pytest.raises(KeyboardInterrupt):
        tools.run("run_command", {"command": command}, workspace)
    assert started[0].wait(timeout=5) is not None
