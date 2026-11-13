<div align="center">

# Arty for Chrome

**A browser extension that lets Arty Desktop use your own browser for you - open pages, read them, scroll, look at them, click and type in your tabs. It loads in Google Chrome, Microsoft Edge, Brave, Chromium and Firefox.**

[![Chrome](https://img.shields.io/badge/chrome-manifest%20v3-4285F4?style=flat-square&logo=googlechrome&logoColor=white)](https://developer.chrome.com/docs/extensions)
[![JavaScript](https://img.shields.io/badge/javascript-F7DF1E?style=flat-square&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../../LICENSE)

</div>

---

## Overview

The companion to [Arty Desktop](../../apps/desktop/README.md). It takes browser commands from the desktop app's
local service on `127.0.0.1:8765` and runs them in your browser. That service only listens on your computer, and
only accepts the app itself and this extension (recognised by its fixed id in Chrome, Edge and Brave or its
`moz-extension://` address in Firefox, plus a pairing code you paste once, and only for its own browser queue), so
nothing else can drive your browser or your computer through it. What you have typed into password, card and
one-time-code fields is never read out to Arty, and while any of those fields has something in it Arty won't take a
picture of the page either. With Arty Desktop closed, the extension does nothing.

What Arty can do in your browser:

| Tool | What it does |
|---|---|
| `chrome_tabs` | lists your tabs (titles and addresses only for sites you've allowed) |
| `chrome_open` | opens a page, or searches for words, and reads it |
| `chrome_read` | reads a tab: its main results first, then fields and other links and buttons, numbered, then its text; menus, sidebars, ads and short videos are counted, not listed |
| `chrome_find` | finds links, buttons and fields anywhere on the page by the words on them |
| `chrome_scroll` | scrolls down or up a screen, or to the top or bottom, and reads what came into view |
| `chrome_look` | takes a picture of the tab and has the vision model describe it (videos, pictures, layout) |
| `chrome_click` | clicks a link or button by its number or its text |
| `chrome_type` | types into a field; never passwords or card details |
| `chrome_tab` | switches to, closes, reloads or goes back or forward in a tab |
| `chrome_forget_site` | forgets a site you allowed |

The names start with `chrome_` whichever browser you use.

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

1. Load the extension:
   - **Google Chrome, Chromium, Brave**: go to `chrome://extensions` (Brave: `brave://extensions`), turn on
     **Developer mode** (top right), click **Load unpacked** and choose this folder (`extensions/browser`).
   - **Microsoft Edge**: the same from `edge://extensions` (**Developer mode** is on the left).
   - **Firefox**: go to `about:debugging#/runtime/this-firefox`, click **Load Temporary Add-on...** and choose
     `manifest.json` in this folder. Firefox removes temporary add-ons when it closes, so load it again after a
     restart. To keep it, Firefox needs it signed by Mozilla; Firefox ESR, Developer Edition and Nightly can
     instead install it unsigned once `xpinstall.signatures.required` is set to `false` in `about:config`: zip the
     files in this folder (not the folder itself, and without `tests/`) into `arty-browser.xpi` and choose
     **Install Add-on From File...** in `about:addons`.
2. In Arty Desktop, open the tray menu and choose **Chrome sites…**. Click the Arty for Chrome icon in your
   browser's toolbar (in Firefox and Edge it may be under the puzzle-piece extensions button) and paste the pairing
   code shown there. Until it is paired the icon shows a red **!** and the extension does nothing.
   The same screen lists the sites Arty may use, each with a **Forget** button.

After pulling a newer version, click the reload icon on its card. Version 1.2 has a fixed extension id that Arty
Desktop checks, so when updating from 1.1 remove the old card and choose **Load unpacked** again; Arty Desktop
ignores any other extension. Version 1.3 also needs the pairing code (step 2) once after updating. Version 1.4 adds
scrolling, looking at a tab and Firefox; Arty Desktop needs the matching update for those.

## Usage

With Arty Desktop open and **HANDS** on, ask for something in your browser - "open the BBC weather page", "what's
on the tab I've got open?", "search YouTube for homelab tours", "scroll down and tell me what's next", "what does
this page look like?". The browser tools are only offered while the extension has checked in during the last
minute, and only with **HANDS** on.

---

## Tested On

Google Chrome 156.0.8078.47 on Debian 13 (trixie) with KDE Plasma 6.3.6, alongside Arty Desktop 1.0.0. Version 1.4
with Chromium 156.0.8078.79 and Firefox ESR 153.7.0 on Debian 13 under Xvfb, against Arty Desktop's local service
from source. Edge and Brave load the same Chromium manifest but were not tried.

## License

[PolyForm Strict 1.0.0](https://polyformproject.org/licenses/strict/1.0.0) - see [LICENSE](../../LICENSE). You can download, keep and use Arty for yourself or any other
noncommercial purpose; sharing it, changing it, building on it or using it commercially needs permission.
