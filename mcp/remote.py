"""SSH access to the assistant's desktop VM, shared by the MCP tool servers.

Everything runs as the `ai` account over a dedicated key, so the tools can do
exactly what that account can do. Revoking access is removing the key from its
authorized_keys.
"""
import fnmatch
import os
import posixpath
import re
import shlex
import subprocess

SETTINGS_FILE = os.environ.get("WORKSPACE_ENV", "/srv/ai/secrets/workspace.env")
USER = os.environ.get("WORKSPACE_USER", "ai")
KEY = os.environ.get("WORKSPACE_KEY", "/srv/ai/mcp/workspace_key")
KNOWN_HOSTS = os.environ.get("WORKSPACE_KNOWN_HOSTS", "/srv/ai/mcp/workspace_known_hosts")
CONTROL_DIR = os.environ.get("WORKSPACE_CONTROL_DIR", "/srv/ai/mcp/ssh-control")
HOST_ALIAS = os.environ.get("WORKSPACE_HOST_ALIAS", "arty-desktop")
HOST_KEY_REFUSED = b"Host key verification failed"
UNPINNED_MESSAGE = (b"the desktop machine's host key doesn't match the pinned one (or none is pinned yet), so nothing "
                    b"was sent. If the VM was rebuilt on purpose, run bin/pin-desktop-host-key on the laptop and deploy again.")
PROJECTS = "~/projects"
OUTPUT_LIMIT = int(os.environ.get("WORKSPACE_OUTPUT_CHARS", "6000"))
DEFAULT_TIMEOUT = int(os.environ.get("WORKSPACE_TIMEOUT_SECONDS", "120"))
GUI_ENV = "export DISPLAY=:0 XAUTHORITY=$HOME/.Xauthority;"
SSH_GRACE_SECONDS = 15
PRIVATE_FOLDERS = ((".gmail-chrome",), (".browser-profile",), (".local", "browser-agent"), (".ssh",))
PRIVATE_REFUSAL = ("Refused: Arty's signed-in browser profiles (~/.browser-profile, ~/.gmail-chrome), the browser "
                   "agent's folder and ~/.ssh are off limits to tools.")
PRIVATE_EXIT = 86
PATH_SEPARATORS = re.compile(r"[/=:\s()`;|&<>]+")
GLOB_CHARACTERS = re.compile(r"[*?\[{]")
BRACE_GROUP = re.compile(r"\{[^{}]*\}")
QUOTING = re.compile(r"[\\'\"]")


def clip(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters cut] ...\n{text[-half:]}"


def desktop_host() -> str:
    load_env_file(SETTINGS_FILE)
    return os.environ.get("WORKSPACE_HOST", "")


def control_folder() -> str | None:
    try:
        os.makedirs(CONTROL_DIR, mode=0o700, exist_ok=True)
        os.chmod(CONTROL_DIR, 0o700)
    except OSError:
        return None
    return CONTROL_DIR


def connection_sharing() -> list[str]:
    folder = control_folder()
    if folder is None:
        return []
    return ["-o", "ControlMaster=auto", "-o", f"ControlPath={folder}/%C", "-o", "ControlPersist=120"]


def ssh(command: str, stdin: bytes | None = None, timeout: int = DEFAULT_TIMEOUT, merge_errors: bool = True) -> tuple[int, bytes]:
    host = desktop_host()
    if not host:
        return 1, b"The desktop VM isn't set up (WORKSPACE_HOST missing)."
    args = [
        "ssh", "-T", "-i", KEY,
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=6", "-o", "LogLevel=ERROR",
        "-o", "StrictHostKeyChecking=yes", "-o", f"HostKeyAlias={HOST_ALIAS}", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
        *connection_sharing(),
        f"{USER}@{host}", command,
    ]
    errors = subprocess.STDOUT if merge_errors else subprocess.PIPE
    try:
        done = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=errors, timeout=timeout + SSH_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return 124, b"timed out"
    output = done.stdout
    if not merge_errors and done.returncode != 0:
        output = done.stderr or done.stdout
    if done.returncode == 255 and HOST_KEY_REFUSED in output:
        return 255, UNPINNED_MESSAGE
    if done.returncode == 255 and not output.strip():
        return 255, b"cannot reach the desktop machine (is the ai-desktop VM running?)"
    return done.returncode, output


def text(command: str, stdin: bytes | None = None, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str]:
    code, out = ssh(command, stdin, timeout)
    return code, out.decode("utf-8", errors="replace")


def expand(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return PROJECTS
    if path.startswith(("/", "~")):
        return path
    return f"{PROJECTS}/{path}"


def quoted(path: str) -> str:
    expanded = expand(path)
    if expanded.startswith("~"):
        return '"$HOME"' + shlex.quote(expanded[1:]) if len(expanded) > 1 else '"$HOME"'
    return shlex.quote(expanded)


def segment_names(segment: str, name: str) -> bool:
    if segment == name:
        return True
    if not GLOB_CHARACTERS.search(segment) or (name.startswith(".") and not segment.startswith(".")):
        return False
    return fnmatch.fnmatchcase(name, BRACE_GROUP.sub("*", segment))


def names_private_folder(segments: list[str]) -> bool:
    for start in range(len(segments)):
        for folder in PRIVATE_FOLDERS:
            window = segments[start:start + len(folder)]
            if len(window) == len(folder) and all(map(segment_names, window, folder)):
                return True
    return any(segment == folder[-1] for segment in segments for folder in PRIVATE_FOLDERS if len(folder) > 1)


def unquoted_words(text: str) -> list[str]:
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return QUOTING.sub("", text).split()


def touches_private_folders(text: str) -> bool:
    return any(names_private_folder([part for part in PATH_SEPARATORS.split(word) if part])
               for word in unquoted_words(text))


def private_path(path: str) -> bool:
    return touches_private_folders(shlex.quote(posixpath.normpath(expand(path))))


def private_case_patterns() -> str:
    return "|".join(f"*/{'/'.join(folder)}/*" for folder in PRIVATE_FOLDERS)


def refuse_private_target() -> str:
    return f"case \"/$target/\" in {private_case_patterns()}) echo {shlex.quote(PRIVATE_REFUSAL)} >&2; exit {PRIVATE_EXIT};; esac;"


def resolve_outside_private_folders(path_expr: str, must_exist: bool = True) -> str:
    existence = "-e" if must_exist else "-m"
    return f"target=$(realpath {existence} -- {path_expr}) || exit 1; {refuse_private_target()}"


def load_env_file(path: str):
    """Load KEY=value lines into os.environ (without overriding). mcpo starts tool servers with a
    minimal environment, so servers that need secrets read their own root-only file instead."""
    if not os.path.exists(path):
        return
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
