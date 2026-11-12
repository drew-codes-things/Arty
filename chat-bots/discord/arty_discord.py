"""Arty on Discord, for its owner only.

- /arty works anywhere once the app is added to the owner's account (User Install): any server,
  group chat or DM, even ones the bot isn't in. A "private" option makes the reply visible only to him.
- Right-click a message > Apps > "Ask Arty" to have Arty reply to or explain it (private).
- DMs to the bot, and @mentions in servers where the bot itself was added.
Anyone else gets a short private refusal (slash command and menu) or is ignored (messages);
their text never reaches Arty. Each channel keeps a short rolling history so follow-ups,
including saying "yes" to an approval, work.

Runs as a PufferPanel server on the home server: settings (bot token, owner, Arty API key) come from
config.env in its folder, edited in PufferPanel's file manager, and discord.py lives in ./deps.
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "deps"))


def load_config(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_config(HERE / "config.env")

import discord  # noqa: E402  (after the deps path and config are set up)
from discord import app_commands  # noqa: E402

if not os.environ.get("DISCORD_BOT_TOKEN"):
    # Exit cleanly (code 0) so PufferPanel's crash recovery doesn't restart it in a loop.
    print("Put the bot token in config.env (DISCORD_BOT_TOKEN=...) using PufferPanel's Files tab, then start again.")
    sys.exit(0)

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
OWNER_ID = int(os.environ["DISCORD_OWNER_ID"])
API_KEY = os.environ["ARTY_API_KEY"]
BRAIN_URL = os.environ.get("BRAIN_URL", "http://127.0.0.1:8081/v1/chat/completions")
BRAIN_BASE = BRAIN_URL.split("/v1/", 1)[0]
CLIENT_NAME = "discord"
DOWNLOAD_LINK = re.compile(r"!?\[([^\]]*)\]\((https?://[^)\s]+?(/arty-files/[^)\s]+))\)")
DISCORD_FILE_BYTES = 10 * 1024 * 1024
DISCORD_FILES_PER_MESSAGE = 10
AVATAR_FILE = HERE / "avatar.png"
AVATAR_STAMP = HERE / ".avatar.sha256"
HISTORY_MESSAGES = 8  # sent with each message: 8 to 12 (see trimmed)
HISTORY_STEP_MESSAGES = 4
DISCORD_LIMIT = 2000
BRAIN_TIMEOUT_SECONDS = 900
NOT_YOURS = "Arty is a personal assistant and only works for its owner."
DISCORD_NOTE = (
    "You are replying on Discord. Keep replies short (under 1800 characters unless asked for more), "
    "use Discord markdown, and no tables."
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arty-discord")
histories: dict[str, list] = defaultdict(list)


def trimmed(history: list) -> list:
    """Cuts the conversation back to HISTORY_MESSAGES in one go once it's grown by HISTORY_STEP_MESSAGES.
    Its start then stays put for a few messages, so the brain's cached prompt stays valid; dropping the
    oldest message every time would make the model re-read the whole conversation on every message."""
    return history[-HISTORY_MESSAGES:] if len(history) > HISTORY_MESSAGES + HISTORY_STEP_MESSAGES else history


def ask_brain(messages: list[dict]) -> str:
    body = json.dumps({"model": "Arty", "messages": messages, "client": CLIENT_NAME}).encode()
    request = urllib.request.Request(BRAIN_URL, data=body, headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(request, timeout=BRAIN_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())["choices"][0]["message"]["content"].strip() or "(no reply)"


async def answer(channel_key: str, prompt: str) -> str:
    history = histories[channel_key]
    messages = [{"role": "system", "content": DISCORD_NOTE}, *history, {"role": "user", "content": prompt}]
    try:
        reply = await asyncio.to_thread(ask_brain, messages)
    except (OSError, KeyError, ValueError) as error:  # OSError covers URLError, timeouts and dropped connections
        log.error("brain request failed: %s", error)
        return f"I couldn't reach my brain just now ({error}). The AI server may be switched off."
    histories[channel_key] = trimmed(history + [{"role": "user", "content": prompt}, {"role": "assistant", "content": reply}])
    return reply


def fetch_made_files(reply: str) -> tuple[str, list[tuple[str, bytes]]]:
    files = []
    for link in DOWNLOAD_LINK.finditer(reply):
        name = urllib.parse.unquote(link.group(1) or "file")
        try:
            with urllib.request.urlopen(f"{BRAIN_BASE}{link.group(3)}", timeout=120) as response:
                data = response.read(DISCORD_FILE_BYTES + 1)
        except OSError as error:
            log.error("couldn't fetch %s: %s", name, error)
            continue
        if len(data) > DISCORD_FILE_BYTES or len(files) >= DISCORD_FILES_PER_MESSAGE:
            reply = reply.replace(link.group(0), f"{name} (too big to send on Discord)")
            continue
        files.append((name, data))
        reply = reply.replace(link.group(0), f"{name} (attached)")
    return reply, files


async def reply_parts(reply: str) -> tuple[list[str], list[discord.File]]:
    text, files = await asyncio.to_thread(fetch_made_files, reply)
    return chunks(text) or ["(attached)"], [discord.File(io.BytesIO(data), filename=name) for name, data in files]


def chunks(text: str) -> list[str]:
    """Split a reply into Discord-sized messages, preferring line breaks."""
    parts = []
    while len(text) > DISCORD_LIMIT:
        cut = text.rfind("\n", 0, DISCORD_LIMIT)
        cut = cut if cut > DISCORD_LIMIT // 2 else DISCORD_LIMIT
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts + [text] if text else parts


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())
tree = app_commands.CommandTree(client)


@tree.command(name="arty", description="Ask Arty")
@app_commands.describe(prompt="What you want Arty to do", private="Only you can see the reply")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def arty(interaction: discord.Interaction, prompt: str, private: bool = False):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=private, thinking=True)
    reply = await answer(f"channel-{interaction.channel_id}", prompt)
    parts, files = await reply_parts(reply)
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        await interaction.followup.send(part, ephemeral=private, **({"files": files} if last and files else {}))


@tree.context_menu(name="Ask Arty")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def ask_about_message(interaction: discord.Interaction, message: discord.Message):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(NOT_YOURS, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    prompt = (f'A Discord message from {message.author.display_name}:\n"{message.content[:3000]}"\n\n'
              "Explain it or draft a reply to it, whichever is more useful.")
    reply = await answer(f"menu-{interaction.channel_id}", prompt)
    for part in chunks(reply):
        await interaction.followup.send(part, ephemeral=True)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.author.id != OWNER_ID:
        return
    is_dm = isinstance(message.channel, discord.DMChannel)
    if not is_dm and client.user not in message.mentions:
        return
    prompt = re.sub(rf"<@!?{client.user.id}>", "", message.content).strip()
    if not prompt:
        return
    async with message.channel.typing():
        reply = await answer(f"channel-{message.channel.id}", prompt)
    parts, files = await reply_parts(reply)
    await message.reply(parts[0], mention_author=False, **({"files": files} if len(parts) == 1 and files else {}))
    for index, part in enumerate(parts[1:], start=2):
        await message.channel.send(part, **({"files": files} if index == len(parts) and files else {}))


async def update_avatar():
    """Upload the avatar only when the file changes; Discord rate-limits avatar edits."""
    if not AVATAR_FILE.exists():
        return
    image = AVATAR_FILE.read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if AVATAR_STAMP.exists() and AVATAR_STAMP.read_text().strip() == digest:
        return
    try:
        await client.user.edit(avatar=image)
        AVATAR_STAMP.write_text(digest)
        log.info("avatar updated from %s", AVATAR_FILE)
    except discord.HTTPException as error:
        log.error("avatar update failed: %s", error)


@client.event
async def on_ready():
    synced = await tree.sync()
    log.info("logged in as %s; %d commands synced; owner %s", client.user, len(synced), OWNER_ID)
    await update_avatar()


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
