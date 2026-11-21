import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

EXTENSION_ID = "lepggpdmafbgbgfgacpglpjelmiofkli"
FIREFOX_ADDON_ID = "arty-browser@arty.home"
MAX_SETTINGS_FILE_BYTES = 16_000_000
MAX_PROFILES_PER_BROWSER = 50
NUMBERED_PROFILE = re.compile(r"Profile \d{1,4}")
CHROMIUM_FAMILY = "chromium"
FIREFOX_FAMILY = "firefox"
NATIVE = "native"
SNAP = "snap"
FLATPAK = "flatpak"
CHROMIUM_SETTINGS_FILES = ("Secure Preferences", "Preferences")


@dataclass(frozen=True)
class BrowserPlace:
    name: str
    family: str
    root: Path
    packaging: str = NATIVE
    profile_at_root: bool = False
    host_folder: Path | None = None
    registry_key: str = ""


@dataclass
class FoundBrowser:
    name: str
    family: str
    packaging: str
    root: Path
    profiles: list = field(default_factory=list)
    host_folder: Path | None = None
    registry_key: str = ""

    @property
    def can_connect_natively(self):
        return self.packaging == NATIVE and (self.host_folder is not None or bool(self.registry_key))


def linux_places(home):
    config = home / ".config"
    chromium_roots = [
        ("Chrome", config / "google-chrome"), ("Chrome", config / "google-chrome-beta"),
        ("Chrome", config / "google-chrome-unstable"), ("Chromium", config / "chromium"),
        ("Edge", config / "microsoft-edge"), ("Edge", config / "microsoft-edge-beta"),
        ("Edge", config / "microsoft-edge-dev"), ("Brave", config / "BraveSoftware" / "Brave-Browser"),
        ("Vivaldi", config / "vivaldi"),
    ]
    places = [BrowserPlace(name, CHROMIUM_FAMILY, root, host_folder=root / "NativeMessagingHosts")
              for name, root in chromium_roots]
    places.append(BrowserPlace("Opera", CHROMIUM_FAMILY, config / "opera", profile_at_root=True,
                               host_folder=config / "opera" / "NativeMessagingHosts"))
    places += [
        BrowserPlace("Chromium", CHROMIUM_FAMILY, home / "snap" / "chromium" / "common" / "chromium", packaging=SNAP),
        BrowserPlace("Chromium", CHROMIUM_FAMILY, home / ".var" / "app" / "org.chromium.Chromium" / "config" / "chromium",
                     packaging=FLATPAK),
        BrowserPlace("Firefox", FIREFOX_FAMILY, home / ".mozilla" / "firefox",
                     host_folder=home / ".mozilla" / "native-messaging-hosts"),
        BrowserPlace("Firefox", FIREFOX_FAMILY, config / "mozilla" / "firefox",
                     host_folder=config / "mozilla" / "native-messaging-hosts"),
        BrowserPlace("Firefox", FIREFOX_FAMILY, home / "snap" / "firefox" / "common" / ".mozilla" / "firefox", packaging=SNAP),
        BrowserPlace("Firefox", FIREFOX_FAMILY, home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
                     packaging=FLATPAK),
    ]
    return places


def windows_places(local_app_data, roaming_app_data):
    chrome_key = r"Software\Google\Chrome\NativeMessagingHosts"
    chromium_roots = [
        ("Chrome", local_app_data / "Google" / "Chrome" / "User Data", chrome_key),
        ("Chromium", local_app_data / "Chromium" / "User Data", r"Software\Chromium\NativeMessagingHosts"),
        ("Edge", local_app_data / "Microsoft" / "Edge" / "User Data", r"Software\Microsoft\Edge\NativeMessagingHosts"),
        ("Brave", local_app_data / "BraveSoftware" / "Brave-Browser" / "User Data",
         r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"),
        ("Vivaldi", local_app_data / "Vivaldi" / "User Data", chrome_key),
    ]
    places = [BrowserPlace(name, CHROMIUM_FAMILY, root, registry_key=key) for name, root, key in chromium_roots]
    places += [BrowserPlace("Opera", CHROMIUM_FAMILY, roaming_app_data / "Opera Software" / folder, profile_at_root=True,
                            registry_key=chrome_key) for folder in ("Opera Stable", "Opera GX Stable")]
    places.append(BrowserPlace("Firefox", FIREFOX_FAMILY, roaming_app_data / "Mozilla" / "Firefox" / "Profiles",
                               registry_key=r"Software\Mozilla\NativeMessagingHosts"))
    return places


def places_here(home=None, environ=None, platform=None):
    home = Path.home() if home is None else home
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform != "win32":
        return linux_places(home)
    local = Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    roaming = Path(environ.get("APPDATA") or home / "AppData" / "Roaming")
    return windows_places(local, roaming)


def _read_json(path):
    try:
        if path.stat().st_size > MAX_SETTINGS_FILE_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _chromium_profiles(place):
    folders = [place.root] if place.profile_at_root else []
    try:
        children = sorted(place.root.iterdir())
    except OSError:
        return folders
    named = [child for child in children
             if child.is_dir() and (child.name == "Default" or NUMBERED_PROFILE.fullmatch(child.name))]
    return (folders + named)[:MAX_PROFILES_PER_BROWSER]


def _packed_in(profile):
    versions = profile / "Extensions" / EXTENSION_ID
    try:
        return any((version / "manifest.json").is_file() for version in versions.iterdir())
    except OSError:
        return False


def _listed_in_settings(profile):
    for file_name in CHROMIUM_SETTINGS_FILES:
        settings = _read_json(profile / file_name)
        extensions = settings.get("extensions") if isinstance(settings, dict) else None
        listed = extensions.get("settings") if isinstance(extensions, dict) else None
        if isinstance(listed, dict) and EXTENSION_ID in listed:
            return True
    return False


def chromium_has_extension(profile):
    return _packed_in(profile) or _listed_in_settings(profile)


def _firefox_profiles(place):
    try:
        children = sorted(place.root.iterdir())
    except OSError:
        return []
    return [child for child in children if child.is_dir()][:MAX_PROFILES_PER_BROWSER]


def firefox_has_addon(profile):
    if (profile / "extensions" / f"{FIREFOX_ADDON_ID}.xpi").is_file():
        return True
    listing = _read_json(profile / "extensions.json")
    addons = listing.get("addons") if isinstance(listing, dict) else None
    return isinstance(addons, list) and any(isinstance(addon, dict) and addon.get("id") == FIREFOX_ADDON_ID
                                            for addon in addons)


def profiles_with_extension(place):
    if place.family == FIREFOX_FAMILY:
        return [profile.name for profile in _firefox_profiles(place) if firefox_has_addon(profile)]
    return [profile.name for profile in _chromium_profiles(place) if chromium_has_extension(profile)]


def found_browsers(places):
    found = []
    for place in places:
        if not place.root.is_dir():
            continue
        profiles = profiles_with_extension(place)
        if profiles:
            found.append(FoundBrowser(place.name, place.family, place.packaging, place.root, profiles,
                                      place.host_folder, place.registry_key))
    return found


def browsers_with_extension(home=None, environ=None, platform=None):
    return found_browsers(places_here(home, environ, platform))
