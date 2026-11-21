[Arty](../README.md) / Arty's PC

<div align="center">

# Arty's PC

**The scripts that turn a plain Debian + XFCE virtual machine into Arty's own computer - a browser it drives, a Gmail window it reads, a terminal you can watch, and a desktop it decorates itself.**

[![Debian](https://img.shields.io/badge/debian-13-A81D33?style=flat-square&logo=debian&logoColor=white)](https://www.debian.org/)
[![XFCE](https://img.shields.io/badge/xfce-4.20-2284F2?style=flat-square&logo=xfce&logoColor=white)](https://xfce.org/)
[![Python](https://img.shields.io/badge/python-3.13-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](../LICENSE)

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
| `sign-in-browser`, `sign-in-browser.desktop` | `/usr/local/bin/` and `/usr/local/share/applications/` (owned by root), desktop shortcut | **Sign in to websites** - pauses Arty's browser and opens the same profile (`~/.browser-profile`) as a normal window, so sites that refuse automated browsers accept your sign-in. Not for your own Drive, Calendar or starfire: this browser never opens them |
| `sign-in-apps`, `sign-in-apps.desktop` | `/usr/local/bin/` and `/usr/local/share/applications/` (owned by root), desktop shortcut | **Sign in to Starfire apps** - opens the separate apps browser (profile `~/.apps-browser-profile`) on the starfire, Drive and Calendar pages. Sign in to starfire there once: only Arty's [app actions](../docs/acting-in-your-apps.md) use this browser, started by the agent without a debugging port for each call and closed after it. App actions wait while this window is open |
| `own-apps.json` | `/etc/arty/` (written by `deploy.sh`, owned by root) | The only Drive and Calendar addresses the agent will call, plus the starfire address (`DRIVE_ADDRESS`, `CALENDAR_ADDRESS`, `STARFIRE_ADDRESS` in `hosts.env`). Each call runs in the apps browser on that address, only for the calls the app actions need. Arty's own browser refuses every step on these hosts and aborts every request to them |
| `sign-in-google`, `sign-in-google.desktop` | `/usr/local/bin/` and `/usr/local/share/applications/` (owned by root), desktop shortcut | **Sign in to Google** - the separate Google Chrome window (profile `~/.gmail-chrome`) that Arty reads Gmail in and sends approved emails from, with the [Gmail extension](../extensions/gmail/README.md) |
| `web_host.py` | `~/.local/browser-agent/` | Reads an address's host the way a browser does (full-width letters, other dots, percent signs, international names) and refuses one with a backslash or control character, so no way of writing an own app's address gets past the check. The brain and the tool hub carry the same file |
| `own-app-cookie-rows` | run by `deploy.sh` over SSH, not installed | Counts cookies in Arty's browser profile (`~/.browser-profile`) for an own app, its subdomains or a parent domain. `deploy.sh` stops before copying anything if there are any, or if they can't be counted |
| `ai-tmux` | `/usr/local/bin/` (copy it by hand; `deploy.sh` doesn't) | The shared terminal (tmux session `assistant`) - you can watch the commands Arty runs |
| `doc2text` | `/usr/local/bin/` | Prints any document's text (PDF, Office, images by OCR, ...) for `read_document` and the `~/library` search |
| `arty-style` | `~/.local/bin/` | Arty's own desktop look - draws a wallpaper with its motto and sets a matching theme, icons and terminal colours. The brain picks the look (`brain/desktop_look.py`) |

---

## Setup

1. Make a Debian 13 VM with XFCE, a user `ai` with SSH key login from the brain, and the QEMU guest agent.
2. From the project root, run `./provision.sh` once (document, OCR and clipboard tools), then `./deploy.sh`. That
   copies these scripts, installs the Arc-Dark and Papirus-Dark themes if they're missing, and puts the three sign-in
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
- **One tab** - every page Arty opens goes into one working tab. Opening a page closes the browser's other tabs
  (ones sites opened, crashed ones, ones left from before a restart), so research never piles them up. Chrome
  starts without the "Restore pages?" bubble.
- **Repeated chores** - when the agent finds a button or field by its text, it remembers a CSS selector for it per
  site and step in `~/.local/browser-agent/selectors.json` (owner-only, the 300 most recently used, no page text or
  cookies). Next time it tries that selector first, checks the element still has the same kind and text, and
  searches again if it doesn't. Nothing is remembered on sign-in, payment or bot-check pages, or for steps that
  name an email address or a long number.
- **Documents** - put files in `~/library` (or ask Arty to) and it finds them by meaning.
- **Private folders** - Arty's tools refuse to read, list, write, upload or run commands in `~/.gmail-chrome`,
  `~/.browser-profile`, `~/.apps-browser-profile`, `~/.local/browser-agent` and `~/.ssh`, checked again by real path
  on the VM so a link into them is refused too.
- **Its desktop** - ask Arty to "customise your desktop" and it picks a new look. It also tinkers now and then while
  you're not using it (`"desktop_look"` in `brain/brain.json`).

---

## Tested On

The desktop VM on Proxmox VE 9.2.21 - Debian 13 (trixie), XFCE 4.20, Google Chrome 156.0.8078.47, Python 3.13.5,
ImageMagick 7.1.

## License

[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) - see [LICENSE](../LICENSE) and [NOTICE](../NOTICE). You can use, change and share Arty,
including commercially; if you share it or anything built from it, keep the LICENSE and NOTICE files, which credit Arty's author, and say what you changed.
