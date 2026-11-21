[Arty](../../README.md) / Arty for your browser

<div align="center">

# Arty for your browser

**A browser extension that lets Arty Desktop use your own browser for you - open pages, read them, scroll, look at them, click and type in your tabs. It loads in Google Chrome, Microsoft Edge, Brave, Opera, Vivaldi, Chromium and Firefox, and Arty can use each one by name.**

[![Chrome](https://img.shields.io/badge/chrome-manifest%20v3-4285F4?style=flat-square&logo=googlechrome&logoColor=white)](https://developer.chrome.com/docs/extensions)
[![JavaScript](https://img.shields.io/badge/javascript-F7DF1E?style=flat-square&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](../../LICENSE)

</div>

> **Latest release.** Version 1.7 is attached to the
> [1.6.0 release](https://github.com/drew-codes-things/Arty/releases/tag/v1.6.0). It pairs with Arty Desktop by itself
> (see [Pairing by itself](#pairing-by-itself)) and needs Arty Desktop 1.6.0 for that. Version 1.6 tells Arty Desktop
> which browser it is in so Arty can use a browser by name, and added the Opera and Vivaldi steps below; choosing a
> browser by name needs Arty Desktop 1.5.9 or newer.

---

## Overview

The companion to [Arty Desktop](../../apps/desktop/README.md). It takes browser commands from the desktop app's
local service on `127.0.0.1:8765` and runs them in your browser. That service only listens on your computer, and
only accepts the app itself and this extension (recognised by its fixed id in Chrome, Edge, Brave, Opera, Vivaldi and Chromium or its
`moz-extension://` address in Firefox, plus a pairing code you paste once, and only for its own browser queue), so
nothing else can drive your browser or your computer through it. The extension in turn sends its pairing code, and
takes commands, only after the service has proved it holds the same code, so another program listening on that port
gets nothing from it. Password, passcode, PIN, one-time-code, card, CVV, IBAN, sort code, account number, national
insurance, security answer and API key fields are recognised by their type, label, placeholder, name, id or
autocomplete: what you have typed into them is never read out to Arty, Arty never types into them, and while any of
them has something in it Arty won't take a picture of the page either. With Arty Desktop closed, the extension does
nothing.

What Arty can do in your browser:

| Tool | What it does |
|---|---|
| `chrome_tabs` | lists your tabs (titles and addresses only for sites you've allowed) |
| `chrome_open` | opens a page, or searches for words, and reads it |
| `chrome_read` | reads a tab: its main results first, then fields and other links and buttons, numbered, then its text; menus, sidebars, ads and short videos are counted, not listed |
| `chrome_find` | finds links, buttons and fields anywhere on the page by the words on them |
| `chrome_scroll` | scrolls down or up a screen, or to the top or bottom, and reads what came into view |
| `chrome_look` | takes a picture of the tab and has the vision model describe it (videos, pictures, layout) |
| `chrome_click` | clicks a link or button by its number, or by its whole label |
| `chrome_type` | types into a field; never password, code or payment fields |
| `chrome_tab` | switches to, closes, reloads or goes back or forward in a tab |
| `chrome_forget_site` | forgets a site you allowed |

The names start with `chrome_` whichever browser you use. Each of them except `chrome_forget_site` also takes an
optional `browser`, so "open the BBC in Firefox" or "read my tab in Opera" goes to that browser.

### More than one browser

Install and pair the extension in each browser you want Arty to use. The extension tells Arty Desktop which browser
it is in (Chrome, Edge, Brave, Opera, Vivaldi, Chromium or Firefox), and Arty is told which of them are connected
right now, so it only offers those. Ask for a browser by name and that browser is used; ask for one that isn't
paired or isn't open and Arty says so, names the browsers that are connected and says the extension has to be
installed there: it never quietly uses another browser instead. Without a name, a reply uses the browser Arty used
last, while it is still open, as before. Two paired copies of the same browser (two profiles, say) are told apart as
"Chrome" and "Chrome 2", the first one to check in keeping the plain name.

The name is only used to pick a browser and to show it. It decides nothing about what is allowed: each browser
still needs the pairing code, keeps its own random id and its own queue of commands, and a name that isn't on the
list above, or a Chromium browser calling itself Firefox (or the other way round), is ignored.

---

## How It Works

```
You: "what's on the tab I've got open?"
        |
Arty Desktop  ->  local service (127.0.0.1:8765)
        |
This extension (polling the service)  ->  reads, opens, clicks or types in your tabs
        |
Anything that submits a form, downloads a file, leads to a payment, account or permission page, leaves for a
site you haven't allowed, or is labelled with words like buy, pay, allow, send, post or delete
        ->  Allow / Not now card in Arty Desktop first
```

Everything Arty reads from a page, its title, address, labels, field values and text, is handed to it inside a
marker saying it is information, not instructions, to help stop a website steering it. Text and links a person can't
see (faded out, moved off the page, tiny or hidden from screen readers) are left out. A click goes ahead only if the
item still has the label and does the same kind of thing it had when Arty read the page. After Arty reads a page,
Arty Desktop asks before any command, file change or link in the same reply. Arty is also told to leave logins and
CAPTCHAs to you - that's an instruction to the model rather than something the extension enforces, so keep an eye
on it.

---

## Setup

1. Load the extension:
   - **Google Chrome, Chromium, Brave**: go to `chrome://extensions` (Brave: `brave://extensions`), turn on
     **Developer mode** (top right), click **Load unpacked** and choose this folder (`extensions/browser`).
   - **Microsoft Edge**: the same from `edge://extensions` (**Developer mode** is on the left).
   - **Opera** (and Opera GX): go to `opera://extensions`, turn on **Developer mode** (top right), click
     **Load unpacked** and choose this folder.
   - **Vivaldi**: go to `vivaldi://extensions`, turn on **Developer mode** (top right), click **Load unpacked** and
     choose this folder.
   - **Firefox**: go to `about:debugging#/runtime/this-firefox`, click **Load Temporary Add-on...** and choose
     `manifest.json` in this folder. Firefox removes temporary add-ons when it closes, so load it again after a
     restart. To keep it, Firefox needs it signed by Mozilla; Firefox ESR, Developer Edition and Nightly can
     instead install it unsigned once `xpinstall.signatures.required` is set to `false` in `about:config`: zip the
     files in this folder (not the folder itself, and without `tests/`) into `arty-browser.xpi` and choose
     **Install Add-on From File...** in `about:addons`.
2. In Arty Desktop, open the tray menu and choose **Chrome sites…**. Click the Arty icon in your
   browser's toolbar (in Firefox and Edge it may be under the puzzle-piece extensions button) and paste the pairing
   code shown there. Until it is paired the icon shows a red **!** and the extension does nothing.
   The same screen lists the sites Arty may use, each with a **Forget** button.
   With version 1.7 and Arty Desktop 1.6.0 or newer this step is usually not needed: see
   [Pairing by itself](#pairing-by-itself).

### Pairing by itself

Version 1.7 asks Arty Desktop for the pairing code through the browser's native messaging, so a browser with the
extension connects without pasting anything. Arty Desktop looks for browsers that have the extension (by its fixed id
in Chrome, Chromium, Edge, Brave, Opera and Vivaldi, by `arty-browser@arty.home` in Firefox) each time it starts, and
for each one writes a small connector (`app.arty.desktop`) into your own user folders only: a manifest in the
browser's `NativeMessagingHosts` folder on Linux, a key under `HKEY_CURRENT_USER` on Windows. No administrator rights
are used. The connector is a short standalone Python script in Arty Desktop's data folder. It answers only the Arty
extension (the browser starts it only for the extension id or add-on id named in its manifest, and the script checks
that again before reading anything), takes one message of at most 4 KB, and replies with the pairing code or with
"unavailable". The extension still checks that Arty Desktop holds the code before it sends it, as in 1.5.

The code is the same one a program running as you could already read from Arty Desktop's data folder, so this does
not let anything new in. As with the Origin check, a copy of the extension loaded unpacked (or as a temporary
add-on in Firefox) with the same id gets the code too: only load the extension from this folder or a release.

Browsers installed as a Snap or Flatpak can't start programs outside their sandbox, so they keep using the pasted
code. The **Browsers** section under **Chrome sites…** in Arty Desktop shows each browser it found, whether it is
connected, and whether it connects by itself, in one line each. A browser that isn't connected but could connect by
itself has a **Connect** button, which turns connecting by itself on and writes its connector again; if the browser
is open it connects within half a minute. The button under the list switches connecting by itself off (which
removes every connector Arty wrote) and on again. On Linux, switch it off there before deleting the Arty AppImage; the Windows
uninstaller removes the connector by itself. Opera and Vivaldi on Windows read Chrome's key, and on Linux the
connector is written into their own config folders: both are not tested yet.

After pulling a newer version, click the reload icon on its card. Version 1.2 has a fixed extension id that Arty
Desktop checks, so when updating from 1.1 remove the old card and choose **Load unpacked** again; Arty Desktop
ignores any other extension. Version 1.3 also needs the pairing code (step 2) once after updating. Version 1.4 adds
scrolling, looking at a tab and Firefox; Arty Desktop needs the matching update for those. Version 1.5 checks that
Arty Desktop holds the pairing code before using it, so it needs Arty Desktop 1.5.1 or newer; with an older Arty
Desktop its icon shows the red **!**. Version 1.6 tells Arty Desktop which browser it is in, so Arty can use a
browser by name; it is now called Arty for your browser, with the same extension id, so updating is a reload. It
needs Arty Desktop 1.5.9 or newer for choosing by name; with an older Arty Desktop it works as 1.5 did. A browser
still on 1.5 or older stays usable but can't be chosen by name, and Arty lists it as one that doesn't say which
browser it is. Version 1.7 adds the `nativeMessaging` permission and pairs by itself (above); the browser asks you to
accept the new permission when it updates, and pasting a code still works.

## Usage

With Arty Desktop open and **HANDS** on, ask for something in your browser - "open the BBC weather page", "what's
on the tab I've got open?", "search YouTube for homelab tours", "scroll down and tell me what's next", "what does
this page look like?", "open the BBC in Firefox", "what's open in Opera?". The browser tools are only offered while the extension has checked in during the last
minute, and only with **HANDS** on.

---

## Tested On

Google Chrome 156.0.8078.47 on Debian 13 (trixie) with KDE Plasma 6.3.6, alongside Arty Desktop 1.0.0. Version 1.4
with Chromium 156.0.8078.79 and Firefox ESR 153.7.0 on Debian 13 under Xvfb, against Arty Desktop's local service
from source. Edge and Brave load the same Chromium manifest but were not tried. Version 1.5 with Chromium 156 on
Debian 13 under Xvfb against Arty Desktop's local service from source and the live brain, on a local test page: each
of the clicks it should ask about (an item number given with another item's words, a submit button labelled Go,
Allow, Add to basket, an authorise link, a download link) showed the Allow / Not now card, and a click on part of a
label was refused. Its page code was also run in headless Google Chrome on local test pages. Version 1.6's browser
naming was checked with the extension's tests (the brands and user agents of Chrome, Edge, Brave, Opera, Opera GX,
Vivaldi, Chromium and Firefox) and in headless Google Chrome 158, whose brands named it Chrome; Opera, Vivaldi, Edge,
Brave and Firefox themselves were not tried.

## License

[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) - see [LICENSE](../../LICENSE) and [NOTICE](../../NOTICE). You can use, change and share Arty,
including commercially; if you share it or anything built from it, keep the LICENSE and NOTICE files, which credit Arty's author, and say what you changed.
