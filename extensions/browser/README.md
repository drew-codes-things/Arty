<div align="center">

# Arty for Chrome

**A Chrome extension that lets Arty Desktop use your own browser for you - open pages, read them, click and type in your tabs.**

[![Chrome](https://img.shields.io/badge/chrome-manifest%20v3-4285F4?style=flat-square&logo=googlechrome&logoColor=white)](https://developer.chrome.com/docs/extensions)
[![JavaScript](https://img.shields.io/badge/javascript-F7DF1E?style=flat-square&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../../LICENSE)

</div>

---

## Overview

The companion to [Arty Desktop](../../apps/desktop/README.md). It takes browser commands from the desktop app's
local service on `127.0.0.1:8765` and runs them in your Chrome. That service only listens on your computer, and
only accepts the app itself and this extension (recognised by its fixed id and a pairing code you paste once, and
only for its own browser queue), so nothing else can drive your browser or your computer through it. What you have typed into password, card and
one-time-code fields is never read out to Arty. With Arty
Desktop closed, the extension does nothing.

---

## How It Works

```
You: "what's on the tab I've got open?"
        |
Arty Desktop  ->  local service (127.0.0.1:8765)
        |
This extension (polling the service)  ->  reads, opens, clicks or types in your tabs
        |
Anything that sends, buys, posts or deletes  ->  Allow / Not now card in Arty Desktop first
```

Pages Arty reads are handed to it marked as information, not instructions, to help stop a website steering it. Arty
is also told to leave logins and CAPTCHAs to you - that's an instruction to the model rather than something the
extension enforces, so keep an eye on it.

---

## Setup

1. In Google Chrome, go to `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and choose this folder (`extensions/browser`).
3. In Arty Desktop, open the tray menu and choose **Chrome sites…**. Click the Arty for Chrome icon in Chrome and
   paste the pairing code shown there. Until it is paired the icon shows a red **!** and the extension does nothing.
   The same screen lists the sites Arty may use, each with a **Forget** button.

After pulling a newer version, click the reload icon on its card. Version 1.2 has a fixed extension id that Arty
Desktop checks, so when updating from 1.1 remove the old card and choose **Load unpacked** again; Arty Desktop
ignores any other extension. Version 1.3 also needs the pairing code (step 3) once after updating.

## Usage

With Arty Desktop open, ask for something in your browser - "open the BBC weather page", "what's on the tab I've
got open?", "search YouTube for homelab tours".

---

## Tested On

Google Chrome 156.0.8078.47 on Debian 13 (trixie) with KDE Plasma 6.3.6, alongside Arty Desktop 1.0.0.

## License

[PolyForm Strict 1.0.0](https://polyformproject.org/licenses/strict/1.0.0) - see [LICENSE](../../LICENSE). You can download, keep and use Arty for yourself or any other
noncommercial purpose; sharing it, changing it, building on it or using it commercially needs permission.
