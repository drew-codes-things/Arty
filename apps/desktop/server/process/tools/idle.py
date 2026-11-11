import ctypes
import os

from process.tools import shell

TICK_WRAP = 2 ** 32
MILLISECONDS = 1000
DBUS_TIMEOUT_SECONDS = 2
DESKTOPS_REPORTING_LOCK_TIME_IN_MILLISECONDS = ("KDE",)


class NotSupported(Exception):
    pass


def idle_from_ticks(now_ticks: int, last_input_ticks: int) -> float:
    return ((now_ticks - last_input_ticks) % TICK_WRAP) / MILLISECONDS


class LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def windows_idle_seconds() -> float | None:
    info = LastInputInfo(ctypes.sizeof(LastInputInfo), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    tick_count = ctypes.windll.kernel32.GetTickCount
    tick_count.restype = ctypes.c_uint
    return idle_from_ticks(tick_count(), info.dwTime)


def screensaver_call(method: str) -> int:
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
        from jeepney.wrappers import DBusErrorResponse, unwrap_msg
    except ImportError as error:
        raise NotSupported(f"{method}: jeepney is not installed ({error})") from None

    address = DBusAddress("/ScreenSaver", bus_name="org.freedesktop.ScreenSaver", interface="org.freedesktop.ScreenSaver")
    try:
        with open_dbus_connection(bus="SESSION") as connection:
            reply = connection.send_and_get_reply(new_method_call(address, method), timeout=DBUS_TIMEOUT_SECONDS)
        return int(unwrap_msg(reply)[0])
    except DBusErrorResponse as error:
        raise NotSupported(f"{method}: {error}") from None


def current_desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "")


def lock_time_in_seconds(active_time: int, desktop: str) -> float:
    if any(name in desktop for name in DESKTOPS_REPORTING_LOCK_TIME_IN_MILLISECONDS):
        return active_time / MILLISECONDS
    return float(active_time)


def linux_idle_seconds(call=screensaver_call, desktop: str | None = None) -> float | None:
    try:
        return float(call("GetSessionIdleTime"))
    except (NotSupported, IndexError, ValueError, TypeError):
        pass
    try:
        active_time = call("GetActiveTime")
    except (NotSupported, IndexError, ValueError, TypeError):
        return None
    return lock_time_in_seconds(active_time, current_desktop() if desktop is None else desktop)


def seconds_idle() -> float | None:
    return windows_idle_seconds() if shell.is_windows() else linux_idle_seconds()
