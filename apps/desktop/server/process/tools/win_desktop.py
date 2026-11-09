import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

WHEEL_DELTA = 120
VK_RETURN = 0x0D
VK_TAB = 0x09
TYPING_CHUNK = 40
TYPING_PAUSE_SECONDS = 0.004
DOUBLE_CLICK_GAP_SECONDS = 0.05
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
DWMWA_CLOAKED = 14
GW_OWNER = 4
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
WM_CLOSE = 0x0010
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SPI_GETWORKAREA = 0x0030
VK_MENU = 0x12
VK_UNASSIGNED = 0xE8
ALT_TAP_KEYS = ((VK_MENU, 0), (VK_UNASSIGNED, 0), (VK_UNASSIGNED, 0x0002), (VK_MENU, 0x0002))
IGNORED_WINDOWS = {"Program Manager"}

MODIFIERS = {"shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "option": 0x12, "opt": 0x12,
             "super": 0x5B, "win": 0x5B, "meta": 0x5B}
_NAMED = {"return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20, "delete": 0x08, "backspace": 0x08,
          "forwarddelete": 0x2E, "escape": 0x1B, "esc": 0x1B, "home": 0x24, "end": 0x23, "pageup": 0x21,
          "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
_PUNCTUATION = {"=": 0xBB, "-": 0xBD, "]": 0xDD, "[": 0xDB, "'": 0xDE, ";": 0xBA, "\\": 0xDC, ",": 0xBC,
                "/": 0xBF, ".": 0xBE, "`": 0xC0}
_EXTENDED_KEYS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
_SCROLL = {"up": ("vertical", 1), "down": ("vertical", -1), "right": ("horizontal", 1), "left": ("horizontal", -1)}

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000


def virtual_key(key):
    if key in _NAMED:
        return _NAMED[key]
    if key in _PUNCTUATION:
        return _PUNCTUATION[key]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return ord(key.upper())
    if len(key) > 1 and key[0] == "f" and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return 0x70 + int(key[1:]) - 1
    return None


def text_events(text):
    events = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\r":
            events.append(("vk", VK_RETURN))
            if text[index + 1:index + 2] == "\n":
                index += 1
        elif char == "\n":
            events.append(("vk", VK_RETURN))
        elif char == "\t":
            events.append(("vk", VK_TAB))
        else:
            units = char.encode("utf-16-le")
            for offset in range(0, len(units), 2):
                events.append(("unicode", int.from_bytes(units[offset:offset + 2], "little")))
        index += 1
    return events


def wheel(direction, amount):
    axis, sign = _SCROLL[direction]
    return axis, sign * WHEEL_DELTA * amount


def group_by_app(windows):
    grouped = {}
    for app, title in windows:
        grouped.setdefault(app, []).append(title)
    return grouped


ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


_libraries = {}


def _require_windows():
    if sys.platform != "win32":
        raise RuntimeError("this only works on Windows")


def _user():
    _require_windows()
    if "user32" not in _libraries:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        user.SendInput.restype = wintypes.UINT
        user.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        user.SetCursorPos.restype = wintypes.BOOL
        user.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
        user.GetCursorPos.restype = wintypes.BOOL
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
        user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        user.IsWindowVisible.argtypes = (wintypes.HWND,)
        user.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
        user.GetWindow.restype = wintypes.HWND
        user.GetSystemMetrics.argtypes = (ctypes.c_int,)
        user.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user.IsIconic.argtypes = (wintypes.HWND,)
        user.IsZoomed.argtypes = (wintypes.HWND,)
        user.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, wintypes.UINT)
        user.SystemParametersInfoW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
        user.LockWorkStation.restype = wintypes.BOOL
        user.OpenClipboard.argtypes = (wintypes.HWND,)
        user.GetClipboardData.argtypes = (wintypes.UINT,)
        user.GetClipboardData.restype = wintypes.HANDLE
        try:
            user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            user.SetProcessDPIAware()
        _libraries["user32"] = user
    return _libraries["user32"]


def _kernel():
    _require_windows()
    if "kernel32" not in _libraries:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                      ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.GlobalLock.argtypes = (wintypes.HANDLE,)
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = (wintypes.HANDLE,)
        _libraries["kernel32"] = kernel
    return _libraries["kernel32"]


def screen_size():
    user = _user()
    return user.GetSystemMetrics(0), user.GetSystemMetrics(1)


def capture(path):
    _user()
    from PIL import ImageGrab

    ImageGrab.grab().save(path)


def cursor_position():
    point = wintypes.POINT()
    _user().GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def _send(inputs):
    array = (INPUT * len(inputs))(*inputs)
    sent = _user().SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise RuntimeError("Windows refused the input (a window running as administrator may be in front)")


def _mouse(flags, data=0):
    event = INPUT()
    event.type = INPUT_MOUSE
    event.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
    return event


