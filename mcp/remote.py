"""SSH access to the assistant's desktop VM, shared by the MCP tool servers.

Everything runs as the `ai` account over a dedicated key, so the tools can do
exactly what that account can do. Revoking access is removing the key from its
authorized_keys.
"""
import os
import shlex
import subprocess

SETTINGS_FILE = os.environ.get("WORKSPACE_ENV", "/srv/ai/secrets/workspace.env")
USER = os.environ.get("WORKSPACE_USER", "ai")
KEY = os.environ.get("WORKSPACE_KEY", "/srv/ai/mcp/workspace_key")
KNOWN_HOSTS = os.environ.get("WORKSPACE_KNOWN_HOSTS", "/srv/ai/mcp/workspace_known_hosts")
PROJECTS = "~/projects"
OUTPUT_LIMIT = int(os.environ.get("WORKSPACE_OUTPUT_CHARS", "6000"))
DEFAULT_TIMEOUT = int(os.environ.get("WORKSPACE_TIMEOUT_SECONDS", "120"))
GUI_ENV = "export DISPLAY=:0 XAUTHORITY=$HOME/.Xauthority;"
SSH_GRACE_SECONDS = 15


def clip(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters cut] ...\n{text[-half:]}"


def desktop_host() -> str:
    load_env_file(SETTINGS_FILE)
    return os.environ.get("WORKSPACE_HOST", "")


def ssh(command: str, stdin: bytes | None = None, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, bytes]:
    host = desktop_host()
    if not host:
        return 1, b"The desktop VM isn't set up (WORKSPACE_HOST missing)."
    args = [
        "ssh", "-T", "-i", KEY,
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=6", "-o", "LogLevel=ERROR",
        "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
        "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/workspace-ssh-%C", "-o", "ControlPersist=120",
        f"{USER}@{host}", command,
    ]
    try:
        done = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout + SSH_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return 124, b"timed out"
    if done.returncode == 255 and not done.stdout.strip():
        return 255, b"cannot reach the desktop machine (is the ai-desktop VM running?)"
    return done.returncode, done.stdout


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
