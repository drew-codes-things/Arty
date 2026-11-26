[Arty](../../README.md) / Arty Desktop

<div align="center">

# Arty Desktop

**Arty on your Linux or Windows PC - talk to it or type, hear it answer, and let it use your computer for you, from a small always-on-top window.**

[![Electron](https://img.shields.io/badge/electron-44-47848F?style=flat-square&logo=electron&logoColor=white)](https://www.electronjs.org/)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Linux](https://img.shields.io/badge/linux-KDE%20Plasma-1D99F3?style=flat-square&logo=kde&logoColor=white)](https://kde.org/plasma-desktop/)
[![Windows](https://img.shields.io/badge/windows-10%20and%2011-0078D4?style=flat-square&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](../../LICENSE)

</div>

---

## Overview

Arty's brain (the root of this project, on the homelab) does the thinking and holds the persona, long-term memory
and its own tools. This app gives it a voice, ears and hands on your own PC: speech recognition and Arty's voice
both run locally, and with your say-so it can look at the screen, open apps, click, type and run commands.

---

## How It Works

```
app/ (Electron window)  ──HTTP──▶  server/arty_server.py (127.0.0.1:8765)  ──▶  Arty's brain (192.0.2.13:8081)
                                     voice in/out, runs the brain's            persona, memory, models,
                                     computer_* tool calls here                web, homelab, its own VM
```

The brain gets this PC's tools as OpenAI "client tools" (header `X-Arty-Client-Tools: 1`); when it calls one,
`server/process/brain.py` runs it locally and sends back the result. Screen reading asks the brain's vision model
directly (`X-Arty-Direct-Vision: 1`). The local service only listens on this computer, and only answers the app
itself and [Arty for your browser](../../extensions/browser/README.md).

---

## Features

- **Voice** - speech recognition (Faster-Whisper) on this PC. Arty's voice comes from your voice server when
  `tts.server.host` in `arty_config.yaml` is set and the server answers (it speaks to this PC through the Wyoming speech
  port, 10300, so `SATELLITE_ALLOW` on the voice server must list this PC; see [voice](../../voice/README.md)), and from
  Kokoro on this PC when it doesn't. Kokoro loads the first time it has to speak and is let go after 10 minutes without
  speaking (`tts.kokoro.idle_unload_seconds`), which gives about 600 MB back. If `models/kokoro/kokoro-v1.0.int8.onnx`
  (about 90 MB, fetched with `./download-kokoro.sh light`) is there it is used instead of the 325 MB model. Click the
  orb or press **Ctrl+Shift+Space** to talk; tap the orb or press Esc to stop it talking. **WAKE** listens for
  "Arty, ..." hands-free.
- **Presentation mode** - for showing Arty to other people. The screen chip beside the orb, or **Ctrl+Shift+P** while
  the window is in front, hides the Memory and Updates views, the chat history on screen, the read-aloud chip, desktop
  notifications and spoken updates, models from the private add-on, addresses in error messages and tooltips, and the
  tray's connect, phone code and Chrome sites screens; the text is bigger and a **Presenting** badge shows. Arty answers
  as normal. It is remembered between runs and off by default.
- **Hands** - off until you turn **HANDS** on. Then Arty can look at the screen, open apps, click, type, press keys,
  scroll, run commands and read or write files. The first click or key press in each reply, programs that aren't
  installed apps, changes to startup files (`~/.bashrc`, autostart, `~/.local/bin`) and anything risky - deleting,
  sending, installing, overwriting - show an Allow / Not now card first. A new card flashes **New question** and
  ignores clicks, Enter and Space for its first second on screen, so keys or a click meant for something else
  cannot answer it. Passwords, keys, browser profiles, `.env`
  files and Arty's own settings are off limits.
  Before clicking, typing, pressing keys or scrolling, Arty checks that a password manager isn't the window in
  front. That check needs to know which window is in front: Windows and X11 always say, KDE on Wayland only with
  `kdotool` installed, and other Wayland desktops (GNOME and the rest) don't say at all. Without it, input goes to
  whichever window has focus, so keep password managers closed while Arty is using the computer.
- **Models** - the menu next to Arty's name lists **Arty Auto** first (the default: it picks the model for each
  message, and each reply says which model and role it used), then every model Arty offers, once each. The bar
  above the message box shows turns, steps, speed, tokens, cache hit and how full the context window is.
- **Memory** - the **MEM** chip lists what Arty remembers, with search, add, edit and delete; changes reach every
  chat on every app.
- **Updates** - the **UPD** chip shows reminders, briefings, task results and approvals with a count of new ones.
  New ones are read out, and pop up as a desktop notification while the window isn't focused.
- **Screen and clipboard** - "Ask about my screen" (**Ctrl+Alt+Space**) and "Explain what I copied"
  (**Ctrl+Alt+E**) from the tray.
- **Always there** - a small window in Arty's own style that lives in the tray. **Ctrl+Shift+A** shows or hides it,
  with transparency, keep-on-top and start-with-the-PC in the tray menu.
- **Calls** - the phone button calls Arty: it picks up with a greeting, then listens and answers in turns, talking
  the way people do on the phone. The call bar has a timer, **Mute** and **Hang up**; notifications aren't read out
  during a call.
- **Noticing** (off by default) - with `notice: enabled: true` in `arty_config.yaml`, Arty says something when you
  come back after `away_minutes` (20) away, or after `focus_minutes` (120) in one app, at most once every 30 minutes.
  Windows reads the time since your last key press or mouse move. On Linux it uses the desktop's idle time, or
  where the desktop won't give that (KDE on Wayland), how long the screen has been locked. Spotting long stretches
  in one app needs the app in front, which KDE on Wayland only gives with `kdotool` installed. Nothing is stored.
- **Link to starfire Desktop** - starfire Desktop on the same PC can pair with Arty and ask for computer steps.
  **Link starfire…** in the tray menu shows a 6 digit code (one use, two minutes, five tries and it is gone). Each step
  a linked app asks for shows an Allow / Not now card first, whatever Arty would have done on its own, and needs
  **HANDS** on. From 1.6.0, when both apps run as you on the same computer, starfire Desktop links by itself and
  the code is only needed as a fallback. **Unlink** in the same screen, or in starfire Desktop, ends it. From 1.6.0 starfire Desktop is told
  what it may ask for (look at the screen, open a page in one of your connected browsers) and can ask Arty to open a
  page, with a card naming the browser and the whole address. How the pairing, the cards, window screenshots and
  opening pages work: [Linking Arty Desktop to starfire Desktop](../../docs/desktop-starfire-link.md).
- **Your browser** - add [Arty for your browser](../../extensions/browser/README.md) (Chrome, Edge, Brave, Chromium
  and Firefox) and it can open, read, scroll, look at, click and type in your own tabs too. Not released yet, coming
  in Arty Desktop 1.6.0 with extension 1.6 or newer: Opera and Vivaldi, and choosing a browser by name. With it paired in
  several browsers, ask for one by name ("open the BBC in Firefox"); Arty is told which are connected, and asking for
  one that isn't gets an answer naming those that are, never another browser instead. Choosing by name needs
  extension 1.6 or newer.
  Pair it once with the code under **Chrome sites…** in the tray menu, or, from Arty Desktop 1.6.0 with extension
  1.7, not at all: Arty finds browsers that have the extension and connects them by itself through native
  messaging ([how](../../extensions/browser/README.md#pairing-by-itself)). The **Browsers** section of that screen
  shows each one with one line saying whether it is connected, and a **Connect** button that sets up what Arty can
  by itself; it also switches this off, and on Linux switch it off before deleting the AppImage. The same screen lists
  the sites Arty may use, each with a **Forget** button. Once Arty has read a page in your browser, any command, file change or link it
  opens in that same reply asks first.
- **Arty's own browser** - from 1.6.0 Arty can also run a separate browser of its own, not signed in to anything of
  yours, to look things up: [Arty's own browser, screen control and quizzes](../../docs/desktop-control.md).
- **Screen control** - from 1.6.0, off until you tick **Let Arty control the screen** in the tray menu: Arty can
  click, type and send a Discord or Slack message on your screen, asking every time with the exact name and text, with
  a red bar and Stop button while it works, stopping the moment you touch the mouse or keyboard
  ([how](../../docs/desktop-control.md#screen-control)).

---

## Download

Download the file for your system from the [Releases](../../../../releases) page (it's also built into
`release/` from source). Each is one file with its own Python, the voice and speech models and a starter config, so it
runs on a PC that has never seen this project.

> **On Linux, Arty Desktop works properly on Debian 13 with KDE Plasma. On Windows it targets Windows 10 and 11.**

### Linux

```bash
sudo apt install libfuse2t64   # FUSE, to run AppImages
chmod +x Arty-1.7.0-x86_64.AppImage
./Arty-1.7.0-x86_64.AppImage
```

On start, Arty checks for the system packages it needs (PortAudio for the microphone, a clipboard tool, a screenshot
tool for your desktop, `xdg-open`, `xprop` on KDE, `xdotool` on X11). If any are missing it lists them and offers to
install them through your desktop's password prompt (pkexec with apt, dnf, pacman or zypper), then restarts its local
service to use them. "Don't ask again" remembers the choice in `~/.config/Arty/skipped-dependencies.json`. A run from
source also reinstalls the Python packages whenever `server/requirements-linux.txt` changes.

### Windows

Run `Arty-Setup-1.7.0.exe`. It installs for your own account (no administrator needed), carries its own Python and
the Microsoft Visual C++ runtime the speech engine uses, and adds a Start menu and desktop shortcut. The installer is
not code-signed, so Windows SmartScreen shows "Windows protected your PC" the first time: choose **More info**, then
**Run anyway**.

On Windows Arty looks at the screen with Pillow and clicks, types and scrolls with the Win32 `SendInput` call, so there
is nothing extra to install. Commands run in Windows PowerShell, and anything destructive (deleting, killing
processes, registry and firewall changes, downloads, installs) asks you first. Windows blocks input into programs
running as administrator, so Arty cannot click or type in those. Reading other apps' notifications aloud is not
possible on Windows (it needs a packaged-app listener), so that one tool is Linux only; Arty's own notifications work.
"Start with the PC" in the tray menu uses Windows' own login items.

The first run opens a **Connect to Arty** screen: enter your brain's address (`http://<brain address>:8081/v1`) and
the Arty API key, and, for use away from home, the Cloudflare address and Access token. Arty checks them against your
brain before saving them to `~/.config/Arty/arty_config.yaml` on Linux or `%APPDATA%\Arty\arty_config.yaml` on Windows (readable by you only), then restarts its local service
to use them. **Connect to another Arty…** in the tray menu changes them later. **Link a phone…** in the tray menu
shows a one-use QR code for the Android and iPhone apps to scan (two minutes, no keys in it) and lists the linked
phones, each with **Remove** ([docs/connecting-a-phone.md](../../docs/connecting-a-phone.md)).

## Setup From Source

```bash
./setup-linux.sh        # Python venv, Kokoro voice model, Electron
cd app && npm start     # starts the local service too
```

On Windows, from PowerShell:

```powershell
.\setup-windows.ps1     # Python venv, Kokoro voice model, Electron
cd app; npm start       # starts the local service too
```

Settings are in `arty_config.yaml` (created from `arty_config.example.yaml` on first run, and not in git).
`brain.api_key` is the Arty API key. If you move the folder, `./fix-venv.sh` repairs the venv's paths.

Building the installer: `./build-app.sh` (needs rsync and network access; it downloads a standalone Python 3.12),
then copy `app/dist/Arty-*.AppImage` to `release/`. On Windows, `.\build-installer.ps1` (needs Node.js and the
Microsoft Visual C++ runtime on the build machine; it downloads Python 3.12 and the models) writes
`app\dist\Arty-Setup-*.exe`.

## New Versions

Arty Desktop asks GitHub for the latest Arty release (drew-codes-things/Arty) a minute after it starts and then once a day, and compares its tag with its own version. When a newer version is out, a small notice says so with a button that opens the release page in your browser; nothing is downloaded for you. Dismiss it and it stays away until the next version. Turn it off with **Tell me about new versions** in the tray menu.

## Tests

```bash
.venv/bin/pip install -r server/requirements-dev.txt
.venv/bin/python -m pytest server/tests
cd app && npm test      # the desktop app's dependency checks
```

On Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`; the suite skips the Linux-only tests there
and runs the real PowerShell ones.

---

## Tested On

Debian 13 (trixie), KDE Plasma 6.3.6 in a Wayland session (the window runs through XWayland,
`--ozone-platform=x11`), Linux 6.12, on an Intel Core i5-7300U laptop. Electron 44, Node 20.19.2, Python 3.13.5 for
the source setup and the AppImage's bundled Python 3.12.14, with the brain on the homelab over the LAN.

Windows 10 Pro 22H2 (build 19041) in a virtual machine, with the installer's bundled Python 3.12.10 and Electron 44.
Typing, clicking, screenshots, window moves and the command tool were run on its real desktop. A machine with a
microphone, speakers, Wi-Fi, Bluetooth, a screen with brightness control and a Windows 11 install have not been tried.

## License

[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) - see [LICENSE](../../LICENSE) and [NOTICE](../../NOTICE). You can use, change and share Arty,
including commercially; if you share it or anything built from it, keep the LICENSE and NOTICE files, which credit Arty's author, and say what you changed.

The voice and computer-control plumbing is adapted from MIT-licensed projects and stays under MIT; see
[NOTICE](NOTICE).
