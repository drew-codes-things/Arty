import threading

from process import browser
from process import browsers_found
from process import config as cfg
from process import native_hosts

SETTINGS_SECTION = "browser"
AUTOMATIC_KEY = "connect_automatically"
NATIVE_ON = "on"
NATIVE_OFF = "off"
NATIVE_UNAVAILABLE = "unavailable"

_refresh_lock = threading.Lock()


def connects_automatically():
    return bool(cfg.setting(SETTINGS_SECTION, AUTOMATIC_KEY, True))


def _native_state(found, registered):
    if not found.can_connect_natively:
        return NATIVE_UNAVAILABLE
    return NATIVE_ON if found.name in registered else NATIVE_OFF


def _is_connected(name, connected_names):
    return any(shown == name or shown.startswith(f"{name} ") for shown in connected_names)


def report(found_list, registered, connected_names):
    return [{"name": found.name, "packaging": found.packaging, "profiles": len(found.profiles),
             "native": _native_state(found, registered), "connected": _is_connected(found.name, connected_names)}
            for found in found_list]


def refresh(data_root=None, find=browsers_found.browsers_with_extension, places=browsers_found.places_here,
            python=native_hosts.host_python, registry=None):
    data_root = cfg.DATA_ROOT if data_root is None else data_root
    with _refresh_lock:
        found_list = find()
        if not connects_automatically():
            native_hosts.unregister(places(), data_root, registry=registry)
            return found_list, set()
        try:
            registered = native_hosts.register(found_list, data_root, python(), registry=registry)
        except native_hosts.NativeHostProblem as problem:
            print(f"[browsers] browsers can't connect automatically: {problem}", flush=True)
            registered = {}
        return found_list, set(registered)


def found_report(data_root=None, **dependencies):
    found_list, registered = refresh(data_root, **dependencies)
    return {"automatic": connects_automatically(), "browsers": report(found_list, registered, browser.connected_names())}


def set_automatic(enabled, data_root=None, **dependencies):
    cfg.set_value(SETTINGS_SECTION, AUTOMATIC_KEY, bool(enabled))
    return found_report(data_root, **dependencies)


def refresh_in_background():
    def run():
        try:
            found_list, registered = refresh()
        except Exception as error:
            print(f"[browsers] looking for browsers failed: {type(error).__name__}: {error}", flush=True)
            return
        names = ", ".join(sorted({found.name for found in found_list})) or "none"
        print(f"[browsers] with the Arty extension: {names}; connecting automatically: {len(registered)}", flush=True)
    threading.Thread(target=run, name="browsers", daemon=True).start()
