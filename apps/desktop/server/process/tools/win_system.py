import ctypes
import json
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from process.tools import shell, win_desktop

COMMAND_SECONDS = 20
CPU_SAMPLE_SECONDS = 0.25
MAX_VOLUME = 1.0
AC_PLUGGED_IN = 1
BATTERY_LOW = 0x02
BATTERY_CRITICAL = 0x04
BATTERY_CHARGING = 0x08
BATTERY_ABSENT = 0x80
BATTERY_UNKNOWN = 0xFF
PERCENT_UNKNOWN = 255
BYTES_PER_GB = 1024 ** 3
TOAST_POLICY_KEY = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"
TOAST_POLICY_VALUE = "ToastEnabled"
TOAST_SENDER_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
VOLUME_MUTE_KEY = 0xAD
VOLUME_DOWN_KEY = 0xAE
VOLUME_UP_KEY = 0xAF
VOLUME_KEY_STEP = 2
VOLUME_KEY_PRESSES_FROM_SILENT_TO_FULL = 100 // VOLUME_KEY_STEP
MEDIA_KEYS = {"next": 0xB0, "previous": 0xB1, "stop": 0xB2, "play_pause": 0xB3, "play": 0xB3, "pause": 0xB3}
UNAVAILABLE = "isn't available on Windows"
BRIGHTNESS_HINT = "Laptop screens can be changed this way; desktop monitors usually can't."
SCRIPT_PREFIX = (
    "$ProgressPreference = 'SilentlyContinue'\n"
    "$ErrorActionPreference = 'Stop'\n"
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
    "try {\n")
SCRIPT_SUFFIX = "\n} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }\n"


class WindowsToolError(RuntimeError):
    pass


def snap_rect(work_area, side):
    left, top, width, height = work_area
    half = width // 2
    if side == "left":
        return left, top, half, height
    return left + half, top, width - half, height


