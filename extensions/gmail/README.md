<div align="center">

# Arty Gmail Reader

**A small Chrome extension that lets Arty read your Gmail through an ordinary, signed-in Chrome window on its own PC - no app passwords, no IMAP, and nothing it can send or delete.**

[![Chrome](https://img.shields.io/badge/chrome-manifest%20v3-4285F4?style=flat-square&logo=googlechrome&logoColor=white)](https://developer.chrome.com/docs/extensions)
[![JavaScript](https://img.shields.io/badge/javascript-F7DF1E?style=flat-square&logo=javascript&logoColor=black)](https://developer.mozilla.org/en-US/docs/Web/JavaScript)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](../../LICENSE)

</div>

---

## Overview

Google blocks automated browsers from signing in, and Arty isn't given an app password or IMAP access, so this is
how it reads email: a normal Google Chrome window on Arty's PC (its desktop VM), signed in by you, with this extension
inside it.

---

## How It Works

```
You: "any new emails?"
        |
Brain  ->  Arty's PC browser agent (127.0.0.1:9334, that computer only)
        |
This extension (long-polling the agent)  ->  runs the Gmail search in the signed-in window
        |
Reads the results list back  ->  Arty sorts what matters and answers
```

It only reads: it can open a search and read the results, but can't send, delete, archive or change anything.

The agent only answers requests that carry its key. On every start it copies the key into this folder as `agent-token.json` (readable only by its owner); the extension reads it from its own files, which web pages can't do, and sends it with each request.

---

## Setup

`./deploy.sh` copies this folder to `~/.local/browser-agent/gmail-extension` on Arty's desktop VM. Then, once:

1. On Arty's desktop, double-click **Sign in to Google** and sign in to Gmail in that window.
2. In the same window, go to `chrome://extensions` and turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose `~/.local/browser-agent/gmail-extension`. If the file picker's Open button is
   off the screen, maximise the picker first.

Chrome remembers the extension and runs it from that folder (it ignores `--load-extension`, so this has to be done
by hand once). After a deploy that changes it, click the reload icon on its card in `chrome://extensions`, or close
and reopen the window.

## Usage

Ask Arty "any new emails?", "check my email" or "find the email from ...". The window has to stay open and signed
in - if it isn't, Arty says it couldn't check your email and asks you to sign in again. The morning briefing lists
your important unread emails the same way.

---

## Tested On

Google Chrome 156.0.8078.47 on Arty's desktop VM (Debian 13 trixie, XFCE 4.20), profile `~/.gmail-chrome`. The sign-in
survives restarts of Chrome and of the VM.

## License

[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) - see [LICENSE](../../LICENSE) and [NOTICE](../../NOTICE). You can use, change and share Arty,
including commercially; if you share it or anything built from it, keep the LICENSE and NOTICE files, which credit Arty's author, and say what you changed.
