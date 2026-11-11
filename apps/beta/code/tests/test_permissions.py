import pytest

from arty_code import permissions, tools


class Asker:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions = []

    def __call__(self, question, preview=""):
        self.questions.append((question, preview))
        return self.answers.pop(0) if self.answers else "n"


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    return tools.Workspace(tmp_path)


@pytest.mark.parametrize("command", ["ls -la", "git status", "git diff HEAD~1", "cat a.py", "pwd", "grep -n x a.py"])
def test_looking_around_needs_no_ok(command):
    assert permissions.is_read_only_command(command)


@pytest.mark.parametrize("command", ["rm -rf build", "ls; rm a.py", "cat a.py > b.py", "git push", "git checkout .",
                                     "echo $(whoami)", "find . -delete", "find . -exec rm {} ;", "ls | sh", "cat `x`"])
def test_anything_that_changes_or_chains_needs_an_ok(command):
    assert not permissions.is_read_only_command(command)


def test_reading_inside_the_project_is_allowed_without_asking(workspace):
    ask = Asker()
    gate = permissions.Gate(workspace, ask)
    assert gate.allows("read_file", {"path": "a.py"})
    assert ask.questions == []


def test_reading_outside_the_project_asks(workspace):
    ask = Asker("y")
    gate = permissions.Gate(workspace, ask)
    assert gate.allows("read_file", {"path": "/etc/hostname"})
    assert "outside" in ask.questions[0][0]


