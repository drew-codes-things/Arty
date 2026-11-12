<div align="center">

# Arty for iPhone

**Arty on your iPhone or iPad as a Home Screen app - chat with any of Arty's models, Updates, tasks, calendar, schedules, lectures and memory, from home or anywhere.**

[![JavaScript](https://img.shields.io/badge/javascript-no%20build-F7DF1E?style=flat-square&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
![iOS](https://img.shields.io/badge/iOS-15.4%2B-000000?style=flat-square&logo=apple&logoColor=white)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../../LICENSE)

</div>

---

## Overview

Apple only lets iPhone apps be built and signed on a Mac, so Arty for iPhone is a web app you add to the Home Screen
instead. It opens full screen with its own icon, like any other app. The brain serves it at `/ios/` (the
`web_app.py` module), so it comes from the same place as the API and needs no other hosting, no Mac and no App
Store.

| Screen | What it does |
|---|---|
| Chat | Chats kept on the phone, a model picker (Arty's models and the solo models), attachments, Arty's files and pictures, read aloud |
| Updates | Reminders, briefings, task results and alerts as they happen, with Approve / Deny |
| Tasks | Background tasks: start one, approve, deny, mark done or cancel |
| Calendar | The next 30 days from Arty and Google; add events in plain words, remove Arty's own |
| More | Schedules, lectures (record or add a recording; read the notes), memory search, read-aloud, the keys, **Approved devices** (every computer you approved, with a two-tap **Turn off** and a **Can control Arty CLI** switch), and **Computers** (follow Arty CLI on a computer that ran `/remote`: its transcript, Allow / Deny for its questions, a message box and Stop) |
| Approve a computer | Opened by the link a computer shows when it asks to join (`/ios/?authorise=CODE`): type the code it shows, then Approve or Deny. A failed approve has **Try again**. In a browser with no Arty key it shows the code with a link and a QR code to starfire's Arty devices page (when the brain has `BRAIN_STARFIRE_URL`), or **Paste an API key instead** |

It talks to the brain with the same API as the Android app ([docs/API.md](../../docs/API.md)), as client `ios`.

## Install on the iPhone

1. In **Safari**, open `https://arty.<your domain>/ios/`.
2. Tap **Share**, then **Add to Home Screen**, then **Add**.
3. Open **Arty** from the Home Screen and tap **Scan QR code**, then point it at the code from Arty Desktop (tray
   menu > **Phone setup code…**). Or tap **Enter details manually** and type the app key (`ARTY_API_KEY`) and, for
   use away from home, the Cloudflare Access id and secret. Arty checks them before saving. Where each one comes
   from: [docs/connecting-a-phone.md](../../docs/connecting-a-phone.md).

The keys are kept on the iPhone, in the Home Screen app's own storage, and are only sent to Arty. **More > Forget
the keys on this iPhone** removes them; the chats stay. The page itself holds no keys, which is why `/ios/` can be
open to anyone while every API call still needs both keys (see [docs/cloudflare.md](../../docs/cloudflare.md)).

## How It Differs From Android

- **Talking:** tap the microphone on the iPhone keyboard to dictate. Replies can be read aloud (More > Read Arty's
  replies aloud), using the iPhone's own voices.
- **Updates arrive while the app is open.** There are no notifications when it's closed: that needs Web Push, which
  the brain doesn't send yet.
- **Calls and texts:** a web app can't read contacts, so asking Arty to call or text someone gets a pointer to the
  phone app instead.
- **Lecture recording** only runs while Arty is open with the screen on. For long lessons, record in Voice Memos and
  use **Add a recording**.
- No widget, no call screen and no battery note.

## Files

| Path | What it is |
|---|---|
| `index.html`, `app.css` | The page and its look (Arty's colours, IBM Plex fonts from the Android app) |
| `js/api.js` | The brain API: keys on every request, chat jobs with polling, uploads, downloads, announcements |
| `js/chat.js`, `js/conversations.js` | Chat screen and the chats saved on the phone (same history window as Android) |
| `js/manage.js`, `js/updates.js`, `js/tasks.js` | Tasks, calendar, schedules, lectures, memory, approved devices and Updates |
| `js/authorise.js` | The "approve this computer" sheet, and the starfire link and QR code when this browser has no key |
| `js/qr.js`, `js/vendor/qrcode-generator.js` | Draws QR codes as SVG (qrcode-generator 2.0.4, MIT, see `js/vendor/LICENSE-qrcode-generator.txt`) |
| `js/setup.js`, `js/setupcode.js`, `js/scanner.js` | First start: scan the setup QR code or type the keys |
| `js/markdown.js`, `js/links.js` | Safe markdown (everything escaped first) and Arty file links |
| `sw.js`, `manifest.webmanifest`, `icons/` | Home Screen install and opening without a connection |
| `tests/` | Unit tests and `fake_brain.py`, a stand-in brain for trying the app on a laptop |

## Tests

```
cd apps/ios
node --test tests/
```

To click through it on a laptop, run `python3 tests/fake_brain.py 8099`, open `http://127.0.0.1:8099/ios/` and
enter the test key `fake-brain-test-key` (it is in `fake_brain.py`). The brain's side is tested with
`cd brain && python3 -m unittest tests.test_web_app`.
