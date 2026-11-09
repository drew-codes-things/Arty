<div align="center">

# Arty Desktop

**Arty on your Linux PC - talk to it or type, hear it answer, and let it use your computer for you, from a small always-on-top window.**

[![Electron](https://img.shields.io/badge/electron-44-47848F?style=flat-square&logo=electron&logoColor=white)](https://www.electronjs.org/)
[![Python](https://img.shields.io/badge/python-3.12+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Linux](https://img.shields.io/badge/linux-KDE%20Plasma-1D99F3?style=flat-square&logo=kde&logoColor=white)](https://kde.org/plasma-desktop/)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../../LICENSE)

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
itself and [Arty for Chrome](../../extensions/browser/README.md).

---

## Features

- **Voice** - speech recognition (Faster-Whisper) and Arty's voice (Kokoro), both on this PC. Click the orb or press
  **Ctrl+Shift+Space** to talk; tap the orb or press Esc to stop it talking. **WAKE** listens for "Arty, ..."
  hands-free.
- **Hands** - with **HANDS** on, Arty can look at the screen, open apps, click, type, press keys, scroll, run
  commands and read or write files. Anything risky - deleting, sending, installing, touching secrets - shows an
  Allow / Not now card first.
- **Updates** - the **UPD** chip shows reminders, briefings, task results and approvals with a count of new ones.
  New ones are read out, and pop up as a desktop notification while the window isn't focused.
- **Screen and clipboard** - "Ask about my screen" (**Ctrl+Alt+Space**) and "Explain what I copied"
  (**Ctrl+Alt+E**) from the tray.
- **Always there** - a small window in Arty's own style that lives in the tray. **Ctrl+Shift+A** shows or hides it,
  with transparency, keep-on-top and start-with-the-PC in the tray menu.
- **Your browser** - add [Arty for Chrome](../../extensions/browser/README.md) and it can use your own tabs too.

---

## Download

Download `Arty-1.0.0-x86_64.AppImage` from the [Releases](../../../../releases) page (it's also built into
`release/` from source) - one file with its own Python, the voice and speech models and a starter config, so it runs
on a PC that has never seen this project.

> **For now Arty Desktop only works properly on Debian 13 with KDE Plasma.** Windows support may be coming soon.

```bash
sudo apt install libfuse2t64   # FUSE, to run AppImages
chmod +x Arty-1.0.0-x86_64.AppImage
./Arty-1.0.0-x86_64.AppImage
```

On start, Arty checks for the system packages it needs (PortAudio for the microphone, a clipboard tool, a screenshot
tool for your desktop, `xdg-open`, `xprop` on KDE, `xdotool` on X11). If any are missing it lists them and offers to
install them through your desktop's password prompt (pkexec with apt, dnf, pacman or zypper), then restarts its local
service to use them. "Don't ask again" remembers the choice in `~/.config/Arty/skipped-dependencies.json`. A run from
source also reinstalls the Python packages whenever `server/requirements-linux.txt` changes.

The first run opens a **Connect to Arty** screen: enter your brain's address (`http://<brain address>:8081/v1`) and
the Arty API key, and, for use away from home, the Cloudflare address and Access token. Arty checks them against your
brain before saving them to `~/.config/Arty/arty_config.yaml` (readable by you only), then restarts its local service
to use them. **Connect to another Arty…** in the tray menu changes them later.

## Setup From Source

```bash
./setup-linux.sh        # Python venv, Kokoro voice model, Electron
cd app && npm start     # starts the local service too
```

Settings are in `arty_config.yaml` (created from `arty_config.example.yaml` on first run, and not in git).
`brain.api_key` is the Arty API key. If you move the folder, `./fix-venv.sh` repairs the venv's paths.

Building the installer: `./build-app.sh` (needs rsync and network access; it downloads a standalone Python 3.12),
then copy `app/dist/Arty-*.AppImage` to `release/`.

## Running the Tests

```bash
.venv/bin/pip install -r server/requirements-dev.txt
.venv/bin/python -m pytest server/tests
cd app && npm test      # the desktop app's dependency checks
```

---

## Tested On

Debian 13 (trixie), KDE Plasma 6.3.6 in a Wayland session (the window runs through XWayland,
`--ozone-platform=x11`), Linux 6.12, on an Intel Core i5-7300U laptop. Electron 44, Node 20.19.2, Python 3.13.5 for
the source setup and the AppImage's bundled Python 3.12.14, with the brain on the homelab over the LAN.

## License

[PolyForm Strict 1.0.0](https://polyformproject.org/licenses/strict/1.0.0) - see [LICENSE](../../LICENSE). You can download, keep and use Arty for yourself or any other
noncommercial purpose; sharing it, changing it, building on it or using it commercially needs permission.

The voice and computer-control plumbing is adapted from MIT-licensed projects and stays under MIT; see
[NOTICE](NOTICE).
