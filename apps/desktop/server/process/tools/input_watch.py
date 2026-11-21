import ctypes
import ctypes.util
import os
import socket
import struct
import threading
import time

from process.tools import idle
from process.tools import session
from process.tools import shell

INJECTION_GRACE_SECONDS = 0.25
WAYLAND_IDLE_MS = 150
WAYLAND_SETTLE_SECONDS = WAYLAND_IDLE_MS / 1000 + 0.25
WAYLAND_CONNECT_SECONDS = 2.0
DISPLAY_ID, REGISTRY_ID, SYNC_ID = 1, 2, 3
SEAT_ID, NOTIFIER_ID, NOTIFICATION_ID = 4, 5, 6
DISPLAY_SYNC, DISPLAY_GET_REGISTRY = 0, 1
DISPLAY_ERROR = 0
REGISTRY_GLOBAL, REGISTRY_BIND = 0, 0
CALLBACK_DONE = 0
NOTIFIER_GET_IDLE_NOTIFICATION = 1
NOTIFICATION_IDLED, NOTIFICATION_RESUMED = 0, 1
HEADER = struct.Struct("=II")
MAX_WAYLAND_MESSAGE = 4096
IDLE_NOTIFIER = "ext_idle_notifier_v1"
SEAT = "wl_seat"
MUTTER_BUS = "org.gnome.Mutter.IdleMonitor"
MUTTER_PATH = "/org/gnome/Mutter/IdleMonitor/Core"
DBUS_TIMEOUT_SECONDS = 2
MILLISECONDS = 1000


class NoInputWatch(RuntimeError):
    pass


class TickWatch:
    def __init__(self, read_idle_seconds, clock=time.monotonic, sleep=time.sleep, on_close=None):
        self._read_idle_seconds = read_idle_seconds
        self._clock = clock
        self._sleep = sleep
        self._on_close = on_close

    def quiet_since(self, moment):
        try:
            seconds = self._read_idle_seconds()
        except Exception as error:
            print(f"[screen control] reading input activity failed ({type(error).__name__}); counting it as input",
                  flush=True)
            return False
        if seconds is None:
            return False
        return self._clock() - float(seconds) <= moment + INJECTION_GRACE_SECONDS

    def wait(self, seconds):
        self._sleep(seconds)

    def close(self):
        if self._on_close is not None:
            self._on_close()


def wayland_string(text):
    data = text.encode("utf-8") + b"\0"
    return struct.pack("=I", len(data)) + data + b"\0" * (-len(data) % 4)


def wayland_message(object_id, opcode, payload=b""):
    return HEADER.pack(object_id, (HEADER.size + len(payload)) << 16 | opcode) + payload


def read_wayland_string(payload, offset):
    (length,) = struct.unpack_from("=I", payload, offset)
    start = offset + 4
    text = payload[start:start + max(0, length - 1)].decode("utf-8", errors="replace")
    return text, start + length + (-length % 4)


class WaylandIdleWatch:
    def __init__(self, sock, clock=time.monotonic, sleep=time.sleep):
        self._sock = sock
        self._clock = clock
        self._sleep = sleep
        self._buffer = b""
        self._state = threading.Condition()
        self._idle = False
        self._resumed_at = None
        self._broken = False
        self._bind_notification()
        threading.Thread(target=self._follow, name="idle-notify", daemon=True).start()

    def _send(self, message):
        self._sock.sendall(message)

    def _event(self):
        while len(self._buffer) < HEADER.size:
            self._receive()
        object_id, size_and_opcode = HEADER.unpack_from(self._buffer)
        size, opcode = size_and_opcode >> 16, size_and_opcode & 0xFFFF
        if size < HEADER.size or size > MAX_WAYLAND_MESSAGE:
            raise NoInputWatch("the desktop sent a message Arty can't read")
        while len(self._buffer) < size:
            self._receive()
        payload, self._buffer = self._buffer[HEADER.size:size], self._buffer[size:]
        return object_id, opcode, payload

    def _receive(self):
        chunk = self._sock.recv(MAX_WAYLAND_MESSAGE)
        if not chunk:
            raise NoInputWatch("the desktop closed the connection")
        self._buffer += chunk

    def _globals(self):
        self._send(wayland_message(DISPLAY_ID, DISPLAY_GET_REGISTRY, struct.pack("=I", REGISTRY_ID)))
        self._send(wayland_message(DISPLAY_ID, DISPLAY_SYNC, struct.pack("=I", SYNC_ID)))
        found = {}
        while True:
            object_id, opcode, payload = self._event()
            if object_id == SYNC_ID and opcode == CALLBACK_DONE:
                return found
            if object_id == DISPLAY_ID and opcode == DISPLAY_ERROR:
                raise NoInputWatch("the desktop refused Arty's request")
            if object_id == REGISTRY_ID and opcode == REGISTRY_GLOBAL:
                (name,) = struct.unpack_from("=I", payload)
                interface, _end = read_wayland_string(payload, 4)
                found.setdefault(interface, name)

    def _bind(self, name, interface, new_id):
        payload = struct.pack("=I", name) + wayland_string(interface) + struct.pack("=II", 1, new_id)
        self._send(wayland_message(REGISTRY_ID, REGISTRY_BIND, payload))

    def _bind_notification(self):
        found = self._globals()
        if IDLE_NOTIFIER not in found or SEAT not in found:
            raise NoInputWatch("this desktop doesn't offer idle notifications")
        self._bind(found[SEAT], SEAT, SEAT_ID)
        self._bind(found[IDLE_NOTIFIER], IDLE_NOTIFIER, NOTIFIER_ID)
        request = struct.pack("=III", NOTIFICATION_ID, WAYLAND_IDLE_MS, SEAT_ID)
        self._send(wayland_message(NOTIFIER_ID, NOTIFIER_GET_IDLE_NOTIFICATION, request))

    def _follow(self):
        try:
            while True:
                self._take(*self._event())
        except (OSError, NoInputWatch, struct.error) as error:
            print(f"[screen control] the idle watch ended ({error}); counting everything as input", flush=True)
        with self._state:
            self._broken = True
            self._state.notify_all()

    def _take(self, object_id, opcode, _payload):
        if object_id == DISPLAY_ID and opcode == DISPLAY_ERROR:
            raise NoInputWatch("the desktop reported an error")
        if object_id != NOTIFICATION_ID:
            return
        with self._state:
            if opcode == NOTIFICATION_IDLED:
                self._idle = True
            elif opcode == NOTIFICATION_RESUMED:
                self._idle = False
                self._resumed_at = self._clock()
            self._state.notify_all()

    def quiet_since(self, moment):
        settled_at = moment + WAYLAND_SETTLE_SECONDS
        if self._clock() < settled_at:
            self._sleep(settled_at - self._clock())
        with self._state:
            if self._broken or not self._idle:
                return False
            return self._resumed_at is None or self._resumed_at <= moment + INJECTION_GRACE_SECONDS

    def wait(self, seconds):
        self._sleep(seconds)

    def close(self):
        try:
            self._sock.close()
        except OSError:
            return


