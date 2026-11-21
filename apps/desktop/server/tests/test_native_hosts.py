import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import HAS_POSIX_MODES
from process import browsers_found, native_host, native_hosts

PYTHON = "/usr/bin/python3"


class FakeRegistry:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = []

    def read_default(self, key):
        return self.values.get(key, "")

    def write_default(self, key, value):
        self.writes.append(key)
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def chromium(name, folder, packaging="native", key=""):
    return browsers_found.FoundBrowser(name, browsers_found.CHROMIUM_FAMILY, packaging, folder.parent, ["Default"],
                                       None if key else folder, key)


def firefox(folder, key=""):
    return browsers_found.FoundBrowser("Firefox", browsers_found.FIREFOX_FAMILY, "native", folder.parent, ["p"],
                                       None if key else folder, key)


@pytest.fixture
def linux_found(tmp_path):
    return [chromium("Chrome", tmp_path / "chrome" / "NativeMessagingHosts"),
            firefox(tmp_path / "mozilla" / "native-messaging-hosts")]


def test_register_installs_the_host_launcher_and_one_manifest_per_browser(tmp_path, linux_found):
    data = tmp_path / "data"
    done = native_hosts.register(linux_found, data, PYTHON, platform="linux")
    files = native_hosts.host_files(data, "linux")
    assert set(done) == {"Chrome", "Firefox"}
    assert files.script.read_text() == Path(native_host.__file__).read_text()
    assert files.launcher.read_text() == f"#!/bin/sh\nexec {PYTHON} -I {files.script} \"$@\"\n"
    assert not HAS_POSIX_MODES or stat.S_IMODE(files.launcher.stat().st_mode) == native_hosts.LAUNCHER_MODE
    chrome = json.loads((tmp_path / "chrome" / "NativeMessagingHosts" / "app.arty.desktop.json").read_text())
    assert chrome == {"name": "app.arty.desktop", "description": native_hosts.HOST_DESCRIPTION, "path": str(files.launcher),
                      "type": "stdio", "allowed_origins": ["chrome-extension://lepggpdmafbgbgfgacpglpjelmiofkli/"]}
    fox = json.loads((tmp_path / "mozilla" / "native-messaging-hosts" / "app.arty.desktop.json").read_text())
    assert fox["allowed_extensions"] == ["arty-browser@arty.home"] and "allowed_origins" not in fox


def test_register_twice_changes_nothing(tmp_path, linux_found):
    data = tmp_path / "data"
    native_hosts.register(linux_found, data, PYTHON, platform="linux")
    written = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    stamps = {path: path.stat().st_mtime_ns for path in written}
    for path in written:
        os.utime(path, ns=(1, 1))
    native_hosts.register(linux_found, data, PYTHON, platform="linux")
    assert all(path.stat().st_mtime_ns == 1 for path in written), stamps


def test_paths_with_spaces_and_quotes_are_quoted_for_the_shell(tmp_path):
    files = native_hosts.install_host(tmp_path / "it's data", "/opt/my python/bin/python3", platform="linux")
    assert files.launcher.read_text().splitlines()[1] == (
        "exec '/opt/my python/bin/python3' -I '" + str(files.script).replace("'", "'\"'\"'") + "' \"$@\"")


def test_a_manifest_that_is_not_ours_is_left_alone(tmp_path, linux_found):
    foreign = tmp_path / "chrome" / "NativeMessagingHosts" / "app.arty.desktop.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text(json.dumps({"name": "app.arty.desktop", "path": "/tmp/evil"}))
    done = native_hosts.register(linux_found, tmp_path / "data", PYTHON, platform="linux")
    assert "Chrome" not in done and "Firefox" in done
    assert json.loads(foreign.read_text())["path"] == "/tmp/evil"


def test_snap_copies_are_not_registered(tmp_path):
    snap = chromium("Chromium", tmp_path / "snap" / "NativeMessagingHosts", packaging="snap")
    assert native_hosts.register([snap], tmp_path / "data", PYTHON, platform="linux") == {}
    assert not (tmp_path / "data").exists()


def test_no_python_means_no_registration(tmp_path, linux_found):
    with pytest.raises(native_hosts.NativeHostProblem):
        native_hosts.register(linux_found, tmp_path / "data", "", platform="linux")


