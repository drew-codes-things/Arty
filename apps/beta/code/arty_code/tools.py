import collections
import fnmatch
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path

from arty_code import webfetch

DEFAULT_READ_LINES = 2000
MAX_LINE_CHARS = 2000
MAX_LISTED_FILES = 500
MAX_SEARCH_MATCHES = 200
MAX_SEARCHED_FILE_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 20_000
OUTPUT_HEAD_CHARS = 8_000
DEFAULT_COMMAND_SECONDS = 120
MAX_COMMAND_SECONDS = 600
MAX_JOB_OUTPUT_CHARS = 200_000
GLOB_CHARACTERS = set("*?[")
ANY_FOLDERS = "**"
WINDOWS_LINE_END = "\r\n"
UNIX_LINE_END = "\n"
SKIPPED_FOLDERS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
                   ".pytest_cache", ".gradle", "build", "dist"}
CHANGING_TOOLS = ("write_file", "edit_file")
TODO_MARKS = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}


class ToolError(Exception):
    pass


class Job:
    def __init__(self, command: str, process: subprocess.Popen):
        self.command = command
        self.process = process
        self.output = collections.deque()
        self.kept_chars = 0
        self.read_upto = 0
        self.missed = 0
        self.lock = threading.Lock()
        threading.Thread(target=self.collect, daemon=True).start()

    def collect(self) -> None:
        for line in self.process.stdout:
            with self.lock:
                self.keep(line)

    def keep(self, line: str) -> None:
        self.output.append(line)
        self.kept_chars += len(line)
        while self.kept_chars > MAX_JOB_OUTPUT_CHARS and self.output:
            self.kept_chars -= len(self.output.popleft())
            if self.read_upto:
                self.read_upto -= 1
            else:
                self.missed += 1

    def unread(self) -> str:
        with self.lock:
            fresh = "".join(list(self.output)[self.read_upto:])
            self.read_upto = len(self.output)
            missed, self.missed = self.missed, 0
        return f"... {missed} earlier lines dropped ...\n{fresh}" if missed else fresh

    def state(self) -> str:
        code = self.process.poll()
        return "still running" if code is None else f"finished with exit code {code}"


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.todos = []
        self.jobs = {}

    def resolve(self, path: str) -> Path:
        if not path:
            raise ToolError("a path is needed")
        given = Path(os.path.expanduser(path))
        return (given if given.is_absolute() else self.root / given).resolve()

    def contains(self, path: str) -> bool:
        return self.resolve(path).is_relative_to(self.root)

    def shown(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)

    def stop_jobs(self) -> None:
        for job in self.jobs.values():
            if job.process.poll() is None:
                stop(job.process)
                job.process.wait()


def read_text(path: Path) -> str:
    if not path.is_file():
        raise ToolError(f"{path} is not a file")
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except UnicodeDecodeError:
        raise ToolError(f"{path} is not a text file") from None


def write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def uses_windows_line_ends(text: str) -> bool:
    windows_ends = text.count(WINDOWS_LINE_END)
    return windows_ends > 0 and windows_ends == text.count(UNIX_LINE_END)


def as_windows_lines(text: str) -> str:
    return text.replace(WINDOWS_LINE_END, UNIX_LINE_END).replace(UNIX_LINE_END, WINDOWS_LINE_END)


def in_line_style_of(text: str, existing: str) -> str:
    return as_windows_lines(text) if uses_windows_line_ends(existing) else text


def matching_old_text(before: str, old_text: str) -> str:
    if old_text in before and not uses_windows_line_ends(before):
        return old_text
    windows_form = as_windows_lines(old_text)
    return windows_form if WINDOWS_LINE_END in before and windows_form in before else old_text


def read_file(workspace, path, offset=1, limit=DEFAULT_READ_LINES):
    lines = read_text(workspace.resolve(path)).splitlines()
    start = max(int(offset), 1)
    chosen = lines[start - 1:start - 1 + max(int(limit), 1)]
    if not chosen and lines:
        raise ToolError(f"{path} has only {len(lines)} lines")
    return "\n".join(f"{number}\t{line[:MAX_LINE_CHARS]}" for number, line in enumerate(chosen, start=start))


def edited_text(before: str, old_text: str, new_text: str, replace_all: bool) -> str:
    if not old_text:
        raise ToolError("old_text is empty; use write_file to create a file")
    found = before.count(old_text)
    if found == 0:
        raise ToolError("old_text was not found; read the file again and copy the text exactly")
    if found > 1 and not replace_all:
        raise ToolError(f"old_text is in {found} places; add more surrounding text or set replace_all")
    return before.replace(old_text, new_text)


def planned_change(name: str, arguments: dict, workspace) -> tuple[Path, str, str]:
    target = workspace.resolve(arguments.get("path", ""))
    if name == "write_file":
        before = read_text(target) if target.exists() else ""
        return target, before, in_line_style_of(str(arguments.get("content", "")), before)
    if name == "edit_file":
        before = read_text(target)
        given_old = str(arguments.get("old_text", ""))
        old_text = matching_old_text(before, given_old)
        new_text = str(arguments.get("new_text", ""))
        if old_text != given_old:
            new_text = as_windows_lines(new_text)
        after = edited_text(before, old_text, new_text, bool(arguments.get("replace_all", False)))
        return target, before, after
    raise ToolError(f"{name} doesn't change files")


