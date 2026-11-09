import base64
import os
import signal
import subprocess
import sys

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
UTF8_OUTPUT = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "


def is_windows():
    return sys.platform == "win32"


def _powershell():
    root = os.environ.get("SystemRoot", "C:\\Windows")
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def argv(command):
    if is_windows():
        return [_powershell(), "-NoProfile", "-NonInteractive", "-Command", UTF8_OUTPUT + command]
    return ["/bin/bash", "-lc", command]


def encoded_argv(script):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [_powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def process_options():
    if is_windows():
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}
    return {"start_new_session": True}


def kill_tree(process):
    if is_windows():
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
        return
    os.killpg(process.pid, signal.SIGKILL)