def parse_wlan(text):
    state = re.search(r"^\s*State\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    if not state:
        return None, ""
    ssid = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", text, re.MULTILINE)
    return state.group(1).lower(), ssid.group(1) if ssid else ""


def battery_text(ac_line, flag, percent):
    if percent == PERCENT_UNKNOWN or flag in (BATTERY_ABSENT, BATTERY_UNKNOWN):
        return None
    if flag & BATTERY_CHARGING:
        state = "charging"
    elif ac_line == AC_PLUGGED_IN:
        state = "plugged in"
    elif flag & (BATTERY_LOW | BATTERY_CRITICAL):
        state = "on battery, low"
    else:
        state = "on battery"
    return f"battery {percent}% ({state})"


def cpu_percent(before, after):
    idle = after[0] - before[0]
    total = (after[1] - before[1]) + (after[2] - before[2])
    if total <= 0:
        return 0
    return round((total - idle) / total * 100)


AUDIO_TYPES = r"""
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f(); int g(); int h(); int i();
  int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);
  int j();
  int GetMasterVolumeLevelScalar(out float pfLevel);
  int k(); int l(); int m(); int n();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, System.Guid pguidEventContext);
  int GetMute(out bool pbMute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
  int Activate(ref System.Guid id, int clsCtx, int activationParams, out IAudioEndpointVolume aev);
}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
  int f();
  int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
}
[ComImport, Guid("BCDE0395-E52F-44F2-A5C8-BFFFD2C3F3A6")] class MMDeviceEnumeratorComObject { }
public class Audio {
  static IAudioEndpointVolume Endpoint() {
    var enumerator = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;
    IMMDevice device = null;
    Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0, 1, out device));
    IAudioEndpointVolume endpoint = null;
    var endpointId = typeof(IAudioEndpointVolume).GUID;
    Marshal.ThrowExceptionForHR(device.Activate(ref endpointId, 23, 0, out endpoint));
    return endpoint;
  }
  public static float Volume {
    get { float level = -1; Marshal.ThrowExceptionForHR(Endpoint().GetMasterVolumeLevelScalar(out level)); return level; }
    set { Marshal.ThrowExceptionForHR(Endpoint().SetMasterVolumeLevelScalar(value, System.Guid.Empty)); }
  }
  public static bool Mute {
    get { bool muted; Marshal.ThrowExceptionForHR(Endpoint().GetMute(out muted)); return muted; }
    set { Marshal.ThrowExceptionForHR(Endpoint().SetMute(value, System.Guid.Empty)); }
  }
  public static float Level() { return Volume; }
  public static bool Muted() { return Mute; }
}
"""


def volume_script(level, change, mute):
    lines = [f"Add-Type -TypeDefinition @'{AUDIO_TYPES}'@"]
    if level is not None:
        lines.append(f"[Audio]::Volume = {_fraction(max(0, min(int(level), 100)) / 100)}")
    if change:
        step = _fraction(int(change) / 100)
        lines.append(f"[Audio]::Volume = [Math]::Max(0, [Math]::Min({MAX_VOLUME}, [Audio]::Level() + ({step})))")
    if mute is not None:
        lines.append(f"[Audio]::Mute = {'$true' if mute else '$false'}")
    lines.append("@{level = [int][Math]::Round([Audio]::Level() * 100); muted = [Audio]::Muted()} | ConvertTo-Json -Compress")
    return "\n".join(lines)


def _fraction(value):
    return repr(round(value, 2))


RADIO_HELPERS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($operation, $resultType) {
  $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
  $task.Wait(-1) | Out-Null
  $task.Result
}
[Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus, Windows.System.Devices, ContentType = WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState, Windows.System.Devices, ContentType = WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
"""


def radio_script(kind, state):
    lines = [RADIO_HELPERS, f"$radio = $radios | Where-Object {{ $_.Kind -eq '{kind}' }} | Select-Object -First 1"]
    if state in ("on", "off"):
        wanted = state.capitalize()
        lines.append(f"if ($radio) {{ Await ($radio.SetStateAsync([Windows.Devices.Radios.RadioState]::{wanted})) "
                     "([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null }")
    lines.append("@{present = [bool]$radio; state = if ($radio) { [string]$radio.State } else { '' }} | ConvertTo-Json -Compress")
    return "\n".join(lines)


BLUETOOTH_DEVICES_SCRIPT = r"""
$connectedKey = '{83DA6326-97A6-4088-9453-A1923F573B29} 15'
Get-PnpDevice -Class Bluetooth | Where-Object { $_.InstanceId -like 'BTHENUM\DEV_*' -or $_.InstanceId -like 'BTHLE\DEV_*' } | ForEach-Object {
  $connected = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName $connectedKey -ErrorAction SilentlyContinue).Data
  if ($connected) { $_.FriendlyName }
}
"""

BRIGHTNESS_READ = "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object -First 1).CurrentBrightness"

NOTIFY_SCRIPT = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastText02"><text id="1"></text><text id="2"></text></binding></visual></toast>')
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode($env:ARTY_TITLE)) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:ARTY_MESSAGE)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_SENDER_ID}').Show($toast)
"""


def run_command(argv, timeout=COMMAND_SECONDS, env=None):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                stdin=subprocess.DEVNULL, env=env, **shell.process_options())
    except FileNotFoundError:
        raise WindowsToolError(f"{Path(argv[0]).name} isn't installed") from None
    except subprocess.TimeoutExpired:
        raise WindowsToolError(f"{Path(argv[0]).name} didn't answer") from None
    if result.returncode != 0:
        raise WindowsToolError((result.stderr or result.stdout).strip()[:200] or f"{Path(argv[0]).name} failed")
    return result.stdout.strip()


def powershell(script, env=None, timeout=COMMAND_SECONDS):
    full_env = {**os.environ, **env} if env else None
    return run_command(shell.encoded_argv(SCRIPT_PREFIX + script + SCRIPT_SUFFIX), timeout=timeout, env=full_env)


def _json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise WindowsToolError(f"unexpected answer from Windows: {text[:80]}") from None


def desktop_windows():
    front = win_desktop.front_window()
    lines = [f"{'* ' if (app, title) == front else '- '}{app}: {title}" for app, title in win_desktop.open_windows()]
    return ("Windows (* is in front):\n" + "\n".join(lines)) if lines else "No windows are open."


def _window_action(action, hwnd, geometry):
    if action == "focus":
        win_desktop.focus_window(hwnd)
    elif action == "minimise":
        win_desktop.show_window(hwnd, win_desktop.SW_MINIMIZE)
    elif action == "maximise":
        win_desktop.show_window(hwnd, win_desktop.SW_MAXIMIZE)
    elif action == "restore":
        win_desktop.show_window(hwnd, win_desktop.SW_RESTORE)
    elif action == "close":
        win_desktop.close_window(hwnd)
    elif action in ("snap_left", "snap_right"):
        win_desktop.place_window(hwnd, *snap_rect(win_desktop.work_area(), action.removeprefix("snap_")))
    else:
        x, y, width, height = win_desktop.window_rect(hwnd)
        new_x, new_y, new_width, new_height = geometry
        win_desktop.place_window(hwnd, x if new_x is None else new_x, y if new_y is None else new_y,
                                 width if new_width is None else new_width, height if new_height is None else new_height)


