"""Window and system control on a KDE Plasma desktop, for Arty's brain.

Windows go through KWin scripts (loaded over D-Bus; a script reports back by print(), which
lands in the user journal), so they work on Wayland where X11 tools can't see windows.
System controls use what Plasma and Debian already have: wpctl (volume), MPRIS over D-Bus
(media), org.kde.ScreenBrightness, nmcli, bluetoothctl, loginctl, notify-send, and
org.freedesktop.Notifications for do-not-disturb.
"""
import functools
import json
import os
import re
import subprocess
import tempfile
import time
import uuid

from process.tools import session, shell, win_system

KWIN_REPLY_SECONDS = 3.0
COMMAND_SECONDS = 10
BRIGHTNESS_PATH = "/org/kde/ScreenBrightness/display0"
WINDOW_ACTIONS = ("focus", "minimise", "maximise", "restore", "close", "fullscreen", "move", "resize",
                  "snap_left", "snap_right", "to_desktop")


class DesktopError(RuntimeError):
    """Something on the desktop said no; the message goes back to Arty."""


def _on_windows(function):
    @functools.wraps(function)
    def choose(*args, **kwargs):
        if shell.is_windows():
            return getattr(win_system, function.__name__)(*args, **kwargs)
        return function(*args, **kwargs)
    return choose


def available():
    return shell.is_windows() or (session.kind() == "wayland" and session.have("qdbus6")
                                  and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""))


def _run(args, timeout=COMMAND_SECONDS):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise DesktopError(f"{args[0]} isn't installed") from None
    except subprocess.TimeoutExpired:
        raise DesktopError(f"{args[0]} didn't answer") from None
    if result.returncode != 0:
        raise DesktopError((result.stderr or result.stdout).strip()[:200] or f"{args[0]} failed")
    return result.stdout.strip()


# ---------- KWin scripts ----------

WINDOW_HELPERS = """
function describe(w) {
  return {id: w.internalId.toString(), title: w.caption, app: w.resourceClass, active: w === workspace.activeWindow,
          minimised: w.minimized, desktops: w.desktops.map(d => workspace.desktops.indexOf(d) + 1),
          x: w.frameGeometry.x, y: w.frameGeometry.y, width: w.frameGeometry.width, height: w.frameGeometry.height};
}
function windows() { return workspace.windowList().filter(w => w.normalWindow && !w.skipTaskbar); }
function find(name) {
  const list = windows();
  if (!name) return workspace.activeWindow;
  const wanted = name.toLowerCase();
  return list.find(w => w.resourceClass.toLowerCase() === wanted)
      || list.find(w => w.caption.toLowerCase().includes(wanted) || w.resourceClass.toLowerCase().includes(wanted));
}
"""


