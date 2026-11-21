import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from process import browsers_found
from process import native_host

HOST_NAME = "app.arty.desktop"
HOST_DESCRIPTION = "Arty Desktop: connects the Arty extension without a pairing code"
HOST_FOLDER_NAME = "native-host"
HOST_SCRIPT_NAME = "host.py"
LINUX_LAUNCHER_NAME = "arty-native-host"
WINDOWS_LAUNCHER_NAME = "arty-native-host.bat"
LAUNCHER_MODE = 0o700
MANIFEST_FILE_NAME = f"{HOST_NAME}.json"
APPIMAGE_MOUNT_MARK = "/.mount_"
WINDOWS_CONSOLE_PYTHON = "python.exe"
WINDOWS_WINDOWLESS_PYTHON = "pythonw.exe"
SAFE_WINDOWS_PATH_CHARACTERS = '"\r\n'
NO_PYTHON = "no Python 3 to run the connector was found on this computer"


class NativeHostProblem(RuntimeError):
    pass


@dataclass(frozen=True)
class HostFiles:
    folder: Path
    script: Path
    launcher: Path


def host_files(data_root, platform=None):
    platform = sys.platform if platform is None else platform
    folder = data_root / HOST_FOLDER_NAME
    launcher_name = WINDOWS_LAUNCHER_NAME if platform == "win32" else LINUX_LAUNCHER_NAME
    return HostFiles(folder, folder / HOST_SCRIPT_NAME, folder / launcher_name)


def is_inside_appimage(executable, environ):
    return APPIMAGE_MOUNT_MARK in str(executable) or bool(environ.get("APPIMAGE"))


def host_python(executable=None, environ=None, which=shutil.which, platform=None):
    executable = sys.executable if executable is None else executable
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        path = PureWindowsPath(executable)
        return str(path.with_name(WINDOWS_CONSOLE_PYTHON)) if path.name.lower() == WINDOWS_WINDOWLESS_PYTHON else str(path)
    if is_inside_appimage(executable, environ):
        return which("python3") or ""
    return str(executable)


def linux_launcher(python, script):
    return f"#!/bin/sh\nexec {shlex.quote(python)} -I {shlex.quote(str(script))} \"$@\"\n"


def windows_launcher(python, script):
    for value in (python, str(script)):
        if any(character in value for character in SAFE_WINDOWS_PATH_CHARACTERS):
            raise NativeHostProblem(f"the path {value!r} cannot be written safely into a batch file")
    return f'@echo off\r\n"{_batch_escaped(python)}" -I "{_batch_escaped(str(script))}" %*\r\n'


def _batch_escaped(value):
    return value.replace("%", "%%")


def manifest(found, launcher):
    entry = {"name": HOST_NAME, "description": HOST_DESCRIPTION, "path": str(launcher), "type": "stdio"}
    if found.family == browsers_found.FIREFOX_FAMILY:
        entry["allowed_extensions"] = [browsers_found.FIREFOX_ADDON_ID]
    else:
        entry["allowed_origins"] = [native_host.EXTENSION_ORIGIN]
    return json.dumps(entry, indent=2) + "\n"


def _write_if_changed(path, text, mode=None):
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    if mode is not None:
        os.chmod(temporary, mode)
    os.replace(temporary, path)
    return True


def install_host(data_root, python, platform=None):
    platform = sys.platform if platform is None else platform
    if not python:
        raise NativeHostProblem(NO_PYTHON)
    files = host_files(data_root, platform)
    source = Path(native_host.__file__).read_text(encoding="utf-8")
    _write_if_changed(files.script, source)
    launcher = windows_launcher(python, files.script) if platform == "win32" else linux_launcher(python, files.script)
    _write_if_changed(files.launcher, launcher, None if platform == "win32" else LAUNCHER_MODE)
    return files


def is_our_manifest(text, launcher):
    try:
        entry = json.loads(text)
    except ValueError:
        return False
    return isinstance(entry, dict) and entry.get("name") == HOST_NAME and entry.get("path") == str(launcher)


def windows_manifest_path(data_root, found):
    return data_root / HOST_FOLDER_NAME / f"{found.family}-{MANIFEST_FILE_NAME}"


def _register_file(found, files):
    target = found.host_folder / MANIFEST_FILE_NAME
    try:
        current = target.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current is not None and not is_our_manifest(current, files.launcher):
        raise NativeHostProblem(f"{target} belongs to another program, so it was left alone")
    _write_if_changed(target, manifest(found, files.launcher))
    return str(target)


def _register_key(found, files, data_root, registry):
    manifest_path = windows_manifest_path(data_root, found)
    _write_if_changed(manifest_path, manifest(found, files.launcher))
    key = f"{found.registry_key}\\{HOST_NAME}"
    if registry.read_default(key) != str(manifest_path):
        registry.write_default(key, str(manifest_path))
    return key


def register(found_list, data_root, python, registry=None, platform=None):
    platform = sys.platform if platform is None else platform
    connectable = [found for found in found_list if found.can_connect_natively]
    if not connectable:
        return {}
    files = install_host(data_root, python, platform)
    done = {}
    for found in connectable:
        try:
            if platform == "win32":
                done[found.name] = _register_key(found, files, data_root, registry or windows_registry())
            else:
                done[found.name] = _register_file(found, files)
        except (OSError, NativeHostProblem) as problem:
            print(f"[browsers] couldn't connect {found.name} automatically: {problem}", flush=True)
    return done


def _remove_our_file(target, launcher):
    try:
        if not is_our_manifest(target.read_text(encoding="utf-8"), launcher):
            return False
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError as problem:
        print(f"[browsers] couldn't remove {target}: {problem}", flush=True)
        return False
    return True


def unregister(places, data_root, registry=None, platform=None):
    platform = sys.platform if platform is None else platform
    files = host_files(data_root, platform)
    removed = []
    for place in places:
        if platform == "win32" and place.registry_key:
            key = f"{place.registry_key}\\{HOST_NAME}"
            registry = registry or windows_registry()
            value = registry.read_default(key)
            if value and Path(value).parent == files.folder:
                registry.delete(key)
                removed.append(key)
        elif place.host_folder is not None and _remove_our_file(place.host_folder / MANIFEST_FILE_NAME, files.launcher):
            removed.append(str(place.host_folder / MANIFEST_FILE_NAME))
    return removed


def is_registered(found, data_root, registry=None, platform=None):
    platform = sys.platform if platform is None else platform
    files = host_files(data_root, platform)
    if not found.can_connect_natively:
        return False
    if platform == "win32":
        value = (registry or windows_registry()).read_default(f"{found.registry_key}\\{HOST_NAME}")
        return bool(value) and Path(value).parent == files.folder
    try:
        return is_our_manifest((found.host_folder / MANIFEST_FILE_NAME).read_text(encoding="utf-8"), files.launcher)
    except OSError:
        return False


class WindowsRegistry:
    def __init__(self):
        import winreg
        self._winreg = winreg

    def read_default(self, key):
        try:
            with self._winreg.OpenKey(self._winreg.HKEY_CURRENT_USER, key) as handle:
                return str(self._winreg.QueryValue(handle, None))
        except OSError:
            return ""

    def write_default(self, key, value):
        with self._winreg.CreateKey(self._winreg.HKEY_CURRENT_USER, key) as handle:
            self._winreg.SetValue(handle, "", self._winreg.REG_SZ, value)

    def delete(self, key):
        try:
            self._winreg.DeleteKey(self._winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            return


def windows_registry():
    return WindowsRegistry()