def write_file(workspace, path, content):
    target, before, after = planned_change("write_file", {"path": path, "content": content}, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, after)
    verb = "Updated" if before else "Created"
    return f"{verb} {workspace.shown(target)} ({len(after.splitlines())} lines)"


def edit_file(workspace, path, old_text, new_text, replace_all=False):
    arguments = {"path": path, "old_text": old_text, "new_text": new_text, "replace_all": replace_all}
    target, _before, after = planned_change("edit_file", arguments, workspace)
    write_text(target, after)
    return f"Edited {workspace.shown(target)}"


def split_pattern(pattern: str) -> tuple[str, list[str]]:
    parts = pattern.replace("\\", "/").split("/")
    fixed = []
    for index, part in enumerate(parts):
        if GLOB_CHARACTERS & set(part):
            return "/".join(fixed) or ("/" if fixed else ""), parts[index:]
        fixed.append(part)
    return "/".join(fixed[:-1]) or ("/" if len(fixed) > 1 else ""), fixed[-1:]


def listing_folder(workspace, pattern: str, path: str = ".") -> tuple[Path, list[str]]:
    prefix, rest = split_pattern(pattern or "**/*")
    if ".." in rest:
        raise ToolError("'..' can only come before the first wildcard in a pattern")
    folder = workspace.resolve(path or ".")
    return ((folder / os.path.expanduser(prefix)).resolve() if prefix else folder), rest


def matches(parts: tuple, pattern: list[str]) -> bool:
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == ANY_FOLDERS:
        return any(matches(parts[skipped:], rest) for skipped in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatch(parts[0], head) and matches(parts[1:], rest)


def is_linked_out(workspace, folder: Path, candidate: Path) -> bool:
    return folder.is_relative_to(workspace.root) and not candidate.resolve().is_relative_to(workspace.root)


def project_files(workspace, folder: Path, pattern: list[str]) -> list[Path]:
    deepest = None if ANY_FOLDERS in pattern else len(pattern) - 1
    found = []
    for current, folders, files in os.walk(folder):
        depth = len(Path(current).relative_to(folder).parts)
        folders[:] = [] if deepest is not None and depth >= deepest else [
            name for name in folders if name not in SKIPPED_FOLDERS]
        for name in files:
            candidate = Path(current) / name
            if matches(candidate.relative_to(folder).parts, pattern) and not is_linked_out(workspace, folder, candidate):
                found.append(candidate)
    return sorted(found)


def list_files(workspace, pattern="**/*", path="."):
    folder, rest = listing_folder(workspace, str(pattern), str(path))
    found = [workspace.shown(candidate) for candidate in project_files(workspace, folder, rest)]
    if not found:
        return f"No files match {pattern} in {workspace.shown(folder) or '.'}"
    extra = len(found) - MAX_LISTED_FILES
    listed = "\n".join(found[:MAX_LISTED_FILES])
    return listed + (f"\n... and {extra} more" if extra > 0 else "")


def search(workspace, pattern, path=".", glob="*"):
    try:
        expression = re.compile(pattern)
    except re.error as error:
        raise ToolError(f"bad pattern: {error}") from None
    matches = []
    for candidate in project_files(workspace, workspace.resolve(path), [ANY_FOLDERS, "*"]):
        if not fnmatch.fnmatch(candidate.name, glob) or candidate.stat().st_size > MAX_SEARCHED_FILE_BYTES:
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, start=1):
            if expression.search(line):
                matches.append(f"{workspace.shown(candidate)}:{number}:{line[:MAX_LINE_CHARS]}")
                if len(matches) >= MAX_SEARCH_MATCHES:
                    return "\n".join(matches) + f"\n... stopped at {MAX_SEARCH_MATCHES} matches"
    return "\n".join(matches) if matches else f"No matches for {pattern}"


def clipped(output: str) -> str:
    if len(output) <= MAX_OUTPUT_CHARS:
        return output
    tail = MAX_OUTPUT_CHARS - OUTPUT_HEAD_CHARS
    return f"{output[:OUTPUT_HEAD_CHARS]}\n... {len(output) - MAX_OUTPUT_CHARS} characters cut ...\n{output[-tail:]}"


