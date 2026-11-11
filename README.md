<div align="center">

<img src="docs/arty-banner.png" alt="Arty Desktop and Arty for iPhone" width="100%">

# Arty

**A self-hosted AI assistant that lives on your own homelab - talk to it from your PC, your phone, Telegram or Discord, and let it work on a computer of its own.**

[![Python](https://img.shields.io/badge/python-3.13-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Ollama](https://img.shields.io/badge/ollama-local%20models-000000?style=flat-square&logo=ollama&logoColor=white)](https://ollama.com/)
[![Kotlin](https://img.shields.io/badge/kotlin-android-7F52FF?style=flat-square&logo=kotlin&logoColor=white)](https://kotlinlang.org/)
[![Electron](https://img.shields.io/badge/electron-desktop-47848F?style=flat-square&logo=electron&logoColor=white)](https://www.electronjs.org/)
[![Proxmox](https://img.shields.io/badge/proxmox-VE%209-E57000?style=flat-square&logo=proxmox&logoColor=white)](https://www.proxmox.com/)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](LICENSE)

</div>

---

## Overview

Arty is a personal assistant that runs entirely on your own hardware - the models, its memory, your chats and its
tools all stay at home. It's calm, practical and a bit dry, and it actually does things rather than explaining how
you could: it checks your servers, reads your email, keeps reminders and deadlines, sends a morning and evening
briefing, searches the web privately, and has its own virtual machine to browse and work on.

The brain runs `qwen3:14b` through Ollama on a 12 GB GPU, and everything else - the desktop app, the phone apps, the
voice assistant, the chat bots - talks to it through one API. Every app asks for your own server's address on first
start, so nothing is tied to anyone else's homelab.

This repository is the brain, with everything that connects to it in its own folder. Each part has its own README
covering how to install it, how to use it, and what it's been run and tested on.

---

## Requirements

- **A Proxmox VE 9 host.** `provision.sh` and `deploy.sh` drive it over SSH with `pct` and `qm`.
- **A Debian 13 LXC container for the brain** with nesting on (SearXNG runs in Docker inside it), about 6 GB of RAM
  and a GPU passed through. Built and tested on an NVIDIA RTX 3060 (12 GB); `qwen3:14b` fills it. A smaller card runs a
  smaller model with fewer tools per request. A bigger card can run a bigger model: any Ollama model that supports tool
  calling works as the main model, and models over 16B are shown every tool at once (`tool_budget_by_size` in
  `brain/brain.json`). A 27B to 32B model at 4-bit, such as `qwen3:32b`, needs roughly 20 GB of VRAM, so a 24 GB card;
  only `qwen3:14b` has been tested.
- **A Debian 13 VM with XFCE for Arty's own PC** - its browser, Gmail window, terminal and document library.
- **SSH from the machine you deploy from** to the Proxmox host as root (the SSH host name in `PROXMOX`, `proxmox` by
  default) and to Arty's PC (`DESKTOP`, `ai@<its address>`).
- Optional: Uptime Kuma, Nextcloud and PufferPanel for the homelab tools, a Cloudflare tunnel for the phone apps away
  from home, and a USB mic and speakers on the host for the voice assistant.

---

## How It Works

```
 Arty Desktop (your PC) ───────LAN─────────┐
 Arty for Android ───LAN or Cloudflare─────┤                       ┌─▶ Ollama (models, GPU)
 Arty for iPhone ────LAN or Cloudflare─────┤                       │
 Arty for Quest (beta) ─▶ voice server ────┼─▶ Brain (:8081) ──────┼─▶ Tool hub (mcpo): memory, web search, homelab, ...
 Voice assistant ("Hey Arty") ─────────────┤                       │
 Telegram / Discord bots ──────────────────┘                       └─▶ Arty's PC (VM): browser, Gmail window, terminal
```

Everything talks to the brain through one API ([docs/API.md](docs/API.md)) with the Arty API key. At home the apps
use the brain's LAN address; away from home the phone apps go through a Cloudflare tunnel behind Cloudflare Access
([docs/cloudflare.md](docs/cloudflare.md)). Notifications - reminders, briefings, task results, approvals, homelab
alerts - only ever go to **Updates** in the phone and desktop apps.

---

## What's In Here

| Folder | What it is |
|---|---|
| `brain/`, `mcp/`, `bin/`, `services/`, `tests/` | The brain - persona, memory, the model and tool loop, tasks, schedules, briefings, the Arty API and its MCP tool servers (this README) |
| [`arty-pc/`](arty-pc/) | Scripts for Arty's own computer - its browser agent, terminal, document reader and desktop look |
| [`apps/desktop/`](apps/desktop/) | Arty Desktop - voice, a small window and hands on your Linux PC, with the AppImage in `release/` |
| [`apps/android/`](apps/android/) | Arty for Android - chat, Updates, tasks, schedules and memory, with the APKs in `release/` |
| [`apps/ios/`](apps/ios/) | Arty for iPhone - a Home Screen web app, served by the brain at `/ios/` |
| [`apps/beta/quest/`](apps/beta/quest/) | **Beta.** Arty for Quest 3 and 3S, your own virtual reality AI - an orb and panels in passthrough, talking through the voice server |
| [**Arty CLI**](https://github.com/drew-codes-things/ArtyCode) | Arty in your terminal on Linux and Windows, its own repository (ArtyCode): reads, edits and runs code in your project, asking first. Runs on your Arty, or on Claude or Gemini with your own key; `arty` starts it |
| [`apps/beta/code/`](apps/beta/code/) | **Beta, being replaced by Arty CLI.** Arty Code - the first terminal app |
| [`voice/`](voice/) | The room mic and speakers hub, and the "Hey Arty" voice assistant |
| [`extensions/gmail/`](extensions/gmail/) | Chrome extension that lets Arty's PC read your Gmail |
| [`extensions/browser/`](extensions/browser/) | Chrome extension that lets Arty Desktop use your own browser |
| [`chat-bots/telegram/`](chat-bots/telegram/), [`chat-bots/discord/`](chat-bots/discord/) | Arty on Telegram and Discord, owner-only |
| [`docs/`](docs/) | [The Arty API](docs/API.md), [linking everything through Cloudflare](docs/cloudflare.md), [connecting a phone](docs/connecting-a-phone.md) and [connecting a Quest headset](docs/connecting-a-headset.md) |

The beta apps are built and unit-tested. Arty for Quest has run on a real headset (it connects and shows its panels) but not every feature has been tried.
Arty CLI's Windows installer has been run on Windows 10 (PowerShell 5.1).

---

## Features

- **Local models** - runs on Ollama on your own GPU; switch models with one command or by asking.
- **Real actions, checked in code** - email, calendar, reminders, deadlines, dates, sums, units, currency and server
  status are handled by code before the model sees them, so the model can't make results up.
- **Honesty check** - a reply that claims it did something when no tool ran (or every tool failed) is replaced with
  what really happened.
- **Briefings** - a morning and evening summary built from your real servers, weather, calendar, reminders,
  deadlines and email, with empty sections left out.
- **Its own PC** - a Debian + XFCE virtual machine with a visible browser, a shared terminal you can watch, a
  searchable `~/library` of documents, and a desktop look it picks itself.
- **Approvals** - anything that sends, buys, deletes or touches the homelab waits for your Approve / Deny.
- **Safe browsing** - logins, CAPTCHAs and bot checks are reported back to you, never got around.
- **Everywhere** - desktop app, Android, iPhone and Quest (beta: your own virtual reality AI), voice, Telegram and Discord, all sharing one memory.
- **Works with [starfire.ai](https://github.com/drew-codes-things/starfire.ai)** - add Arty to starfire as an
  OpenAI-compatible provider and chat with it in the browser, next to your plain Ollama models.
  starfire.ai is getting its own update too: a cleaner look with every control styled in both themes, and
  the layout and features of the big chat apps (chat history in a sidebar, a Stop button, copy, edit and
  regenerate on messages, code blocks with a Copy button, renaming and searching chats, tabbed settings and
  keyboard shortcuts).

---

## Download

Everything to install is on the [Releases](../../releases) page:

| Download | What it is |
|---|---|
| `Arty-1.3.0-x86_64.AppImage` | [Arty Desktop](apps/desktop/README.md) for Linux |
| `Arty-Setup-1.3.0.exe` | [Arty Desktop](apps/desktop/README.md) for Windows 10 and 11 |
| `Arty-1.1.0.apk` | [Arty for Android](apps/android/README.md), which asks for your server on first start |

> **On Linux, Arty Desktop works properly on Debian 13 with KDE Plasma. On Windows it targets Windows 10 and 11, and the installer is not code-signed, so SmartScreen asks you to confirm.**

The iPhone app needs no download: your own brain serves it at `/ios/`. The Quest app is built from source while it's
in beta. The brain itself is installed from this repository (below).

What changed lately: [release notes for 11 November 2026](docs/release-notes-2026-11-11.md).

---

## Setup

Set it up in this order. Steps 1 to 3 happen inside the brain's container (`pct enter <id>` on the host); the rest
run from the machine you cloned this repository to.

### 1. Install the software in the brain's container

```bash
apt update
apt install -y python3 python3-venv python3-requests pipx nodejs npm jq curl openssl docker.io
curl -fsSL https://ollama.com/install.sh | sh
```

Point Ollama at its own folder and keep models loaded, in `/etc/systemd/system/ollama.service.d/arty.conf`:

```ini
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MODELS=/srv/ai/ollama"
Environment="OLLAMA_KEEP_ALIVE=-1"
```

```bash
mkdir -p /srv/ai/ollama && chown ollama:ollama /srv/ai/ollama
systemctl daemon-reload && systemctl restart ollama
ollama pull qwen3:14b
ollama pull qwen2.5vl:7b
```

The tool hub and its MCP servers:

```bash
export PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin
pipx install mcpo && pipx inject mcpo "mcp<2" "fastapi==0.116.1"
pipx install mcp-server-fetch
pipx install mcp-server-time
npm install -g @modelcontextprotocol/server-memory @modelcontextprotocol/server-sequential-thinking
python3 -m venv /opt/ai-mcp && /opt/ai-mcp/bin/pip install "mcp==1.30.0" cryptography
```

mcpo needs `mcp<2` and `fastapi==0.116.1`; the fetch server's page reader needs Node 20 or newer.

### 2. Folders and keys

```bash
mkdir -p /srv/ai/brain/state /srv/ai/mcp /srv/ai/memory/conversations
install -d -m 700 /srv/ai/secrets
( umask 077
  printf 'MCPO_API_KEY=%s\n' "$(openssl rand -hex 24)" > /srv/ai/secrets/mcpo.env
  printf 'BRAIN_API_KEY=%s\n' "$(openssl rand -hex 24)" > /srv/ai/secrets/brain.env )
```

`provision.sh` adds the Arty API key (the one the apps use) and the alert token to `brain.env`, and writes them to
`/srv/ai/secrets/arty-api.txt`. Nothing in `/srv/ai/secrets` is ever printed.

### 3. Make it yours

Before the first deploy, in this repository:

| File | Change |
|---|---|
| `brain/brain.json` | `user_name` (what Arty calls you in its notes; it always talks to you as "you"), `weather` (your town and its latitude and longitude), `expiry_watch` (your domains and the sites whose certificates to watch) |
| `brain/persona.md` | Who Arty is and how it talks. Add a paragraph about yourself and one or two things you've written so it can match your style |
| `mcp/mcpo.json` | The time zone in `--local-timezone` |

Then put your server addresses in the files listed in [Your Own Servers](#your-own-servers).

### 4. Provision

Nothing about your network is built in. Copy `hosts.env.example` to `hosts.env` and put your own addresses in it
(it is read by `provision.sh` and `deploy.sh` and is never committed). The addresses in this guide are examples from
the range reserved for documentation, 192.0.2.0/24: replace them with yours. Then, from this repository:

```bash
cp hosts.env.example hosts.env      # then edit it
CT=104 DESKTOP_VMID=105 TIMEZONE=Europe/London \
HEAL_GUESTS="" NEXTCLOUD_CT="" SET_BACKUP_JOBS=no \
./provision.sh
```

You can also pass `AI_ADDRESS`, `DESKTOP_ADDRESS`, `HOST_ADDRESS` and `NETWORK` on the command line instead. The
scripts stop and say which one is missing.

It's safe to re-run. It installs the tools on Arty's PC, makes a Proxmox user that can read status everywhere and
snapshot only Arty's PC, starts SearXNG and the HTTPS proxy, sets up the host's daily facts and security watch, picks
the GPU at every boot, installs the speech-to-text tools and makes the Arty API key.

| Setting | What it does | Leave unset to |
|---|---|---|
| `CT`, `DESKTOP_VMID` | The brain's container and Arty's PC | use 104 and 105 |
| `AI_ADDRESS`, `DESKTOP_ADDRESS` | Their LAN addresses (required) | stop and ask for them |
| `HOST_ADDRESS`, `NETWORK` | The Proxmox host and your LAN, for the security watch (required) | stop and ask for them |
| `HEAL_GUESTS` | Guests Arty may start or restart after you tap Fix | use 100-103 and Arty's PC; `""` gives none |
| `NEXTCLOUD_CT` | The container running Nextcloud in Docker, for Arty's own account | use 100; `""` skips it |
| `SET_BACKUP_JOBS` | Rewrite the vzdump jobs to back up the brain and Arty's PC | rewrite them; `no` leaves your jobs alone |
| `TIMEZONE` | The container's time zone | use Europe/London |

### 5. Deploy

```bash
CT=104 ARTY_DISCORD_ID= ARTY_TELEGRAM_ID= ./deploy.sh
```

It copies the brain, the tool servers, the iPhone app and the services to the container, the scripts to Arty's PC,
and restarts everything. It keeps whichever main model is live. Leave `ARTY_DISCORD_ID` and `ARTY_TELEGRAM_ID` empty
unless your bots run in PufferPanel. Run it again after every change.

Check it's up:

```bash
pct exec 104 -- systemctl is-active ollama mcpo brain
pct exec 104 -- cat /srv/ai/secrets/arty-api.txt
```

### 6. Arty's PC

Follow [arty-pc/README.md](arty-pc/README.md) to sign Arty's browser in to the sites it should use, then install the
[Gmail extension](extensions/gmail/README.md) in it.

### 7. Your devices

| App | First start |
|---|---|
| [Arty Desktop](apps/desktop/README.md) | Asks for the brain's address (`http://<AI_ADDRESS>:8081/v1`) and the API key, and optionally the Cloudflare address and Access token. **Connect to another Arty…** in the tray menu changes them |
| [Arty for Android](apps/android/README.md) | Install `Arty-1.1.0.apk`; it asks for the same details and keeps them encrypted on the phone |
| [Arty for iPhone](apps/ios/README.md) | Open `https://<your address>/ios/` in Safari and add it to the Home Screen; it asks for the API key. It also approves new computers, and More > **Approved devices** lists and turns them off |
| [Arty for Quest](apps/beta/quest/README.md) (beta) | Asks for the voice server's headset address and key |
| [Arty CLI](https://github.com/drew-codes-things/ArtyCode) | `./install.sh` (Debian) or `install.ps1` (Windows) from the ArtyCode repository, then `arty login`: an Arty API key, approving the computer in Arty for iPhone, or a Claude or Gemini key |
| [Arty Code](apps/beta/code/README.md) (beta, being replaced) | `pipx install ./apps/beta/code`, then `arty-code setup` asks for the same details as Arty Desktop |
| [Arty for Chrome](extensions/browser/README.md) | Lets Arty Desktop use your own browser |

Away from home, the phone apps need [the Cloudflare link](docs/cloudflare.md). To set a phone up, scan the QR code
from Arty Desktop's tray menu > **Phone setup code…**, or type the details in
([docs/connecting-a-phone.md](docs/connecting-a-phone.md)).

### 8. Optional

- [Voice](voice/README.md): the room mic and speakers hub, "Hey Arty", and the Quest headset socket.
- The [Telegram](chat-bots/telegram/README.md) and [Discord](chat-bots/discord/README.md) bots.
- Homelab tools: put the keys in `/srv/ai/secrets/homelab.env` (Uptime Kuma: `UPTIME_KUMA_API_KEY`, created under
  Settings > API Keys) and run `systemctl restart mcpo brain`.

---

## Your Own Servers

Every address is a setting and none is built in. The brain's own links, the chat bots and the voice assistant assume
everything runs on the same machine (127.0.0.1) until you tell them otherwise, and a homelab service whose address
is not set (Proxmox, Uptime Kuma, Nextcloud, PufferPanel, Arty's PC) simply says it is not set up. Set the ones you use:

| Setting | Where | Used for |
|---|---|---|
| `BRAIN_PUBLIC_URL` | `/srv/ai/secrets/brain.env` | The brain's own address in links it hands out |
| `BRAIN_FILES_URL` | `/srv/ai/secrets/brain.env` | The HTTPS address that serves files Arty makes for you |
| `WORKSPACE_HOST` | `/srv/ai/secrets/workspace.env` (its own file: tool servers start with a minimal environment) | Arty's PC |
| `PVE_URL` | `/srv/ai/secrets/homelab.env` | The Proxmox API |
| `UPTIME_KUMA_URL`, `UPTIME_KUMA_API_KEY` | `/srv/ai/secrets/homelab.env` | Server status |
| `NEXTCLOUD_URL` | `/srv/ai/secrets/homelab.env` | Nextcloud |
| `PUFFERPANEL_URL`, `PUFFERPANEL_CLIENT_ID`, `PUFFERPANEL_CLIENT_SECRET` | `/srv/ai/secrets/homelab.env` | Game servers |
| `CALENDAR_TIMEZONE` | `/srv/ai/secrets/brain.env` | Your calendar's time zone |
| `BRAIN_TRUSTED_PROXIES` | `/srv/ai/secrets/brain.env` | Comma-separated addresses of the proxy in front of the brain (the Cloudflare tunnel's machine), the only ones whose `Cf-Connecting-Ip` is believed |
| `BRAIN_URL`, `HUB_URL` | the voice assistant's environment | The brain and the voice hub ([voice/](voice/README.md)) |
| `BRAIN_URL` | each chat bot's environment | The brain ([chat-bots/](chat-bots/)) |

Restart with `systemctl restart mcpo brain` after changing them.

---

## Using It

- **Chat**: any of the apps, or your own code through [the Arty API](docs/API.md).
- **In the browser**: [starfire.ai](https://github.com/drew-codes-things/starfire.ai) works with Arty. In starfire,
  Settings → Connections → "Add API provider" → "Other (OpenAI-compatible)", with the brain's address
  (`http://<AI_ADDRESS>:8081/v1`) and `ARTY_API_KEY`, then pick `assistant-core` (Arty's main model).
- **Models**: on the brain's container, `ai-pull <model>` downloads one (no arguments: suggestions), `ai-models`
  lists them and `ai-use <model>` switches Arty's main model. Or tell Arty "switch to <model>" in any app; only models
  in `arty_models` in `brain.json` are accepted. Only models that can use tools can run Arty, unless you run
  `ai-toolless on`. Hugging Face GGUFs work too: `ai-pull hf.co/<user>/<repo>:Q4_K_M`.
- **Models on their own**: anything listed in `solo_models` in `brain.json` shows up in the apps' model pickers as a
  plain chat, with no Arty persona, tools or memory.
- **Approvals**: in chat Arty asks and acts when you say yes; for background tasks, Updates in the phone and desktop
  apps shows Approve and Deny. The rules are under `approval` in `brain.json`.
- **Documents**: put files in `~/library` on Arty's PC (or ask Arty to) and `search_library` finds them by meaning.
- **Changing how it behaves**: personality in `brain/persona.md`, instructions in `brain/system-prompt.md`, settings
  in `brain/brain.json` (in `/srv/ai/brain` on the container). Changes apply on the next message.
- **Where things live**: memory in `/srv/ai/memory/memory.json`, past chats in `/srv/ai/memory/conversations/`,
  tasks and schedules in `/srv/ai/brain/state/arty.db`.

### Safety

- The Proxmox token reads status everywhere and can only snapshot Arty's PC. Background tasks snapshot the PC before
  using it (the newest 3 are kept; roll back in Proxmox > the VM > Snapshots).
- Pages with logins, CAPTCHAs or bot checks are reported as blocked and Arty asks you instead of getting around them.
- Secrets live in `/srv/ai/secrets` (root-only) and are never printed by the scripts.

---

## Limitations

Know these before relying on it:

- **It can still be wrong** - outside the things handled in code, it can misunderstand a request, pick the wrong
  tool or state something false with confidence. Check anything important, and read commands before running them.
- **Speed** - `qwen3:14b` writes about 33 tokens a second on an RTX 3060. Replies usually start within a few
  seconds, but the first message after the model has unloaded, or one that needs a new group of tools, can take
  20-50 seconds.
- **Short memory within a chat** - 12,288 tokens of context, so long chats are trimmed. Older chats and saved facts
  are recalled by search, and it can miss details.
- **One model on the GPU at a time** - looking at an image swaps the vision model in for that one look, then the main
  model loads back in the background (about a minute in all).
- **Limited actions** - email is read-only, through a signed-in Gmail window; it can't send it. It can't create VMs
  or change the homelab on its own, and installing apps on its own PC isn't built yet.
- **Home network for most things** - away from home only the phone apps work (through Cloudflare). Arty Desktop,
  voice and the chat bots need the LAN, and nothing works while the brain's container is off. There's no cloud
  fallback.
- **One person** - it's built for a single owner; the bots and apps don't do multiple users.

---

## Tested On

| Part | Ran and tested on |
|---|---|
| Brain | Proxmox VE 9.2.21 (kernel 7.0.14-25-pve), LXC container on Debian 13 (trixie), Python 3.13.5, Ollama 0.40.11 with `qwen3:14b` on an NVIDIA RTX 3060 (12 GB, CUDA), mcpo 0.0.20 with mcp 1.30.0, SearXNG |
| Everything else | See each part's own README |

## Test Results

### Speed on the RTX 3060 (12 GB)

| Model | Context | Writing speed | Placement |
|---|---|---|---|
| qwen2.5:3b | - | about 120 tok/s | 100% GPU |
| qwen2.5:7b | 16k | 65 tok/s | 100% GPU, with qwen2.5vl:7b loaded too |
| qwen3:14b | 8k | 33 tok/s | 100% GPU (10 GB) |
| qwen3:14b | 12k | 33 tok/s | 100% GPU (11 GB) |
| qwen3:14b | 16k | 22 tok/s | 9% spills onto the CPU (12 GB) |

### qwen2.5:7b and qwen3:14b, same prompts

| Test | qwen2.5:7b | qwen3:14b |
|---|---|---|
| Apples puzzle | Muddled: said "10 apples", then worked out 7 | 7, clean working |
| Film end time (19:50 + 2h47) | Long, cut off before answering | 22:37, correct |
| "How's my server?" with a tool available | Said it would check, but never called the tool | Called the tool straight away |
| Code one-liner | Fine but wordy | Fine and tight |
| Thinking mode on | n/a | 7, in 11 s |

### qwen3:14b weak spots, and what fixed them

A full run of 17 test questions passed 16; everything left over passed when re-tested after these fixes.

| Weak spot | Before | After |
|---|---|---|
| Dates ("days until Christmas", "what day is 14 Feb 2027", Easter, "90 days from today") | 25-80 s, sometimes wrong | Under 2 s, exact: worked out in code first |
| Currency | Web search, no number | ECB exchange rate, about 4 s |
| Units (F to C, miles to km, pints) | Grabbed the time-zone tool | `convert_units` |
| Sums and percentages | Did them in its head | `calculate` |
| Clock maths (film end times) | OK | `clock_maths` for exactness |
| "Write me a command" | Ran it instead of writing it | Gives just the code; "...and run it" runs it |
| All 65 tools offered at once | About 10,500 tokens of tool descriptions in a 12,288-token context | Capped at 40 tools per message, about 5,000 tokens |

### The honesty check, judged by qwen3:14b

| Test | Result |
|---|---|
| "All sorted, the living room lights are on now" with no tool run | Corrected to say it didn't turn them on |
| Same, in Spanish ("Tu recordatorio está listo para las 18:00") | Corrected, in Spanish |
| "Kuma is back up and running" after the restart tool failed | Corrected, giving the permission error |
| A desktop description after `look_at_desktop` | Left alone |
| "I've opened the page" after the browser really opened it | Wrongly flagged, so the check now only runs when no action tool worked or one failed |
| A plain answer ("Paris is the capital of France") | Left alone, in 0.4 s |

### Running the tests

```bash
cd brain && python3 -m unittest discover -s tests
python3 -m pytest -q mcp voice/tests tests arty-pc chat-bots   # needs pytest, mcp, aiohttp, bcrypt, numpy, webrtcvad-wheels
(cd downloads && python3 -m unittest discover -p 'test_*.py')
cd apps/ios && node --test tests/
```

Each app's README has its own.

---

## License

[PolyForm Strict 1.0.0](https://polyformproject.org/licenses/strict/1.0.0) - see [LICENSE](LICENSE). You can
download, keep and use Arty for yourself or any other noncommercial purpose; sharing it, changing it, building on it or
using it commercially needs permission.

To ask for permission, open an issue on this repository.

The parts of Arty Desktop adapted from MIT-licensed projects stay under MIT, see
[apps/desktop/NOTICE](apps/desktop/NOTICE).

Made by [Drew](https://github.com/drew-codes-things)