def test_an_edit_asks_and_shows_the_diff(workspace):
    ask = Asker("y")
    gate = permissions.Gate(workspace, ask)
    assert gate.allows("edit_file", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"})
    question, preview = ask.questions[0]
    assert "a.py" in question
    assert "-x = 1" in preview and "+x = 2" in preview


def test_no_means_no(workspace):
    gate = permissions.Gate(workspace, Asker("n"))
    assert not gate.allows("write_file", {"path": "b.py", "content": "y = 2\n"})


def test_always_covers_later_edits_in_the_session(workspace):
    ask = Asker("a")
    gate = permissions.Gate(workspace, ask)
    assert gate.allows("write_file", {"path": "b.py", "content": "1\n"})
    assert gate.allows("edit_file", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 3"})
    assert len(ask.questions) == 1


def test_always_for_a_command_covers_only_that_command(workspace):
    ask = Asker("a", "n")
    gate = permissions.Gate(workspace, ask)
    assert gate.allows("run_command", {"command": "pytest -q"})
    assert gate.allows("run_command", {"command": "pytest -q"})
    assert not gate.allows("run_command", {"command": "rm -rf /"})
    assert len(ask.questions) == 2


def test_auto_edit_skips_asking_inside_the_project_only(workspace):
    ask = Asker("n")
    gate = permissions.Gate(workspace, ask, mode=permissions.AUTO_EDIT)
    assert gate.allows("write_file", {"path": "b.py", "content": "1\n"})
    assert not gate.allows("write_file", {"path": "../outside.py", "content": "1\n"})
    assert len(ask.questions) == 1


def test_read_only_commands_run_without_asking(workspace):
    ask = Asker()
    assert permissions.Gate(workspace, ask).allows("run_command", {"command": "git status"})
    assert ask.questions == []


def test_without_a_terminal_nothing_risky_is_allowed(workspace):
    gate = permissions.Gate(workspace, permissions.refuse)
    assert not gate.allows("run_command", {"command": "make"})
    assert not gate.allows("edit_file", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"})
    assert gate.allows("read_file", {"path": "a.py"})


def test_plan_mode_looks_but_never_changes(workspace):
    ask = Asker("y")
    gate = permissions.Gate(workspace, ask, mode=permissions.PLAN)
    assert gate.allows("read_file", {"path": "a.py"})
    assert gate.allows("run_command", {"command": "git log"})
    assert gate.check("edit_file", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"}) == permissions.PLAN_REFUSAL
    assert gate.check("run_command", {"command": "make"}) == permissions.PLAN_REFUSAL
    assert ask.questions == []


def test_allow_all_never_asks(workspace):
    ask = Asker()
    gate = permissions.Gate(workspace, ask, mode=permissions.ALLOW_ALL)
    assert gate.allows("run_command", {"command": "make install"})
    assert gate.allows("write_file", {"path": "../elsewhere.py", "content": ""})
    assert ask.questions == []


def test_fetching_a_site_asks_once_per_site_when_told_always(workspace):
    ask = Asker("a", "n")
    gate = permissions.Gate(workspace, ask)
    assert gate.allows("web_fetch", {"url": "https://docs.python.org/3/library/json.html"})
    assert gate.allows("web_fetch", {"url": "https://docs.python.org/3/library/re.html"})
    assert not gate.allows("web_fetch", {"url": "https://example.com/"})
    assert len(ask.questions) == 2


def test_reading_background_output_needs_no_ok(workspace):
    ask = Asker()
    gate = permissions.Gate(workspace, ask)
    assert gate.allows("command_output", {"job": "1"}) and gate.allows("todo_write", {"items": []})
    assert ask.questions == []


@pytest.mark.parametrize("command", [
    "rg --pre ./run.sh x", "rg --pre=./run.sh x", "rg --pre-glob '*.py' --pre sh x", "rg --hostname-bin ./x y",
    "find . -fprint0 out", "find . -fprint out", "tree -o out.txt", "tree -aoout.txt",
    "git log --output=a.py", "git diff --output a.py", "git show --output=x HEAD", "git diff --ext-diff",
    "git branch -D main", "git branch -d old", "git branch -m new", "git branch -M x y", "git branch -c a b",
    "git branch --delete x", "git branch --move a b", "git branch --set-upstream-to=origin/x",
    "git branch -u origin/x", "git branch --unset-upstream", "git branch --edit-description", "git branch newname",
    "git branch -f main HEAD~1",
    "Get-Content (Remove-Item a.py -Force)", "Get-Content $(Remove-Item x)", "Get-ChildItem {Remove-Item x}",
    "Get-Content @(Remove-Item x)",
])
def test_read_only_programs_with_writing_or_running_options_need_an_ok(command):
    assert not permissions.is_read_only_command(command)


@pytest.mark.parametrize("command", [
    "rg -n todo", "rg --glob '*.py' x", "find . -name '*.py' -print", "tree -L 2", "git log --oneline -5",
    "git diff --stat", "git branch", "git branch -a", "git branch -vv", "git branch --list 'feat*'",
    "git branch --contains HEAD", "git branch --show-current", "git branch --sort=-committerdate",
    "grep -n 'def f(x)' a.py", "Get-Content a.py",
])
def test_ordinary_looking_around_still_needs_no_ok(command):
    assert permissions.is_read_only_command(command)


@pytest.mark.parametrize("command", ["cat ~/.ssh/id_rsa", "cat /etc/passwd", "grep -r token ~/.config",
                                     "cat ../secret.txt", "head -n 5 /home/someone/.netrc", "ls ..",
                                     "rg --file=/etc/shadow x", "cat sub/../../x"])
def test_looking_outside_the_project_with_a_command_asks(workspace, command):
    ask = Asker("n")
    gate = permissions.Gate(workspace, ask)
    assert not gate.allows("run_command", {"command": command})
    assert "outside" in ask.questions[0][0]


def test_looking_outside_with_a_command_asks_even_in_plan_mode(workspace):
    ask = Asker("y")
    gate = permissions.Gate(workspace, ask, mode=permissions.PLAN)
    assert gate.allows("run_command", {"command": "cat /etc/hostname"})
    assert len(ask.questions) == 1


def test_a_symlink_out_of_the_project_counts_as_outside(workspace, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    (workspace.root / "link").symlink_to(elsewhere)
    ask = Asker("n")
    assert not permissions.Gate(workspace, ask).allows("run_command", {"command": "ls link"})


def test_listing_with_a_pattern_that_climbs_out_asks(workspace):
    ask = Asker("n", "n")
    gate = permissions.Gate(workspace, ask)
    assert not gate.allows("list_files", {"pattern": "../*"})
    assert not gate.allows("list_files", {"pattern": "/etc/*"})
    assert gate.allows("list_files", {"pattern": "**/*.py"})
    assert len(ask.questions) == 2