def _kwin(body, args):
    """Runs JavaScript in KWin with `args` bound, and returns what the script's `result` was."""
    marker = f"arty-{uuid.uuid4().hex}"
    script = f"const args = {json.dumps(args)};\n{WINDOW_HELPERS}\nlet result = null;\n{body}\nprint('{marker} ' + JSON.stringify(result));\n"
    started = time.time()
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script)
        path = handle.name
    try:
        script_id = _run(["qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", path, marker])
        _run(["qdbus6", "org.kde.KWin", f"/Scripting/Script{script_id}", "org.kde.kwin.Script.run"])
        deadline = time.time() + KWIN_REPLY_SECONDS
        while time.time() < deadline:
            time.sleep(0.15)
            log = subprocess.run(["journalctl", "--user", "--since", f"@{int(started) - 1}", "-o", "cat", "--no-pager"],
                                 capture_output=True, text=True, timeout=COMMAND_SECONDS).stdout
            for line in log.splitlines():
                if marker in line:
                    return json.loads(line.split(marker, 1)[1].strip())
        raise DesktopError("KWin didn't answer")
    finally:
        subprocess.run(["qdbus6", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", marker],
                       capture_output=True, timeout=COMMAND_SECONDS)
        os.unlink(path)


@_on_windows
def desktop_windows():
    try:
        listed = _kwin("result = {windows: windows().map(describe), desktops: workspace.desktops.length, "
                       "current: workspace.desktops.indexOf(workspace.currentDesktop) + 1};", {})
    except DesktopError as error:
        return f"Couldn't list windows: {error}."
    lines = [f"{'* ' if w['active'] else '- '}{w['app']}: {w['title']}" + (" (minimised)" if w["minimised"] else "")
             for w in listed["windows"]]
    return (f"Virtual desktop {listed['current']} of {listed['desktops']}. Windows (* is in front):\n" + "\n".join(lines)) if lines else "No windows are open."


WINDOW_SCRIPT = """
const w = find(args.window);
if (!w) { result = {error: "no window matches " + JSON.stringify(args.window || "the front window")}; }
else {
  const area = workspace.clientArea(KWin.MaximizeArea, w);
  const g = w.frameGeometry;
  switch (args.action) {
    case "focus": w.minimized = false; workspace.activeWindow = w; break;
    case "minimise": w.minimized = true; break;
    case "maximise": w.setMaximize(true, true); break;
    case "restore": w.minimized = false; w.setMaximize(false, false); w.fullScreen = false; break;
    case "close": w.closeWindow(); break;
    case "fullscreen": w.fullScreen = !w.fullScreen; break;
    case "move": w.frameGeometry = {x: args.x ?? g.x, y: args.y ?? g.y, width: g.width, height: g.height}; break;
    case "resize": w.frameGeometry = {x: g.x, y: g.y, width: args.width ?? g.width, height: args.height ?? g.height}; break;
    case "snap_left": w.setMaximize(false, false); w.frameGeometry = {x: area.x, y: area.y, width: area.width / 2, height: area.height}; break;
    case "snap_right": w.setMaximize(false, false); w.frameGeometry = {x: area.x + area.width / 2, y: area.y, width: area.width / 2, height: area.height}; break;
    case "to_desktop": {
      const target = workspace.desktops[(args.desktop || 1) - 1];
      if (!target) { result = {error: "there is no desktop " + args.desktop}; break; }
      w.desktops = [target]; break;
    }
  }
  if (!result) result = {done: describe(w)};
}
"""


@_on_windows
def desktop_window(action, window="", x=None, y=None, width=None, height=None, desktop=None):
    if action not in WINDOW_ACTIONS:
        return f"action must be one of: {', '.join(WINDOW_ACTIONS)}."
    try:
        outcome = _kwin(WINDOW_SCRIPT, {"action": action, "window": window, "x": x, "y": y, "width": width, "height": height, "desktop": desktop})
    except DesktopError as error:
        return f"Couldn't do that: {error}."
    if outcome.get("error"):
        return f"Couldn't: {outcome['error']}. desktop_windows lists what's open."
    done = outcome["done"]
    print(f"[desktop] {action} {done['app']}", flush=True)
    return f"Done: {action.replace('_', ' ')} on {done['app']} ({done['title'][:60]})."


@_on_windows
def desktop_switch(desktop):
    try:
        outcome = _kwin("const d = workspace.desktops[args.desktop - 1]; if (d) { workspace.currentDesktop = d; result = true; } "
                        "else { result = workspace.desktops.length; }", {"desktop": int(desktop)})
    except (DesktopError, ValueError) as error:
        return f"Couldn't switch: {error}."
    return f"Switched to desktop {desktop}." if outcome is True else f"There are only {outcome} desktops."


# ---------- system ----------

def _volume_state():
    text = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])  # e.g. "Volume: 0.45 [MUTED]"
    match = re.search(r"([\d.]+)", text)
    level = round(float(match.group(1)) * 100) if match else None
    return level, "MUTED" in text


@_on_windows
def system_volume(level=None, change=None, mute=None):
    try:
        if level is not None:
            _run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{max(0, min(int(level), 100))}%"])
        if change:
            step = int(change)
            _run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{abs(step)}%{'+' if step > 0 else '-'}"])
        if mute is not None:
            _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if mute else "0"])
        now, muted = _volume_state()
    except (DesktopError, ValueError) as error:
        return f"Couldn't change the volume: {error}."
    return f"Volume is {now}%{' (muted)' if muted else ''}."


