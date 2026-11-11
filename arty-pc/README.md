<div align="center">

# Arty's PC

**The scripts that turn a plain Debian + XFCE virtual machine into Arty's own computer - a browser it drives, a Gmail window it reads, a terminal you can watch, and a desktop it decorates itself.**

[![Debian](https://img.shields.io/badge/debian-13-A81D33?style=flat-square&logo=debian&logoColor=white)](https://www.debian.org/)
[![XFCE](https://img.shields.io/badge/xfce-4.20-2284F2?style=flat-square&logo=xfce&logoColor=white)](https://xfce.org/)
[![Python](https://img.shields.io/badge/python-3.13-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../LICENSE)

</div>

---

## Overview

Arty works on a computer of its own, a VM named `ai-desktop` (192.0.2.14), rather than yours. The brain reaches it
over SSH (`mcp/remote.py`) to browse, read email and documents, and run commands - and you can watch or help from
the Proxmox console at any time.

---

## What's In Here

| File | Installed to | What it does |
|---|---|---|
| `browser_agent.py`, `run.sh` | `~/.local/browser-agent/` | Arty's visible browser (Google Chrome with a localhost-only debugging port; Chromium and Firefox are not installed) and the agent that drives it. `run.sh` keeps it running, and waits while you're signing in. It also serves the Gmail extension on 127.0.0.1:9334. Both ports only answer requests that carry the agent's key (`~/.local/browser-agent/token`, made on first start, owner-only) and refuse anything sent from a web page |
| `sign-in-browser`, `sign-in-browser.desktop` | `~/.local/bin/`, desktop shortcut | **Sign in to websites** - pauses Arty's browser and opens the same profile as a normal window, so sites that refuse automated browsers accept your sign-in |
| `sign-in-google`, `sign-in-google.desktop` | `~/.local/bin/`, desktop shortcut | **Sign in to Google** - the separate Google Chrome window (profile `~/.gmail-chrome`) that Arty reads Gmail in, with the [Gmail extension](../extensions/gmail/README.md) |
| `ai-tmux` | `/usr/local/bin/` (copy it by hand; `deploy.sh` doesn't) | The shared terminal (tmux session `assistant`) - you can watch the commands Arty runs |
| `doc2text` | `/usr/local/bin/` | Prints any document's text (PDF, Office, images by OCR, ...) for `read_document` and the `~/library` search |
| `arty-style` | `~/.local/bin/` | Arty's own desktop look - draws a wallpaper with its motto and sets a matching theme, icons and terminal colours. The brain picks the look (`brain/desktop_look.py`) |

---

## Setup

1. Make a Debian 13 VM with XFCE, a user `ai` with SSH key login from the brain, and the QEMU guest agent.
2. From the project root, run `./provision.sh` once (document, OCR and clipboard tools), then `./deploy.sh`. That
   copies these scripts, installs the Arc-Dark and Papirus-Dark themes if they're missing, and puts the two sign-in
   shortcuts on the desktop. yt-dlp is installed once and then left alone: YouTube breaks old versions within months,
   so when downloads start failing, update it on purpose with `YT_DLP_VERSION=<version> ./deploy.sh` (the version
   from https://pypi.org/project/yt-dlp/, digits and dots only).
3. Install Google Chrome for the Gmail window, then follow [extensions/gmail/README.md](../extensions/gmail/README.md).
4. Keep the VM off the rest of your network: run `bin/desktop-egress apply` from the project root, which loads
   rules on the Proxmox host (internet, DNS and DHCP stay open; `status` shows the rules, `undo` removes them), and pin its ssh key for the
   brain with `bin/pin-desktop-host-key`. After rebuilding the VM, check its new key on the console and run
   `bin/pin-desktop-host-key --trust-fingerprint <fingerprint>`.

## Usage

- **Watch or help** - Proxmox web UI > the desktop VM > Console.
- **Logins and bot checks** - when a site blocks Arty, it asks you. Double-click **Sign in to websites**, sign in,
  close the window, and Arty's browser comes back with the logins. Arty never gets round CAPTCHAs itself.
- **Cookie banners** - after opening a page or clicking, the agent accepts a cookie/consent banner if one is
  showing (an "Accept all"-style button inside the banner). It never clicks reject, manage or sign-in buttons.
- **Documents** - put files in `~/library` (or ask Arty to) and it finds them by meaning.
- **Private folders** - Arty's tools refuse to read, list, write, upload or run commands in `~/.gmail-chrome`,
  `~/.browser-profile`, `~/.local/browser-agent` and `~/.ssh`, checked again by real path on the VM so a link into
  them is refused too.
- **Its desktop** - ask Arty to "customise your desktop" and it picks a new look. It also tinkers now and then while
  you're not using it (`"desktop_look"` in `brain/brain.json`).

---

## Tested On

The desktop VM on Proxmox VE 9.2.21 - Debian 13 (trixie), XFCE 4.20, Google Chrome 156.0.8078.47, Python 3.13.5,
ImageMagick 7.1.

## License

[PolyForm Strict 1.0.0](https://polyformproject.org/licenses/strict/1.0.0) - see [LICENSE](../LICENSE). You can download, keep and use Arty for yourself or any other
noncommercial purpose; sharing it, changing it, building on it or using it commercially needs permission.
