[Arty](../README.md) / [Docs](README.md)

# Setting Up Arty

Everything to install the brain and connect your devices, in order. The short version is in the
[README](../README.md#quick-start).

- [Requirements](#requirements)
- [Setup](#setup): [1. Install the software](#1-install-the-software-in-the-brains-container), [2. Folders and keys](#2-folders-and-keys), [3. Make it yours](#3-make-it-yours), [4. Provision](#4-provision), [5. Deploy](#5-deploy), [6. Arty's PC](#6-artys-pc), [7. Your devices](#7-your-devices), [8. Optional](#8-optional)
- [Your Own Servers](#your-own-servers)

## Requirements

- **A Proxmox VE 9 host.** `provision.sh` and `deploy.sh` drive it over SSH with `pct` and `qm`.
- **A Debian 13 LXC container for the brain** with nesting on (SearXNG runs in Docker inside it), about 6 GB of RAM
  and a GPU passed through. Built and tested on an NVIDIA RTX 3060 (12 GB); `qwen3:14b` fills it. An AMD card is
  handed over too, but that path is untested (see [Provision](#4-provision)). A smaller card runs a
  smaller model with fewer tools per request, but stay at about 7B or above: smaller models are easier to trick
  into the wrong action (see [Limitations](limitations.md)). A bigger card can run a bigger model: any model that supports tool
  calling works as the main model, and models over 16B are shown every tool at once (`tool_budget_by_size` in
  `brain/brain.json`). A 27B to 32B model at 4-bit, such as `qwen3:32b`, needs roughly 20 GB of VRAM, so a 24 GB card;
  only `qwen3:14b` has been tested. Set `vram_budget_mb` in `brain/brain.json` to a little under your card's memory
  (11500, the default, suits 12 GB; about 23000 suits 24 GB): before a picture or a handed-off job loads a second
  model, Arty unloads the others if together they would go over it.
- **A Debian 13 VM with XFCE for Arty's own PC** - its browser, Gmail window, terminal and document library.
- **SSH from the machine you deploy from** to the Proxmox host as root (the SSH host name in `PROXMOX`, `proxmox` by
  default) and to Arty's PC (`DESKTOP`, `ai@<its address>`).
- Optional: Uptime Kuma, Nextcloud and PufferPanel for the homelab tools, a Cloudflare tunnel for the phone apps away
  from home, and a USB mic and speakers on the host for the voice assistant.

---

## Setup

Set it up in this order. Steps 1 to 3 happen inside the brain's container (`pct enter <id>` on the host); the rest
run from the machine you cloned this repository to.

### 1. Install the software in the brain's container

```bash
apt update
apt install -y python3 python3-venv python3-requests pipx nodejs npm jq curl openssl docker.io
```

Install [ArtyEngine](https://github.com/drew-codes-things/ArtyEngine) as its README says, with its models in
`/srv/ai/arty-engine/models`, listening on `127.0.0.1:11438`. Then download the models through it (once `deploy.sh` has
run, `ai-pull <model>` does the same):

```bash
for model in qwen3:14b mimo-v2.6:9b nomic-embed-text; do
  curl -fsS http://127.0.0.1:11438/api/pull -d "{\"model\": \"$model\", \"stream\": false}"
done
```

The tool hub and its MCP servers:

```bash
export PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin
pipx install mcp-server-fetch
pipx install mcp-server-time
npm install -g @modelcontextprotocol/server-memory @modelcontextprotocol/server-sequential-thinking
python3 -m venv /opt/ai-mcp && /opt/ai-mcp/bin/pip install "mcp==1.30.0" cryptography
python3 -m venv /opt/ai-hub && /opt/ai-hub/bin/pip install "mcp==2.3.0"
```

The tool hub (`hub/tool_hub.py`, run from `/opt/ai-hub`) starts every server in `mcp/tool-hub.json` with the official
MCP Python SDK and serves each tool to the brain over HTTP on 127.0.0.1:8000, behind `MCPO_API_KEY`. A server that
dies is started again. Tool servers get only the settings in their `env` plus a basic environment (`PATH`, `HOME`,
`USER`), never the hub's key. The fetch server's page reader needs Node 20 or newer.

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
| `brain/brain.json` | `user_name` (what Arty calls you in its notes; it always talks to you as "you"), `weather` (your town and its latitude and longitude), `expiry_watch` (your domains and the sites whose certificates to watch), `vram_budget_mb` (if your GPU isn't 12 GB: a little under its memory in MB) |
| `brain/persona.md` | Who Arty is and how it talks. Add a paragraph about yourself and one or two things you've written so it can match your style |
| `mcp/tool-hub.json` | The time zone in `--local-timezone` |

Then put your server addresses in the files listed in [Your Own Servers](#your-own-servers).

### 4. Provision

Nothing about your network is built in. Copy `hosts.env.example` to `hosts.env` and put your own addresses in it
(it is read by `provision.sh` and `deploy.sh` and is never committed). The addresses in this guide are examples from
the range reserved for documentation, 192.0.2.0/24: replace them with yours. Then, from this repository:

```bash
cp hosts.env.example hosts.env      # then edit it
CT=104 DESKTOP_VMID=105 TIMEZONE=Europe/London ./provision.sh
```

You can also pass `AI_ADDRESS`, `DESKTOP_ADDRESS`, `HOST_ADDRESS` and `NETWORK` on the command line instead. The
scripts stop and say which one is missing.

It's safe to re-run. It installs the tools on Arty's PC, makes a Proxmox user that can read status everywhere and
snapshot only Arty's PC, starts SearXNG and the HTTPS proxy, sets up the host's daily facts and security watch, picks
the GPU at every boot, installs the speech-to-text tools and makes the Arty API key.

**AMD cards: untested.** No real AMD card has run this path; it is written and tested against a fake host only.
At every boot `arty-gpu-select` gives the container an NVIDIA card when one is fitted, exactly as before. With no
NVIDIA card but an AMD one (a render node of the amdgpu driver), it passes that card's render node and card node, and
`/dev/kfd` when the host has it, with the container's `render` and `video` group ids (992 and 44 unless
`CT_RENDER_GID` and `CT_VIDEO_GID` say otherwise; `pct exec <ct> -- getent group render video` shows them). Set them
in `/etc/default/arty-gpu-select` on the host (lines such as `CT_RENDER_GID=993`), which the boot run, a run by hand
and provisioning's dry run all read; a value in the environment wins over the file, and a value that is not a number
stops the script. `arty-gpu-select --kind` prints `nvidia`, `amd-rocm`, `amd-vulkan` or `none`, and `--gfx-target`
prints the card's gfx target (such as `gfx1100`) from `/sys/class/kfd`. When the card is AMD, provisioning installs,
in the container, the Vulkan loader and Mesa's Vulkan driver (the fallback when ROCm can't use the card), and with
`/dev/kfd` also AMD's ROCm 10.0 libraries with the card's own kernel package (`rocm[libraries,device-<gfx target>]`,
never a default one; with no readable target it skips this and says so) into `/srv/ai/arty-engine/rocm-10.0`,
without keeping pip's cache, then prints the two library folders to list in ArtyEngine's
`runtimes.rocm.library_path`; a failure there is a warning and the rest goes on. ArtyEngine itself still needs its ROCm or Vulkan runtime build and its config
set by hand: its README, AMD GPUs (untested), covers both, and its `tools/amd-check.sh` checks a machine with an AMD
card.

| Setting | What it does | Leave unset to |
|---|---|---|
| `CT`, `DESKTOP_VMID` | The brain's container and Arty's PC | use 104 and 105 |
| `AI_ADDRESS`, `DESKTOP_ADDRESS` | Their LAN addresses (required) | stop and ask for them |
| `HOST_ADDRESS`, `NETWORK` | The Proxmox host and your LAN, for the security watch (required) | stop and ask for them |
| `HEAL_GUESTS` | Guests Arty may start or restart after you tap Fix (ids, space separated) | give it none |
| `NEXTCLOUD_CT` | The container running Nextcloud in Docker, for Arty's own account | skip it |
| `HOST_FACTS_FILE` | The file on the Proxmox host that the brain's container sees as `/srv/ai/state/host-facts.json` (updates waiting and last backups, for the weekly report) | skip the daily host facts |
| `BACKUP_JOB` | The id of a vzdump job in `/etc/pve/jobs.cfg` to add the brain and Arty's PC to. Nothing else in that job, and no other job, is changed | leave every backup job alone |
| `SET_BACKUP_JOBS` | `no` skips the backup step even when `BACKUP_JOB` is set | add them to `BACKUP_JOB` |
| `TIMEZONE` | The container's time zone | use Europe/London |

Any of these can also go in `hosts.env`.

### 5. Deploy

```bash
CT=104 ./deploy.sh
```

It copies the brain, the tool servers, the iPhone app and the services to the container, the scripts to Arty's PC,
and restarts everything. It keeps whichever main model is live. If your chat bots run in PufferPanel, set
`ARTY_DISCORD_ID` and `ARTY_TELEGRAM_ID` (their PufferPanel server ids) and `PUFFER_DIR` (PufferPanel's servers folder
on the Proxmox host) in `hosts.env`; left empty, the bots are skipped. Run it again after every change.

It also installs the docs library (`doclib/` in `/srv/ai/doclib`, the `docs-library` service on port 8889, answering
only the container itself and its Docker network) and a timer that refreshes it every Sunday at 04:30. The library
starts empty: build it once with `pct exec 104 -- systemctl start docs-library-refresh`, which downloads the
documentation and builds the index in `/srv/ai/docs-library` (about 750 MB; it downloads nothing while less than 8 GB
is free). Until then coding questions read the web as before. On a new server `provision.sh` also adds the library to
SearXNG as the `local docs` engine; it leaves an existing `/srv/ai/searxng/settings.yml` alone.

Check it's up:

```bash
pct exec 104 -- systemctl is-active arty-engine tool-hub brain docs-library
pct exec 104 -- cat /srv/ai/secrets/arty-api.txt
```

### 6. Arty's PC

Follow [arty-pc/README.md](../arty-pc/README.md) to sign Arty's browser in to the sites it should use, then install the
[Gmail extension](../extensions/gmail/README.md) in it.

### 7. Your devices

| App | First start |
|---|---|
| [Arty Desktop](../apps/desktop/README.md) | Asks for the brain's address (`http://<AI_ADDRESS>:8081/v1`) and the API key, and optionally the Cloudflare address and Access token. **Connect to another Arty…** in the tray menu changes them |
| [Arty for Android](../apps/android/README.md) | Install `Arty-1.6.0.apk`; it asks for the same details and keeps them encrypted on the phone |
| [Arty for iPhone](../apps/ios/README.md) | Open `https://<your address>/ios/` in Safari and add it to the Home Screen; it asks for the API key. It also approves new computers, and More > **Approved devices** lists and turns them off |
| [Arty for Quest](../apps/beta/quest/README.md) (beta) | Asks for the voice server's headset address and key |
| [Arty CLI](https://github.com/drew-codes-things/ArtyCode) | `./install.sh` (Debian) or `install.ps1` (Windows) from the ArtyCode repository, then `arty login`: an Arty API key, or approving the computer in your browser |
| [Arty Code](../apps/beta/code/README.md) (beta, being replaced) | `pipx install ./apps/beta/code`, then `arty-code setup` asks for the same details as Arty Desktop |
| [Arty for your browser](../extensions/browser/README.md) | Lets Arty Desktop use your own browsers: Chrome, Edge, Brave, Chromium or Firefox. Choosing one by name, and Opera and Vivaldi, need extension 1.6 and Arty Desktop 1.5.9 or newer; pairing without a code needs extension 1.7 and Arty Desktop 1.6.0 |

Away from home, the phone apps need [the Cloudflare link](cloudflare.md). To set a phone up, scan the QR code
from Arty Desktop's tray menu > **Link a phone…**, or type the details in
([connecting-a-phone.md](connecting-a-phone.md)).

### 8. Optional

- [Voice](../voice/README.md): the room mic and speakers hub, "Hey Arty", and the Quest headset socket.
- The [Telegram](../chat-bots/telegram/README.md) and [Discord](../chat-bots/discord/README.md) bots.
- Code checking: make a small Debian container, put its id in `hosts.env` as `RUNNER_CT` (and the brain's as
  `BRAIN_CT` if it isn't 104), and run `code-runner/deploy.sh setup` while it still has internet. Setup ends by putting
  up the firewall (only `AI_ADDRESS` can reach it afterwards; take its gateway away too), and every deploy refuses to
  start the runner without that firewall. Each deploy also makes sure the brain has a `CODE_RUNNER_TOKEN` in
  `/srv/ai/secrets/brain.env` (it creates one the first time, and restarts a running brain whenever the brain runs
  without the saved token) and copies it to the runner's `/etc/code-runner/runner.env`; the runner refuses any check
  without it, and the brain logs a warning at start when `CODE_RUNNER_URL` is set without the token. Set
  `CODE_RUNNER_URL=http://<runner address>:8790` in `/srv/ai/secrets/brain.env` and run `systemctl restart brain`.
  Coding answers then end with a "Sandbox check:" line.
  When an answer shows what its code prints, Arty compares that with what the code really printed in the sandbox and
  fixes the answer if they differ (output that depends on time, randomness or input is left alone).
  A script that reads input or command-line arguments is only fully tested when the question gives an example of that
  input. Without one, the runner runs it with no input and then with a few made-up values, and a stop that only those
  values cause is reported as "couldn't be tested without input" rather than as a pass or a failure. A bug that only
  real input reaches can be missed that way, and now and then a correct script whose library rejects every made-up
  value is failed. Put an example input in the question when you want the whole script checked.
- Homelab tools: put the keys in `/srv/ai/secrets/homelab.env` (Uptime Kuma: `UPTIME_KUMA_API_KEY`, created under
  Settings > API Keys) and run `systemctl restart tool-hub brain`.

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
| `HOMELAB_NAMES` | `/srv/ai/secrets/brain.env` | Comma-separated names of your Proxmox host and guests: a message naming one gets the server status looked up first |
| `BRAIN_TRUSTED_PROXIES` | `/srv/ai/secrets/brain.env` | Comma-separated addresses of the proxy in front of the brain (the Cloudflare tunnel's machine), the only ones whose `Cf-Connecting-Ip` is believed |
| `BRAIN_STARFIRE_URL` | `/srv/ai/secrets/brain.env` | starfire's address (for example `https://<STARFIRE_ADDRESS>`). When a computer asks to be approved in a browser with no Arty key, the approve page links to starfire's Arty devices page with a QR code. Leave it out and the page only offers pasting a key |
| `ROOM_API_KEY`, `ROOM_ADDRESSES` | `/srv/ai/secrets/brain.env` | A separate key for a shared AI chat room app, and the comma-separated addresses it may come from. That key only gets the room chat (see [API.md](API.md#shared-chat-room)); it must differ from every other key or the brain won't start; leave both out to turn it off |
| `BRAIN_URL`, `HUB_URL` | the voice assistant's environment | The brain and the voice hub ([voice/](../voice/README.md)) |
| `BRAIN_URL` | each chat bot's environment | The brain ([chat-bots/](../chat-bots/)) |

Restart with `systemctl restart tool-hub brain` after changing them.