def _players():
    names = _run(["busctl", "--user", "list", "--no-pager", "--no-legend"])
    return [line.split()[0] for line in names.splitlines() if line.startswith("org.mpris.MediaPlayer2.")]


def _player_property(player, prop):
    try:
        return _run(["gdbus", "call", "--session", "--dest", player, "--object-path", "/org/mpris/MediaPlayer2",
                     "--method", "org.freedesktop.DBus.Properties.Get", "org.mpris.MediaPlayer2.Player", prop])
    except DesktopError:
        return ""


MEDIA_METHODS = {"play_pause": "PlayPause", "play": "Play", "pause": "Pause", "next": "Next", "previous": "Previous", "stop": "Stop"}


@_on_windows
def system_media(action="status"):
    try:
        players = _players()
    except DesktopError as error:
        return f"Couldn't reach the media players: {error}."
    if not players:
        return "Nothing is playing media (no music or video app is open)."
    playing = [p for p in players if "Playing" in _player_property(p, "PlaybackStatus")]
    player = (playing or players)[0]
    name = player.removeprefix("org.mpris.MediaPlayer2.").split(".")[0]
    if action != "status":
        method = MEDIA_METHODS.get(action)
        if not method:
            return f"action must be status or one of: {', '.join(MEDIA_METHODS)}."
        try:
            _run(["gdbus", "call", "--session", "--dest", player, "--object-path", "/org/mpris/MediaPlayer2",
                  "--method", f"org.mpris.MediaPlayer2.Player.{method}"])
        except DesktopError as error:
            return f"{name} wouldn't {action.replace('_', '/')}: {error}."
        time.sleep(0.3)
    metadata = _player_property(player, "Metadata")
    title = re.search(r"'xesam:title': <'([^']*)'>", metadata)
    artist = re.search(r"'xesam:artist': <\['([^']*)'", metadata)
    state = re.search(r"'(Playing|Paused|Stopped)'", _player_property(player, "PlaybackStatus"))
    what = f"{title.group(1)}" + (f" by {artist.group(1)}" if artist else "") if title else "something"
    return f"{name}: {state.group(1) if state else 'unknown'}, {what}."


@_on_windows
def system_brightness(percent=None):
    destination = ["qdbus6", "org.kde.ScreenBrightness", BRIGHTNESS_PATH]
    try:
        maximum = int(_run(destination + ["org.kde.ScreenBrightness.Display.MaxBrightness"]))
        if percent is not None:
            value = round(max(1, min(int(percent), 100)) / 100 * maximum)
            _run(destination + ["org.kde.ScreenBrightness.Display.SetBrightness", str(value), "0"])
        current = int(_run(destination + ["org.kde.ScreenBrightness.Display.Brightness"]))
    except (DesktopError, ValueError) as error:
        return f"Couldn't change the brightness: {error}."
    return f"Screen brightness is {round(current / maximum * 100)}%."


@_on_windows
def system_wifi(state="status"):
    try:
        if state in ("on", "off"):
            _run(["nmcli", "radio", "wifi", state])
        radio = _run(["nmcli", "radio", "wifi"])
        network = _run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"]) if radio == "enabled" else ""
    except DesktopError as error:
        return f"Couldn't change Wi-Fi: {error}."
    connected = next((line.split(":", 1)[1] for line in network.splitlines() if line.startswith("yes:")), "")
    return f"Wi-Fi is {'on' if radio == 'enabled' else 'off'}" + (f", connected to {connected}." if connected else ".")


