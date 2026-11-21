import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import browsers_found

ARTY = browsers_found.EXTENSION_ID
OTHER_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def packed(profile, extension_id=ARTY):
    version = profile / "Extensions" / extension_id / "1.7_0"
    version.mkdir(parents=True)
    (version / "manifest.json").write_text("{}")


def listed(profile, file_name="Secure Preferences", extension_id=ARTY):
    profile.mkdir(parents=True, exist_ok=True)
    (profile / file_name).write_text(json.dumps({"extensions": {"settings": {extension_id: {"path": "/x"}}}}))


def firefox_listing(profile, addon_id=browsers_found.FIREFOX_ADDON_ID):
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "extensions.json").write_text(json.dumps({"addons": [{"id": "other@example"}, {"id": addon_id}]}))


def by_name(found):
    return {(item.name, item.packaging): item for item in found}


def test_finds_packed_unpacked_opera_and_firefox_on_linux(tmp_path):
    config = tmp_path / ".config"
    packed(config / "google-chrome" / "Default")
    listed(config / "BraveSoftware" / "Brave-Browser" / "Profile 2")
    listed(config / "opera", file_name="Preferences")
    firefox_listing(tmp_path / ".mozilla" / "firefox" / "abc.default-release")
    found = by_name(browsers_found.browsers_with_extension(home=tmp_path, platform="linux"))
    assert set(found) == {("Chrome", "native"), ("Brave", "native"), ("Opera", "native"), ("Firefox", "native")}
    assert found[("Chrome", "native")].profiles == ["Default"]
    assert found[("Brave", "native")].profiles == ["Profile 2"]
    assert found[("Opera", "native")].profiles == ["opera"]
    assert found[("Firefox", "native")].host_folder == tmp_path / ".mozilla" / "native-messaging-hosts"
    assert found[("Chrome", "native")].host_folder == config / "google-chrome" / "NativeMessagingHosts"
    assert all(item.can_connect_natively for item in found.values())


def test_wrong_extension_id_bad_json_and_oversized_files_are_ignored(tmp_path, monkeypatch):
    config = tmp_path / ".config"
    packed(config / "google-chrome" / "Default", extension_id=OTHER_ID)
    listed(config / "chromium" / "Default", extension_id=OTHER_ID)
    broken = config / "microsoft-edge" / "Default"
    broken.mkdir(parents=True)
    (broken / "Secure Preferences").write_text("{not json")
    big = config / "vivaldi" / "Default"
    listed(big)
    monkeypatch.setattr(browsers_found, "MAX_SETTINGS_FILE_BYTES", 10)
    firefox_listing(tmp_path / ".mozilla" / "firefox" / "p1", addon_id="arty-browser@evil.example")
    assert browsers_found.browsers_with_extension(home=tmp_path, platform="linux") == []


def test_only_real_profile_folders_are_read(tmp_path):
    config = tmp_path / ".config" / "google-chrome"
    packed(config / "System Profile")
    packed(config / "Guest Profile")
    assert browsers_found.browsers_with_extension(home=tmp_path, platform="linux") == []
    packed(config / "Profile 12")
    assert browsers_found.browsers_with_extension(home=tmp_path, platform="linux")[0].profiles == ["Profile 12"]


def test_snap_and_flatpak_copies_are_found_but_cannot_connect_natively(tmp_path):
    packed(tmp_path / "snap" / "chromium" / "common" / "chromium" / "Default")
    firefox_listing(tmp_path / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox" / "x.default")
    found = by_name(browsers_found.browsers_with_extension(home=tmp_path, platform="linux"))
    assert set(found) == {("Chromium", "snap"), ("Firefox", "flatpak")}
    assert not any(item.can_connect_natively for item in found.values())


def test_firefox_xpi_counts(tmp_path):
    profile = tmp_path / ".config" / "mozilla" / "firefox" / "q.default"
    (profile / "extensions").mkdir(parents=True)
    (profile / "extensions" / f"{browsers_found.FIREFOX_ADDON_ID}.xpi").write_bytes(b"PK")
    found = browsers_found.browsers_with_extension(home=tmp_path, platform="linux")
    assert [(item.name, item.host_folder) for item in found] == [
        ("Firefox", tmp_path / ".config" / "mozilla" / "native-messaging-hosts")]


def test_windows_places_use_registry_keys(tmp_path):
    local, roaming = tmp_path / "Local", tmp_path / "Roaming"
    packed(local / "Google" / "Chrome" / "User Data" / "Default")
    listed(local / "Microsoft" / "Edge" / "User Data" / "Profile 1")
    listed(roaming / "Opera Software" / "Opera GX Stable", file_name="Preferences")
    packed(local / "Vivaldi" / "User Data" / "Default")
    firefox_listing(roaming / "Mozilla" / "Firefox" / "Profiles" / "z.default")
    environ = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
    found = by_name(browsers_found.browsers_with_extension(home=tmp_path, environ=environ, platform="win32"))
    keys = {name: item.registry_key for (name, _packaging), item in found.items()}
    assert keys == {
        "Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
        "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
        "Opera": r"Software\Google\Chrome\NativeMessagingHosts",
        "Vivaldi": r"Software\Google\Chrome\NativeMessagingHosts",
        "Firefox": r"Software\Mozilla\NativeMessagingHosts",
    }
    assert all(item.host_folder is None and item.can_connect_natively for item in found.values())


def test_nothing_installed_finds_nothing(tmp_path):
    assert browsers_found.browsers_with_extension(home=tmp_path, platform="linux") == []
    assert browsers_found.browsers_with_extension(home=tmp_path, environ={}, platform="win32") == []