def windows_powershell() -> str:
    root = os.environ.get("SystemRoot", "C:\\Windows")
    return "\\".join([root.rstrip("\\"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"])


def shell_command(command: str) -> tuple[list[str] | str, dict]:
    if sys.platform == "win32":
        return [windows_powershell(), "-NoProfile", "-NonInteractive", "-Command", command], {}
    bash = "/bin/bash" if Path("/bin/bash").exists() else None
    return command, {"shell": True, "executable": bash, "start_new_session": True}


def stop(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def start_process(workspace, command: str) -> subprocess.Popen:
    arguments, options = shell_command(command)
    return subprocess.Popen(arguments, cwd=workspace.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", **options)


def start_job(workspace, command: str) -> str:
    job_id = str(len(workspace.jobs) + 1)
    workspace.jobs[job_id] = Job(command, start_process(workspace, command))
    return f"Job {job_id} started in the background. Read it with command_output, end it with stop_command."


def job_of(workspace, job) -> Job:
    found = workspace.jobs.get(str(job))
    if found is None:
        raise ToolError(f"there is no background job {job}")
    return found


def command_output(workspace, job):
    running = job_of(workspace, job)
    return clipped(running.unread()) + f"\n[job {job} {running.state()}]"


def stop_command(workspace, job):
    running = job_of(workspace, job)
    if running.process.poll() is None:
        stop(running.process)
        running.process.wait()
    return clipped(running.unread()) + f"\n[job {job} stopped]"


def todo_write(workspace, items):
    if not isinstance(items, list):
        raise ToolError("items should be a list")
    for item in items:
        if not isinstance(item, dict) or item.get("status") not in TODO_MARKS or not str(item.get("text", "")).strip():
            raise ToolError("each item needs text and a status of pending, in_progress or done")
    workspace.todos = items
    return "\n".join(f"{TODO_MARKS[item['status']]} {item['text']}" for item in items) or "The list is empty."


def web_fetch(workspace, url):
    try:
        return webfetch.fetch(str(url))
    except webfetch.FetchError as error:
        raise ToolError(str(error)) from None


def run_command(workspace, command, timeout_seconds=DEFAULT_COMMAND_SECONDS, background=False):
    if background:
        return start_job(workspace, command)
    seconds = min(max(int(timeout_seconds), 1), MAX_COMMAND_SECONDS)
    process = start_process(workspace, command)
    try:
        output, _ = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        stop(process)
        output, _ = process.communicate()
        return clipped(output) + f"\n[stopped after {seconds} s]"
    except KeyboardInterrupt:
        stop(process)
        process.wait()
        raise
    return clipped(output) + f"\n[exit code {process.returncode}]"


HANDLERS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "search": search,
    "run_command": run_command,
    "command_output": command_output,
    "stop_command": stop_command,
    "todo_write": todo_write,
    "web_fetch": web_fetch,
}


def function(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties,
                                                            "required": required}}}


TEXT = {"type": "string"}
NUMBER = {"type": "integer"}
DEFINITIONS = [
    function("read_file", "Read a text file in the project. Lines come back numbered.",
             {"path": TEXT, "offset": {**NUMBER, "description": "first line, from 1"},
              "limit": {**NUMBER, "description": "how many lines"}}, ["path"]),
    function("write_file", "Create a file or replace all of it. Prefer edit_file for changes to an existing file.",
             {"path": TEXT, "content": TEXT}, ["path", "content"]),
    function("edit_file", "Replace old_text with new_text in a file. old_text must match exactly once "
                          "(copy it from read_file without the line numbers) unless replace_all is true.",
             {"path": TEXT, "old_text": TEXT, "new_text": TEXT, "replace_all": {"type": "boolean"}},
             ["path", "old_text", "new_text"]),
    function("list_files", "List files in the project by glob pattern, e.g. **/*.py.",
             {"pattern": TEXT, "path": {**TEXT, "description": "folder to look in, default the project"}}, []),
    function("search", "Search file contents with a regular expression. Returns file:line:text.",
             {"pattern": TEXT, "path": TEXT, "glob": {**TEXT, "description": "file name filter, e.g. *.py"}},
             ["pattern"]),
    function("run_command", "Run a shell command in the project folder (bash on Linux, PowerShell on Windows), "
                            "e.g. tests, builds, git. Returns the output and exit code.",
             {"command": TEXT, "timeout_seconds": NUMBER,
              "background": {"type": "boolean", "description": "start it and return at once, e.g. a dev server"}},
             ["command"]),
    function("command_output", "Read new output from a background job started by run_command.",
             {"job": TEXT}, ["job"]),
    function("stop_command", "Stop a background job.", {"job": TEXT}, ["job"]),
    function("todo_write", "Keep a short checklist for a job with several steps; send the whole list each time "
                           "with each item's status (pending, in_progress, done). The user sees it.",
             {"items": {"type": "array", "items": {"type": "object", "properties": {
                 "text": TEXT, "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
                 "required": ["text", "status"]}}}, ["items"]),
    function("web_fetch", "Fetch a web page (documentation, an issue, an API reference) as text.",
             {"url": TEXT}, ["url"]),
]


def run(name: str, arguments: dict, workspace: Workspace) -> str:
    handler = HANDLERS.get(name)
    if handler is None:
        return f"Error: there is no tool called {name}"
    try:
        return handler(workspace, **arguments)
    except TypeError as error:
        return f"Error: wrong arguments for {name}: {error}"
    except (ToolError, OSError, ValueError, LookupError) as error:
        return f"Error: {error}"