def _key(vk=0, scan=0, flags=0):
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return event


def _virtual_key_press(code):
    extended = KEYEVENTF_EXTENDEDKEY if code in _EXTENDED_KEYS else 0
    return [_key(vk=code, flags=extended), _key(vk=code, flags=extended | KEYEVENTF_KEYUP)]


def click(x, y, right=False, double=False):
    if not _user().SetCursorPos(int(x), int(y)):
        raise RuntimeError("couldn't move the mouse there")
    down, up = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if right else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
    for index in range(2 if double else 1):
        if index:
            time.sleep(DOUBLE_CLICK_GAP_SECONDS)
        _send([_mouse(down), _mouse(up)])


def type_text(text):
    inputs = []
    for kind, code in text_events(text):
        if kind == "vk":
            inputs += _virtual_key_press(code)
        else:
            inputs += [_key(scan=code, flags=KEYEVENTF_UNICODE), _key(scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    for start in range(0, len(inputs), TYPING_CHUNK):
        _send(inputs[start:start + TYPING_CHUNK])
        time.sleep(TYPING_PAUSE_SECONDS)
    return []


def press_keys(modifiers, key):
    code = virtual_key(key)
    held = [MODIFIERS[name] for name in modifiers]
    inputs = [_key(vk=vk) for vk in held] + _virtual_key_press(code) + [_key(vk=vk, flags=KEYEVENTF_KEYUP) for vk in reversed(held)]
    _send(inputs)


def scroll(direction, amount):
    axis, delta = wheel(direction, amount)
    _send([_mouse(MOUSEEVENTF_WHEEL if axis == "vertical" else MOUSEEVENTF_HWHEEL, delta)])


def _title(hwnd):
    user = _user()
    length = user.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _process_name(hwnd):
    pid = wintypes.DWORD()
    _user().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    kernel = _kernel()
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).stem.lower()
        return ""
    finally:
        kernel.CloseHandle(handle)


def front_window():
    hwnd = _user().GetForegroundWindow()
    if not hwnd:
        return None
    return _process_name(hwnd), _title(hwnd)


def _is_cloaked(hwnd):
    cloaked = wintypes.DWORD()
    dwm = ctypes.WinDLL("dwmapi")
    result = dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return result == 0 and cloaked.value != 0


def _enumerate():
    user = _user()
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd, _lparam):
        if user.IsWindowVisible(hwnd) and not user.GetWindow(hwnd, GW_OWNER) and not _is_cloaked(hwnd):
            title = _title(hwnd)
            if title and title not in IGNORED_WINDOWS:
                found.append((hwnd, _process_name(hwnd), title))
        return True

    user.EnumWindows(callback_type(collect), 0)
    return found


def open_windows():
    return [(app, title) for _hwnd, app, title in _enumerate()]


def pick_window(windows, name):
    wanted = (name or "").strip().lower()
    for hwnd, app, title in windows:
        if app.lower() == wanted:
            return hwnd, app, title
    for hwnd, app, title in windows:
        if wanted in title.lower() or wanted in app.lower():
            return hwnd, app, title
    return None


def find_window(name=""):
    if not (name or "").strip():
        hwnd = _user().GetForegroundWindow()
        return (hwnd, _process_name(hwnd), _title(hwnd)) if hwnd else None
    return pick_window(_enumerate(), name)


def window_titles():
    return group_by_app(open_windows())


def get_clipboard_text():
    user = _user()
    kernel = _kernel()
    if not user.OpenClipboard(None):
        raise RuntimeError("the clipboard is busy")
    try:
        handle = user.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()


def focus_window(hwnd):
    user = _user()
    if user.IsIconic(hwnd):
        user.ShowWindow(hwnd, SW_RESTORE)
    _send([_key(vk=vk, flags=flags) for vk, flags in ALT_TAP_KEYS])
    if not user.SetForegroundWindow(hwnd):
        raise RuntimeError("Windows wouldn't bring that window to the front")


def show_window(hwnd, state):
    _user().ShowWindow(hwnd, state)


def close_window(hwnd):
    _user().PostMessageW(hwnd, WM_CLOSE, 0, 0)


def window_rect(hwnd):
    rect = wintypes.RECT()
    _user().GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def work_area():
    rect = wintypes.RECT()
    _user().SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def place_window(hwnd, x, y, width, height):
    user = _user()
    if user.IsZoomed(hwnd) or user.IsIconic(hwnd):
        user.ShowWindow(hwnd, SW_RESTORE)
    if not user.SetWindowPos(hwnd, None, int(x), int(y), int(width), int(height), SWP_NOZORDER | SWP_NOACTIVATE):
        raise RuntimeError("Windows wouldn't move that window")


def press_media_key(code):
    _send(_virtual_key_press(code))


def lock_screen():
    if not _user().LockWorkStation():
        raise RuntimeError("Windows refused to lock the screen")
