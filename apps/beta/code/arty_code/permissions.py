import difflib
import glob
import os
import re
import shlex
from urllib.parse import urlparse

from arty_code import tools

YES, NO, ALWAYS = "y", "n", "a"
MAX_PREVIEW_CHARS = 6_000
READ_ONLY_PROGRAMS = {"ls", "pwd", "cat", "head", "tail", "wc", "grep", "rg", "find", "tree", "which", "file", "stat",
                      "du", "diff", "dir", "type", "get-childitem", "get-content", "select-string", "get-location"}
READ_ONLY_GIT = {"status", "diff", "log", "show", "branch", "blame", "rev-parse", "ls-files"}
FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
RG_RUNNING_OPTIONS = ("--pre", "--hostname-bin")
GIT_WRITING_OPTIONS = ("--output", "--ext-diff", "--exec-path")
BRANCH_LISTING_FLAGS = {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "-l", "--list", "--show-current",
                        "--color", "--no-color", "--column", "--no-column", "-i", "--ignore-case", "--abbrev",
                        "--no-abbrev", "--omit-empty", "-q", "--quiet"}
BRANCH_FILTERS = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format"}
BRANCH_LIST_ONLY = {"-l", "--list"}
SHELL_CONTROL = re.compile(r"[;&|<>`$\n]")
ON_WINDOWS = os.name == "nt"
GLOB_CHARACTERS = set("*?[")
DRIVE_OR_PROVIDER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:")
OPTION_PATH_CHARACTERS = set("/\\~:")
HOME_FORMS = ("~", "~/")
TREE_WRITING_LETTERS = set("oHR")
FILE_COMPILE_LETTERS = set("C")
LINK_FOLLOWING_LETTERS = {"grep": set("R"), "rg": set("L"), "ls": set("L"), "du": set("L"), "tree": set("l"),
                          "stat": set("L")}
LINK_FOLLOWING_LONG = ("--dereference-recursive", "--follow", "--dereference")
FIND_LINK_OPTIONS = {"-L", "-follow"}
GIT_RISKY_CONFIG = ("external", "textconv", "fsmonitor", "include", "pager")
GROUPING = set("(){}")
QUOTES = "'\""
READING_TOOLS = ("read_file", "list_files", "search")
FREE_TOOLS = ("command_output", "stop_command", "todo_write")
EDITS = "edits"
DEFAULT, AUTO_EDIT, PLAN, ALLOW_ALL = "default", "auto-edit", "plan", "allow-all"
MODES = (DEFAULT, AUTO_EDIT, PLAN, ALLOW_ALL)
REFUSED = "The user didn't allow that, so it wasn't done. Don't try another way round it; say so and ask what they'd like."
PLAN_REFUSAL = ("Plan mode is on: nothing may be changed or run except looking. Finish looking, then set out the plan "
                "step by step and ask the user to switch plan mode off (/mode default) to carry it out.")


def refuse(question: str, preview: str = "") -> str:
    return NO


def words_of(command: str) -> list[str]:
    try:
        if ON_WINDOWS:
            return [word.strip(QUOTES) for word in shlex.split(command, posix=False)]
        return shlex.split(command)
    except ValueError:
        return []


def has_bare_grouping(command: str) -> bool:
    quote = ""
    for character in command:
        if quote:
            quote = "" if character == quote else quote
        elif character in QUOTES:
            quote = character
        elif character in GROUPING:
            return True
    return False


def option_name(word: str) -> str:
    return word.split("=", 1)[0]


def is_branch_listing(arguments: list[str]) -> bool:
    listing_only = bool(BRANCH_LIST_ONLY & set(arguments))
    value_due = False
    for word in arguments:
        if value_due:
            value_due = False
        elif option_name(word) in BRANCH_FILTERS:
            value_due = "=" not in word
        elif word in BRANCH_LISTING_FLAGS or option_name(word) in BRANCH_LISTING_FLAGS:
            continue
        elif word.startswith("-") or not listing_only:
            return False
    return True


def is_read_only_git(arguments: list[str]) -> bool:
    if not arguments or arguments[0] not in READ_ONLY_GIT:
        return False
    if any(word.startswith(GIT_WRITING_OPTIONS) for word in arguments):
        return False
    return arguments[0] != "branch" or is_branch_listing(arguments[1:])


def short_letters(word: str) -> set[str]:
    return set(word[1:]) if word.startswith("-") and not word.startswith("--") else set()


def writes_tree_output(arguments: list[str]) -> bool:
    return any(word.startswith("--o") or TREE_WRITING_LETTERS & short_letters(word) for word in arguments)


def compiles_magic(arguments: list[str]) -> bool:
    return any(word.startswith("--compile") or FILE_COMPILE_LETTERS & short_letters(word) for word in arguments)


def is_read_only_command(command: str) -> bool:
    if SHELL_CONTROL.search(command) or has_bare_grouping(command):
        return False
    words = words_of(command)
    if not words:
        return False
    program, arguments = words[0].lower(), words[1:]
    if program == "git":
        return is_read_only_git(arguments)
    if program == "find":
        return not FIND_ACTIONS & set(arguments)
    if program == "rg" and any(word.startswith(RG_RUNNING_OPTIONS) for word in arguments):
        return False
    if program == "tree" and writes_tree_output(arguments):
        return False
    if program == "file" and compiles_magic(arguments):
        return False
    return program in READ_ONLY_PROGRAMS


def follows_links(program: str, arguments: list[str]) -> bool:
    if program == "find":
        return bool(FIND_LINK_OPTIONS & set(arguments))
    letters = LINK_FOLLOWING_LETTERS.get(program, set())
    return any(word.startswith(LINK_FOLLOWING_LONG) or letters & short_letters(word) for word in arguments)