@_on_windows
def system_bluetooth(state="status"):
    try:
        if state in ("on", "off"):
            _run(["bluetoothctl", "power", state])
        info = _run(["bluetoothctl", "show"])
        devices = _run(["bluetoothctl", "devices", "Connected"])
    except DesktopError as error:
        return f"Couldn't change Bluetooth: {error}."
    powered = "Powered: yes" in info
    names = [line.split(" ", 2)[2] for line in devices.splitlines() if line.startswith("Device ")]
    return f"Bluetooth is {'on' if powered else 'off'}" + (f", connected to {', '.join(names)}." if names else ".")


@_on_windows
def system_lock():
    try:
        _run(["loginctl", "lock-session"])
    except DesktopError as error:
        return f"Couldn't lock the screen: {error}."
    return "Locked the screen."


_dnd = {"connection": None, "cookie": None}


@_on_windows
def system_do_not_disturb(on=True):
    """Holds a notification inhibition on a connection of our own: it lasts while that connection does."""
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    address = DBusAddress("/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications", interface="org.freedesktop.Notifications")
    try:
        if on and _dnd["cookie"] is None:
            connection = open_dbus_connection(bus="SESSION")
            reply = connection.send_and_get_reply(new_method_call(address, "Inhibit", "ssa{sv}", ("Arty", "Do not disturb", {})), timeout=5)
            _dnd.update(connection=connection, cookie=reply.body[0])
        elif not on and _dnd["cookie"] is not None:
            _dnd["connection"].send_and_get_reply(new_method_call(address, "UnInhibit", "u", (_dnd["cookie"],)), timeout=5)
            _dnd["connection"].close()
            _dnd.update(connection=None, cookie=None)
    except Exception as error:  # jeepney raises several kinds; all mean the same to the user
        return f"Couldn't change do-not-disturb: {error}."
    return "Do not disturb is on: notifications are held back." if on else "Do not disturb is off."


@_on_windows
def system_status():
    parts = []
    try:
        level, muted = _volume_state()
        parts.append(f"volume {level}%{' muted' if muted else ''}")
    except DesktopError:
        pass
    try:
        battery = next((line for line in _run(["upower", "-e"]).splitlines() if "BAT" in line), "")
        if battery:
            info = _run(["upower", "-i", battery])
            percent = re.search(r"percentage:\s+(\S+)", info)
            state = re.search(r"state:\s+(\S+)", info)
            parts.append(f"battery {percent.group(1) if percent else '?'} ({state.group(1) if state else 'unknown'})")
    except DesktopError:
        pass
    with open("/proc/loadavg") as handle:
        load = float(handle.read().split()[0])
    parts.append(f"CPU load {round(load / (os.cpu_count() or 1) * 100)}%")
    with open("/proc/meminfo") as handle:
        memory = {line.split(":")[0]: int(line.split()[1]) for line in handle}
    used = (memory["MemTotal"] - memory["MemAvailable"]) / memory["MemTotal"]
    parts.append(f"memory {round(used * 100)}% used of {memory['MemTotal'] // 1024 // 1024 + 1} GB")
    disk = os.statvfs(os.path.expanduser("~"))
    parts.append(f"{disk.f_bavail * disk.f_frsize // 1024 ** 3} GB free disk")
    return "This computer: " + ", ".join(parts) + "."


@_on_windows
def desktop_notify(title, message=""):
    try:
        _run(["notify-send", "--app-name=Arty", str(title)[:120], str(message)[:500]])
    except DesktopError as error:
        return f"Couldn't show a notification: {error}."
    return "Notification shown."


# ---------- what the app shows and asks ----------

def describe(name, args):
    if name == "desktop_window":
        return f"{str(args.get('action', '')).replace('_', ' ')} {args.get('window') or 'the front window'}"
    return {
        "desktop_windows": "checking your windows", "desktop_switch": f"switching to desktop {args.get('desktop')}",
        "system_volume": "changing the volume", "system_media": f"media: {args.get('action', 'status')}",
        "system_brightness": "changing the brightness", "system_wifi": f"Wi-Fi {args.get('state', 'status')}",
        "system_bluetooth": f"Bluetooth {args.get('state', 'status')}", "system_lock": "locking the screen",
        "system_do_not_disturb": "do not disturb", "system_status": "checking this computer",
        "desktop_notify": "showing a notification", "desktop_notifications": "checking your notifications",
    }.get(name, name)