def test_unregister_removes_only_our_files(tmp_path, linux_found):
    data = tmp_path / "data"
    native_hosts.register(linux_found, data, PYTHON, platform="linux")
    other_folder = tmp_path / "edge" / "NativeMessagingHosts"
    other_folder.mkdir(parents=True)
    (other_folder / "app.arty.desktop.json").write_text(json.dumps({"name": "app.arty.desktop", "path": "/elsewhere"}))
    places = [browsers_found.BrowserPlace("Chrome", "chromium", tmp_path / "chrome", host_folder=tmp_path / "chrome" / "NativeMessagingHosts"),
              browsers_found.BrowserPlace("Edge", "chromium", tmp_path / "edge", host_folder=other_folder),
              browsers_found.BrowserPlace("Firefox", "firefox", tmp_path / "mozilla", host_folder=tmp_path / "mozilla" / "native-messaging-hosts")]
    removed = native_hosts.unregister(places, data, platform="linux")
    assert len(removed) == 2
    assert (other_folder / "app.arty.desktop.json").exists()
    assert not any(native_hosts.is_registered(found, data, platform="linux") for found in linux_found)


def test_windows_registers_keys_pointing_at_one_manifest_per_family(tmp_path):
    chrome_key = r"Software\Google\Chrome\NativeMessagingHosts"
    found = [chromium("Chrome", tmp_path, key=chrome_key), chromium("Opera", tmp_path, key=chrome_key),
             chromium("Edge", tmp_path, key=r"Software\Microsoft\Edge\NativeMessagingHosts"),
             firefox(tmp_path, key=r"Software\Mozilla\NativeMessagingHosts")]
    registry = FakeRegistry()
    data = tmp_path / "data"
    done = native_hosts.register(found, data, r"C:\Arty\python\python.exe", registry=registry, platform="win32")
    folder = data / "native-host"
    assert registry.values == {
        chrome_key + r"\app.arty.desktop": str(folder / "chromium-app.arty.desktop.json"),
        r"Software\Microsoft\Edge\NativeMessagingHosts\app.arty.desktop": str(folder / "chromium-app.arty.desktop.json"),
        r"Software\Mozilla\NativeMessagingHosts\app.arty.desktop": str(folder / "firefox-app.arty.desktop.json"),
    }
    assert set(done) == {"Chrome", "Opera", "Edge", "Firefox"}
    assert len(registry.writes) == 3
    native_hosts.register(found, data, r"C:\Arty\python\python.exe", registry=registry, platform="win32")
    assert len(registry.writes) == 3
    launcher = (folder / "arty-native-host.bat").read_bytes().decode()
    assert launcher == f'@echo off\r\n"C:\\Arty\\python\\python.exe" -I "{folder / "host.py"}" %*\r\n'


def test_windows_unregister_deletes_only_keys_pointing_into_our_folder(tmp_path):
    data = tmp_path / "data"
    ours = r"Software\Google\Chrome\NativeMessagingHosts\app.arty.desktop"
    theirs = r"Software\Mozilla\NativeMessagingHosts\app.arty.desktop"
    registry = FakeRegistry({ours: str(data / "native-host" / "chromium-app.arty.desktop.json"), theirs: r"C:\other\x.json"})
    places = browsers_found.windows_places(tmp_path / "L", tmp_path / "R")
    assert native_hosts.unregister(places, data, registry=registry, platform="win32") == [ours]
    assert registry.values == {theirs: r"C:\other\x.json"}


def test_batch_launcher_escapes_percent_and_refuses_quotes():
    assert native_hosts.windows_launcher(r"C:\100%\python.exe", r"C:\d\host.py").startswith('@echo off\r\n"C:\\100%%\\python.exe"')
    with pytest.raises(native_hosts.NativeHostProblem):
        native_hosts.windows_launcher('C:\\a"b\\python.exe', r"C:\d\host.py")


def test_host_python_choice():
    assert native_hosts.host_python("/usr/bin/python3.13", {}, platform="linux") == "/usr/bin/python3.13"
    assert native_hosts.host_python("/tmp/.mount_ArtyXY/resources/python3.12", {}, which=lambda _name: "/usr/bin/python3",
                                    platform="linux") == "/usr/bin/python3"
    assert native_hosts.host_python("/opt/x/python3", {"APPIMAGE": "/home/u/Arty.AppImage"}, which=lambda _name: None,
                                    platform="linux") == ""
    assert native_hosts.host_python(r"C:\Arty\python\pythonw.exe", {}, platform="win32") == r"C:\Arty\python\python.exe"


def test_the_windows_uninstaller_removes_every_key_arty_writes():
    installer = (Path(__file__).resolve().parents[2] / "app" / "build" / "installer.nsh").read_text()
    package = json.loads((Path(__file__).resolve().parents[2] / "app" / "package.json").read_text())
    assert package["build"]["nsis"]["include"] == "build/installer.nsh"
    keys = {place.registry_key for place in browsers_found.windows_places(Path("L"), Path("R"))}
    for key in keys:
        assert f'"{key}\\{native_hosts.HOST_NAME}"' in installer
    assert f'$APPDATA\\Arty\\{native_hosts.HOST_FOLDER_NAME}' in installer