def git_config_runs_programs(root) -> bool:
    git = root / ".git"
    if git.is_file():
        return True
    try:
        config = (git / "config").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return any(key in config for key in GIT_RISKY_CONFIG)


def named_words(arguments: list[str]) -> tuple[list[str], bool]:
    found, unclear = [], False
    for word in arguments:
        if word.startswith("--"):
            found += [word.split("=", 1)[1]] if "=" in word else []
        elif word.startswith("-") and len(word) > 1:
            unclear = unclear or bool(OPTION_PATH_CHARACTERS & set(word))
        else:
            found.append(word)
    return [word for word in found if word], unclear


def unusual_home(word: str) -> bool:
    return word.startswith("~") and word != HOME_FORMS[0] and not word.startswith(HOME_FORMS[1])


def as_local_path(word: str) -> str:
    return word.replace("\\", "/") if ON_WINDOWS else word
def diff_preview(workspace, name: str, arguments: dict) -> str:
    try:
        path, before, after = tools.planned_change(name, arguments, workspace)
    except (tools.ToolError, OSError):
        return ""
    shown = workspace.shown(path)
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile=shown, tofile=shown, lineterm="")
    preview = "\n".join(lines)
    return preview if len(preview) <= MAX_PREVIEW_CHARS else preview[:MAX_PREVIEW_CHARS] + "\n... (diff cut)"


class Gate:
    def __init__(self, workspace, ask, mode=DEFAULT):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self.workspace = workspace
        self.ask = ask
        self.mode = mode
        self.always = set()

    def outside(self, arguments: dict) -> bool:
        path = arguments.get("path")
        if not path:
            return False
        try:
            return not self.workspace.contains(str(path))
        except tools.ToolError:
            return False

    def reaches_outside(self, path: str) -> bool:
        try:
            return not self.workspace.contains(path)
        except (tools.ToolError, OSError, ValueError):
            return True

    def lists_outside(self, arguments: dict) -> bool:
        try:
            folder, _rest = tools.listing_folder(self.workspace, str(arguments.get("pattern") or ""),
                                                 str(arguments.get("path") or "."))
        except (tools.ToolError, OSError, ValueError):
            return False
        return not folder.is_relative_to(self.workspace.root)

    def confirm(self, question: str, preview: str = "", remember: str = "") -> str | None:
        answer = self.ask(question, preview)
        if answer == ALWAYS and remember:
            self.always.add(remember)
        return None if answer in (YES, ALWAYS) else REFUSED

    def check(self, name: str, arguments: dict) -> str | None:
        if self.mode == ALLOW_ALL or name in FREE_TOOLS:
            return None
        if name in READING_TOOLS:
            if name == "list_files" and self.lists_outside(arguments):
                return self.confirm(f"List files outside the project: {arguments.get('pattern')}?")
            if not self.outside(arguments):
                return None
            return self.confirm(f"Read outside the project: {arguments.get('path')}?")
        if name == "web_fetch":
            return self.check_fetch(str(arguments.get("url", "")))
        if name in tools.CHANGING_TOOLS:
            return PLAN_REFUSAL if self.mode == PLAN else self.check_change(name, arguments)
        if name == "run_command":
            return self.check_command(str(arguments.get("command", "")), bool(arguments.get("background")))
        return None

    def allows(self, name: str, arguments: dict) -> bool:
        return self.check(name, arguments) is None

    def check_fetch(self, url: str) -> str | None:
        site = urlparse(url).netloc
        if site and f"site:{site}" in self.always:
            return None
        return self.confirm(f"Fetch {url}?", remember=f"site:{site}" if site else "")

    def check_change(self, name: str, arguments: dict) -> str | None:
        preview = diff_preview(self.workspace, name, arguments)
        if self.outside(arguments):
            return self.confirm(f"Change a file outside the project: {arguments.get('path')}?", preview)
        if self.mode == AUTO_EDIT or EDITS in self.always:
            return None
        verb = "Write" if name == "write_file" else "Edit"
        return self.confirm(f"{verb} {arguments.get('path')}?", preview, remember=EDITS)

    def check_command(self, command: str, background: bool) -> str | None:
        if is_read_only_command(command) and not background:
            return self.check_looking(command)
        if self.mode == PLAN:
            return PLAN_REFUSAL
        if command in self.always:
            return None
        label = "Start in the background" if background else "Run"
        return self.confirm(f"{label}: {command}", remember=command)

    def check_looking(self, command: str) -> str | None:
        if command in self.always or not self.looks_outside(command):
            return None
        return self.confirm(f"Look outside the project: {command}?", remember=command)

    def looks_outside(self, command: str) -> bool:
        if not ON_WINDOWS and "\\" in command:
            return True
        words = words_of(command)
        if not words:
            return True
        program, arguments = words[0].lower(), words[1:]
        if follows_links(program, arguments):
            return True
        if program == "git" and git_config_runs_programs(self.workspace.root):
            return True
        named, unclear = named_words(arguments)
        return unclear or any(self.word_reaches_outside(word) for word in named)

    def word_reaches_outside(self, word: str) -> bool:
        if unusual_home(word) or DRIVE_OR_PROVIDER.match(word):
            return True
        path = as_local_path(word)
        if not GLOB_CHARACTERS & set(path):
            return self.reaches_outside(path)
        matches = glob.glob(path, root_dir=self.workspace.root)
        if any(os.path.basename(match).startswith("-") for match in matches):
            return True
        return any(self.reaches_outside(match) for match in matches or [path])
