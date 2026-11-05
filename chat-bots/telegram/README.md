<div align="center">

# Arty on Telegram

**Chat with Arty from Telegram - text, photos, voice notes and files - for its owner only, with nothing but the Python standard library.**

[![Python](https://img.shields.io/badge/python-3.13-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/telegram-bot%20api-26A5E4?style=flat-square&logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../../LICENSE)

</div>

---

## Overview

A Telegram bot that passes your messages to Arty's brain and sends back its replies. The Bot API is plain HTTPS,
read with long polling, so there are no dependencies to install. It talks to the brain over the home network, so it
only answers while the brain is on. Notifications don't come here - they go to Updates in the phone and desktop apps.

---

## Features

- **Private chat** - every message goes to Arty.
- **Groups** - `/arty <text>`, an @mention, or a reply to one of Arty's messages.
- **Attachments** - photos, voice notes, audio, video and files go to Arty; files it makes come back as Telegram
  files.
- **Fresh start** - `/new` forgets the current conversation in that chat.
- **Owner only** - after pairing, everyone else is ignored and their messages never reach Arty.

---

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Make a PufferPanel server on core-sys from Docker's `python:3.13-slim` image that runs `python -u arty_telegram.py`.
3. In its folder, create `config.env` (PufferPanel > Files):

   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_PAIR_CODE=<any long random code>
   BRAIN_URL=http://192.168.1.13:8081/v1/chat/completions
   ARTY_API_KEY=...
   ```

   `BRAIN_URL` is optional (that's the default). `ARTY_API_KEY` is the brain's Arty API key.

4. Set `ARTY_TELEGRAM_ID` in `deploy.sh` to the server's id and run `./deploy.sh`, which copies `arty_telegram.py`
   there. Start the server.
5. Pair it: send the bot `/pair <code>` with the code from `config.env`. Your user id is saved to `owner.txt`.

After a deploy, restart the server in PufferPanel to run the new code.

---

## Tested On

PufferPanel on core-sys (CT 100, Debian 12 bookworm), in Docker's `python:3.13-slim` image, with the brain on the
homelab. Used from the Telegram app on Android 16 (Realme RMX5085).

## License

[PolyForm Strict 1.0.0](https://polyformproject.org/licenses/strict/1.0.0) - see [LICENSE](../../LICENSE). You can download, keep and use Arty for yourself or any other
noncommercial purpose; sharing it, changing it, building on it or using it commercially needs permission.