def desktop_window(action, window="", x=None, y=None, width=None, height=None, desktop=None):
    if action in ("fullscreen", "to_desktop"):
        return f"Couldn't: {action.replace('_', ' ')} {UNAVAILABLE}. Maximise or snap the window instead."
    found = win_desktop.find_window(window)
    if not found:
        return f"Couldn't: no window matches {window or 'the front window'!r}. desktop_windows lists what's open."
    hwnd, app, title = found
    try:
        _window_action(action, hwnd, (x, y, width, height))
    except RuntimeError as error:
        return f"Couldn't do that: {error}."
    print(f"[desktop] {action} {app}", flush=True)
    return f"Done: {action.replace('_', ' ')} on {app} ({title[:60]})."


def desktop_switch(desktop):
    return f"Switching virtual desktops {UNAVAILABLE}."


def volume_state():
    answer = _json(powershell(volume_script(None, None, None)))
    return answer["level"], answer["muted"]


def system_volume(level=None, change=None, mute=None):
    try:
        answer = _json(powershell(volume_script(level, change, mute)))
    except (WindowsToolError, ValueError) as error:
        return change_volume_with_keys(level, change, mute, error)
    return f"Volume is {answer['level']}%{' (muted)' if answer['muted'] else ''}."


def volume_key_presses(level, change, mute):
    presses = []
    if level is not None:
        wanted = max(0, min(int(level), 100))
        presses += [VOLUME_DOWN_KEY] * VOLUME_KEY_PRESSES_FROM_SILENT_TO_FULL
        presses += [VOLUME_UP_KEY] * round(wanted / VOLUME_KEY_STEP)
    elif change:
        steps = min(VOLUME_KEY_PRESSES_FROM_SILENT_TO_FULL, max(1, round(abs(int(change)) / VOLUME_KEY_STEP)))
        presses += [VOLUME_UP_KEY if int(change) > 0 else VOLUME_DOWN_KEY] * steps
    if mute is not None:
        presses.append(VOLUME_MUTE_KEY)
    return presses


def change_volume_with_keys(level, change, mute, error):
    presses = volume_key_presses(level, change, mute)
    if not presses:
        return f"Couldn't read the volume: {error}."
    try:
        for code in presses:
            win_desktop.press_media_key(code)
    except RuntimeError as key_error:
        return f"Couldn't change the volume: {error}, and the volume keys failed too: {key_error}."
    return volume_keys_summary(level, change, mute)


def volume_keys_summary(level, change, mute):
    parts = []
    if level is not None:
        parts.append(f"Set the volume to about {max(0, min(int(level), 100))}% with the volume keys")
    elif change:
        direction = "up" if int(change) > 0 else "down"
        parts.append(f"Turned the volume {direction} by about {min(100, abs(int(change)))}% with the volume keys")
    if mute is not None:
        parts.append("pressed the mute key, which switches mute on or off")
    summary = ", and ".join(parts)
    return f"{summary[0].upper()}{summary[1:]}. Windows' audio control isn't available here, so I can't read the exact level."


def system_media(action="status"):
    if action == "status":
        return "Windows can't read what is playing; try play_pause, next, previous or stop."
    code = MEDIA_KEYS.get(action)
    if code is None:
        return f"action must be status or one of: {', '.join(MEDIA_KEYS)}."
    try:
        win_desktop.press_media_key(code)
    except RuntimeError as error:
        return f"Couldn't press the media key: {error}."
    return f"Pressed the {action.replace('_', '/')} media key."


def system_brightness(percent=None):
    try:
        if percent is not None:
            wanted = max(1, min(int(percent), 100))
            powershell("Invoke-CimMethod -InputObject (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
                       f"| Select-Object -First 1) -MethodName WmiSetBrightness -Arguments @{{Timeout = 0; Brightness = {wanted}}} | Out-Null")
        current = int(powershell(BRIGHTNESS_READ))
    except (WindowsToolError, ValueError) as error:
        return f"Couldn't change the brightness: {error}. {BRIGHTNESS_HINT}"
    return f"Screen brightness is {current}%."