def wayland_socket_path(environ=None):
    environ = os.environ if environ is None else environ
    display = environ.get("WAYLAND_DISPLAY", "")
    if not display:
        return ""
    if os.path.isabs(display):
        return display
    runtime = environ.get("XDG_RUNTIME_DIR", "")
    return os.path.join(runtime, display) if runtime else ""


def wayland_watch():
    path = wayland_socket_path()
    if not path:
        raise NoInputWatch("there is no Wayland display")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(WAYLAND_CONNECT_SECONDS)
    try:
        sock.connect(path)
        watch = WaylandIdleWatch(sock)
    except (OSError, NoInputWatch, struct.error):
        sock.close()
        raise
    sock.settimeout(None)
    return watch


class XScreenSaverInfo(ctypes.Structure):
    _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                ("til_or_since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("eventMask", ctypes.c_ulong)]


def x11_idle_reader():
    x11_name, xss_name = ctypes.util.find_library("X11"), ctypes.util.find_library("Xss")
    if not x11_name or not xss_name:
        raise NoInputWatch("libX11 or libXss is missing")
    x11, xss = ctypes.CDLL(x11_name), ctypes.CDLL(xss_name)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(XScreenSaverInfo)
    xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XScreenSaverInfo)]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XFree.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    if not display:
        raise NoInputWatch("the X display can't be opened")
    return X11IdleReader(x11, xss, display)


class X11IdleReader:
    def __init__(self, x11, xss, display):
        self._x11, self._xss = x11, xss
        self._lock = threading.Lock()
        self._display = display
        self._info = xss.XScreenSaverAllocInfo()
        self._root = x11.XDefaultRootWindow(display)

    def __call__(self):
        with self._lock:
            if self._display is None or not self._xss.XScreenSaverQueryInfo(self._display, self._root, self._info):
                return None
            return self._info.contents.idle / MILLISECONDS

    def close(self):
        with self._lock:
            display, self._display = self._display, None
            if display is None:
                return
            self._x11.XFree(self._info)
            self._x11.XCloseDisplay(display)


def mutter_idle_seconds():
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import unwrap_msg

    address = DBusAddress(MUTTER_PATH, bus_name=MUTTER_BUS, interface=MUTTER_BUS)
    with open_dbus_connection(bus="SESSION") as connection:
        reply = connection.send_and_get_reply(new_method_call(address, "GetIdletime"), timeout=DBUS_TIMEOUT_SECONDS)
    return int(unwrap_msg(reply)[0]) / MILLISECONDS


def _working_reader(read):
    close = getattr(read, "close", None)
    try:
        if read() is None:
            raise NoInputWatch("the desktop didn't say")
    except Exception as error:
        if close is not None:
            close()
        if isinstance(error, NoInputWatch):
            raise
        raise NoInputWatch(f"{type(error).__name__}: {error}") from None
    return TickWatch(read, on_close=close)


def _session_idle_seconds():
    try:
        return float(idle.screensaver_call("GetSessionIdleTime"))
    except idle.NotSupported:
        return None


def watch_here(platform_is_windows=None, desktop_kind=None, desktop_name=None):
    if (shell.is_windows() if platform_is_windows is None else platform_is_windows):
        return _working_reader(idle.windows_idle_seconds)
    kind = session.kind() if desktop_kind is None else desktop_kind
    name = idle.current_desktop() if desktop_name is None else desktop_name
    attempts = []
    if kind == "x11":
        attempts.append(lambda: _working_reader(x11_idle_reader()))
    if "GNOME" in name.upper():
        attempts.append(lambda: _working_reader(mutter_idle_seconds))
    if kind == "wayland":
        attempts.append(wayland_watch)
    attempts.append(lambda: _working_reader(_session_idle_seconds))
    reasons = []
    for attempt in attempts:
        try:
            return attempt()
        except (NoInputWatch, OSError, ImportError) as error:
            reasons.append(str(error))
    raise NoInputWatch("Arty can't tell when you use the mouse or keyboard here (" + "; ".join(reasons) + ")")
