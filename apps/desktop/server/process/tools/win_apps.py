import os
import re
import subprocess
from pathlib import Path

from process.tools import session, shell

NOT_AN_APP = re.compile(
    r"uninstall|\bhelp\b|readme|release notes|documentation|\bwebsite\b|\bmanual\b|licen[cs]e|^visit |\bsupport\b|what'?s new",
    re.I)
SHORTCUT_TARGET_TIMEOUT_SECONDS = 10


def start_menu_folders():
    folders = []
    for variable in ("ProgramData", "APPDATA"):
        base = os.environ.get(variable)
        if base:
            folders.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return folders


def installed_apps(folders=None):
    entries = {}
    for folder in start_menu_folders() if folders is None else folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.lnk")):
            if not NOT_AN_APP.search(path.stem):
                entries.setdefault(path.stem.casefold(), {"id": path.stem, "path": path, "name": path.stem, "exec": ""})
    return entries


def shortcut_target(path):
    quoted = str(path).replace("'", "''")
    script = f"(New-Object -ComObject WScript.Shell).CreateShortcut('{quoted}').TargetPath"
    try:
        done = subprocess.run(shell.argv(script), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=SHORTCUT_TARGET_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, **shell.process_options())
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def launch(entry, url=""):
    if not url:
        os.startfile(str(entry["path"]))
        return True
    target = shortcut_target(entry["path"])
    return bool(target) and session.spawn([target, url])
