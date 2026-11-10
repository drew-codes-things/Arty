import ctypes

from process.tools import shell

TICK_WRAP = 2 ** 32
MILLISECONDS = 1000
DBUS_TIMEOUT_SECONDS = 2


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
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import DBusErrorResponse, unwrap_msg

    address = DBusAddress("/ScreenSaver", bus_name="org.freedesktop.ScreenSaver", interface="org.freedesktop.ScreenSaver")
    try:
        with open_dbus_connection(bus="SESSION") as connection:
            reply = connection.send_and_get_reply(new_method_call(address, method), timeout=DBUS_TIMEOUT_SECONDS)
        return int(unwrap_msg(reply)[0])
    except (DBusErrorResponse, OSError, TimeoutError, KeyError, ValueError) as error:
        raise NotSupported(f"{method}: {error}") from None


def linux_idle_seconds(call=screensaver_call) -> float | None:
    for method in ("GetSessionIdleTime", "GetActiveTime"):
        try:
            return float(call(method))
        except NotSupported:
            continue
    return None


def seconds_idle() -> float | None:
    return windows_idle_seconds() if shell.is_windows() else linux_idle_seconds()