def needs_ok(name, args):
    """The question to ask first, or None. Closing loses unsaved work; Wi-Fi off cuts Arty off."""
    if name == "desktop_window" and args.get("action") == "close":
        return f"Close {args.get('window') or 'the front window'}? Anything unsaved in it would be lost."
    if name == "system_wifi" and args.get("state") == "off":
        return "Turn Wi-Fi off? Arty can't reach its brain until it's back on."
    return None


def _object(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required)}


SPECS = [
    ("desktop_windows", desktop_windows, {
        "description": "List the windows open on their computer (app and title, which is in front) and the virtual desktops.",
        "parameters": _object({})}),
    ("desktop_window", desktop_window, {
        "description": "Do something to a window on their computer without clicking: focus (bring to front), minimise, maximise, "
                       "restore, close, fullscreen (toggle), move (x, y), resize (width, height), snap_left, snap_right, "
                       "or to_desktop (desktop number). Name the window by app or part of its title; leave it empty for the front window.",
        "parameters": _object({
            "action": {"type": "string", "enum": list(WINDOW_ACTIONS)},
            "window": {"type": "string", "description": "app name or part of the title, e.g. 'firefox', 'Dolphin'"},
            "x": {"type": "integer"}, "y": {"type": "integer"}, "width": {"type": "integer"}, "height": {"type": "integer"},
            "desktop": {"type": "integer", "description": "for to_desktop: the virtual desktop number, from 1"}}, ["action"])}),
    ("desktop_switch", desktop_switch, {
        "description": "Switch to another virtual desktop by number (from 1).",
        "parameters": _object({"desktop": {"type": "integer"}}, ["desktop"])}),
    ("system_volume", system_volume, {
        "description": "Set the speaker volume (level 0-100), change it (change: +10 or -10), or mute/unmute. With nothing given, says the volume.",
        "parameters": _object({"level": {"type": "integer"}, "change": {"type": "integer"}, "mute": {"type": "boolean"}})}),
    ("system_media", system_media, {
        "description": "Control whatever is playing music or video (Spotify, a browser tab, VLC...): status, play_pause, play, pause, next, previous, stop.",
        "parameters": _object({"action": {"type": "string", "enum": ["status", *MEDIA_METHODS]}})}),
    ("system_brightness", system_brightness, {
        "description": "Set the screen brightness in percent (1-100), or say what it is when left out.",
        "parameters": _object({"percent": {"type": "integer"}})}),
    ("system_wifi", system_wifi, {
        "description": "Wi-Fi: status, on or off.",
        "parameters": _object({"state": {"type": "string", "enum": ["status", "on", "off"]}})}),
    ("system_bluetooth", system_bluetooth, {
        "description": "Bluetooth: status (and connected devices), on or off.",
        "parameters": _object({"state": {"type": "string", "enum": ["status", "on", "off"]}})}),
    ("system_lock", system_lock, {
        "description": "Lock the screen.",
        "parameters": _object({})}),
    ("system_do_not_disturb", system_do_not_disturb, {
        "description": "Turn do-not-disturb on (hold back notifications) or off.",
        "parameters": _object({"on": {"type": "boolean"}}, ["on"])}),
    ("system_status", system_status, {
        "description": "This computer's battery, CPU load, memory, free disk space and volume.",
        "parameters": _object({})}),
    ("desktop_notify", desktop_notify, {
        "description": "Pop up a notification on their computer, e.g. when a long job is done.",
        "parameters": _object({"title": {"type": "string"}, "message": {"type": "string"}}, ["title"])}),
]

PROMPT = """
Window and system control: desktop_windows lists what's open; desktop_window focuses, moves,
snaps, minimises or closes a window by name, with no clicking needed; system_volume,
system_media, system_brightness, system_wifi, system_bluetooth, system_do_not_disturb,
system_lock and system_status control and report on the computer itself. Prefer these over
clicking around the screen whenever they can do the job."""
