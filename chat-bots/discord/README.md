<div align="center">

# Arty on Discord

**Ask Arty anything from Discord - with `/arty` in any server, group chat or DM, or by right-clicking a message - for its owner only.**

[![Python](https://img.shields.io/badge/python-3.13-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7-5865F2?style=flat-square&logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../../LICENSE)

</div>

---

## Overview

A Discord bot that passes your messages to Arty's brain and sends back its replies. Anyone else gets a short
private refusal or is ignored, and their text never reaches Arty. It talks to the brain over the home network, so
it only answers while the brain is on. Notifications don't come here - they go to Updates in the phone and desktop
apps.

---

## Features

- **`/arty` anywhere** - once the app is added to your account (User Install), it works in any server, group chat
  or DM, even ones the bot isn't in. The `private` option makes the reply visible only to you.
- **Ask Arty** - right-click a message > Apps > **Ask Arty** to have Arty reply to it or explain it, privately.
- **DMs and mentions** - message the bot directly, or @mention it in servers it's been added to.
- **Follow-ups** - each channel keeps a short history, so follow-ups work, including saying "yes" to an approval.

---

## Setup

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application with a
   bot, copy the bot token, turn on the **Message Content** intent, and allow User Install.
2. Make a PufferPanel server on core-sys from Docker's `python:3.13-slim` image that runs `python -u arty_discord.py`.
   Install its one dependency into `./deps` in that folder: `pip install --target deps -r requirements.txt`.
3. In its folder, create `config.env` (PufferPanel > Files):

   ```
   DISCORD_BOT_TOKEN=...
   DISCORD_OWNER_ID=<your Discord user id>
   BRAIN_URL=http://192.0.2.13:8081/v1/chat/completions
   ARTY_API_KEY=...
   ```

   `BRAIN_URL` is optional (that's the default). `ARTY_API_KEY` is the brain's Arty API key. Put an `avatar.png` there
   to set the bot's picture.

4. Set `ARTY_DISCORD_ID` in `deploy.sh` to the server's id and run `./deploy.sh`, which copies `arty_discord.py`
   there. Start the server, then add the app to your account from the portal's install link.

After a deploy, restart the server in PufferPanel to run the new code.

---

## Tested On

PufferPanel on core-sys (CT 100, Debian 12 bookworm), in Docker's `python:3.13-slim` image with discord.py 2.7.1,
with the brain on the homelab.

## License

[PolyForm Strict 1.0.0](https://polyformproject.org/licenses/strict/1.0.0) - see [LICENSE](../../LICENSE). You can download, keep and use Arty for yourself or any other
noncommercial purpose; sharing it, changing it, building on it or using it commercially needs permission.