def _radio(kind, state):
    answer = _json(powershell(radio_script(kind, state)))
    return answer["present"], answer["state"].lower() == "on"


def system_wifi(state="status"):
    try:
        present, powered = _radio("WiFi", state)
        if not present:
            return "This computer has no Wi-Fi."
        network = parse_wlan(run_command(["netsh", "wlan", "show", "interfaces"]))[1] if powered else ""
    except WindowsToolError as error:
        return f"Couldn't change Wi-Fi: {error}."
    return f"Wi-Fi is {'on' if powered else 'off'}" + (f", connected to {network}." if network else ".")


def system_bluetooth(state="status"):
    try:
        present, powered = _radio("Bluetooth", state)
        if not present:
            return "This computer has no Bluetooth."
        names = powershell(BLUETOOTH_DEVICES_SCRIPT).splitlines() if powered else []
    except WindowsToolError as error:
        return f"Couldn't change Bluetooth: {error}."
    return f"Bluetooth is {'on' if powered else 'off'}" + (f", connected to {', '.join(names)}." if names else ".")


def system_lock():
    try:
        win_desktop.lock_screen()
    except RuntimeError as error:
        return f"Couldn't lock the screen: {error}."
    return "Locked the screen."


def set_toasts(enabled):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TOAST_POLICY_KEY) as key:
        winreg.SetValueEx(key, TOAST_POLICY_VALUE, 0, winreg.REG_DWORD, 1 if enabled else 0)


def system_do_not_disturb(on=True):
    try:
        set_toasts(not on)
    except OSError as error:
        return f"Couldn't change do-not-disturb: {error}."
    return "Do not disturb is on: notifications are held back." if on else "Do not disturb is off."


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _kernel32():
    if not shell.is_windows():
        raise WindowsToolError("this only works on Windows")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _power_status():
    status = _PowerStatus()
    if not _kernel32().GetSystemPowerStatus(ctypes.byref(status)):
        return None
    return status


def battery_status():
    status = _power_status()
    return status and battery_text(status.ACLineStatus, status.BatteryFlag, status.BatteryLifePercent)


def battery_levels():
    status = _power_status()
    if not status or status.BatteryLifePercent == PERCENT_UNKNOWN or status.BatteryFlag in (BATTERY_ABSENT, BATTERY_UNKNOWN):
        return None
    if status.BatteryFlag & BATTERY_CHARGING:
        return status.BatteryLifePercent, "Charging"
    return status.BatteryLifePercent, "Not charging" if status.ACLineStatus == AC_PLUGGED_IN else "Discharging"


def _system_times():
    kernel = _kernel32()
    idle, kernel_time, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel_time), ctypes.byref(user)):
        raise WindowsToolError("Windows wouldn't say how busy the CPU is")
    return tuple((part.dwHighDateTime << 32) | part.dwLowDateTime for part in (idle, kernel_time, user))


def cpu_load():
    before = _system_times()
    time.sleep(CPU_SAMPLE_SECONDS)
    return cpu_percent(before, _system_times())


def memory_status():
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not _kernel32().GlobalMemoryStatusEx(ctypes.byref(status)):
        raise WindowsToolError("Windows wouldn't say how much memory is used")
    return status.dwMemoryLoad, round(status.ullTotalPhys / BYTES_PER_GB)


def free_disk_gb():
    return shutil.disk_usage(Path.home()).free // BYTES_PER_GB


def system_status():
    parts = []
    try:
        level, muted = volume_state()
        parts.append(f"volume {level}%{' muted' if muted else ''}")
    except WindowsToolError:
        pass
    battery = battery_status()
    if battery:
        parts.append(battery)
    parts.append(f"CPU load {cpu_load()}%")
    percent, total_gb = memory_status()
    parts.append(f"memory {percent}% used of {total_gb} GB")
    parts.append(f"{free_disk_gb()} GB free disk")
    return "This computer: " + ", ".join(parts) + "."


def desktop_notify(title, message=""):
    try:
        powershell(NOTIFY_SCRIPT, env={"ARTY_TITLE": str(title)[:120], "ARTY_MESSAGE": str(message)[:500]})
    except WindowsToolError as error:
        return f"Couldn't show a notification: {error}."
    return "Notification shown."
