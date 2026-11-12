# Arty 1.4.0 release notes, 12 November 2026

Everything added and fixed since 1.3.0: Arty CLI remote control, faster replies with a model for each job, new voice front end, memory that recalls what matters, a new tool hub, and a full review round of the brain, the apps and the release builder.

## Changes you'll notice

- **The main model is called Arty.** `/v1/models` lists the main entry as `Arty`. Clients that still send `assistant-core` keep working, and saved chats open under the new name.
- **New per-install settings for `deploy.sh` and `provision.sh`.** `HEAL_GUESTS`, `NEXTCLOUD_CT`, `HOST_FACTS_FILE`, `BACKUP_JOB`, `PUFFER_DIR`, `ARTY_DISCORD_ID` and `ARTY_TELEGRAM_ID` now come from `hosts.env` (see `hosts.env.example`). Each step is skipped while its key is empty. Homelab host names for lookups come from `HOMELAB_NAMES` in the brain's environment.
- **`provision.sh` only edits the backup job you name** in `BACKUP_JOB`, adds the brain and desktop to it, and never prunes or changes other jobs.
- **`vram_budget_mb` in `brain.json`** tells the brain how much GPU memory it may fill before it unloads other models. A bad value falls back to the default with a warning.
- **The tool hub replaces mcpo.** `services/tool-hub.service` runs `hub/tool_hub.py` on the official MCP SDK in its own environment at `/opt/ai-hub` (`python3 -m venv /opt/ai-hub && /opt/ai-hub/bin/pip install mcp==2.3.0`). `deploy.sh` refuses to run until that exists. `mcp/mcpo.json` is now `mcp/tool-hub.json`; the key name and file are unchanged.
- **More Approve prompts after outside content.** Once a turn has read a web page, search results, an email, a file or the screen, saving to memory, running commands, typing, clicking, writing files and starting tasks wait for a yes. Both lists are in `brain.json`.
- **Release folders taken over with `--adopt`** list what would be removed and need `--allow-removal` before anything is deleted.

## Brain

### New
- **Arty CLI remote control.** An Arty CLI session started with `/remote` or `--remote` is relayed through the brain, so starfire.ai, the Android app and the iPhone app can follow it, send messages, answer Allow/Deny prompts and stop it. The computer needs no open port. Only the main key and devices with the remote-control switch turned on can control a session, and remote control can never switch a session to allow-all.
- **Agent mode** for client agents that bring their own tools, such as Arty CLI. Agent turns go to the coding model.
- **Coding questions go to the coding model.** A chat with plain `Arty` that is clearly about code (a code block, a pasted error, or asking to write, fix, debug, refactor, review or explain code) is answered by `coding_model` in every app, with no app update. `route_coding_chats` in `brain.json` switches it off, and a model picked by name is left alone.
- **A model for each job.** `brain.json` names the main, coding and vision models separately. Models that are not downloaded are never routed to, and handoffs list each model's strengths.
- **Faster first text.** A short greeting went from 6.5 s to 3.0 s, web search from 32 s to 19 s and research from 30 s to 16 s on the reference machine. Pictures are described without thinking, and research checks its notes without thinking but rechecks a question's premise with it.
- **Working notes on streamed chats.** Long turns send a note every 15 seconds, so a proxy such as Cloudflare no longer cuts them off at 100 seconds.
- **Memory recalls what matters.** Each message gets the saved facts that match its words first, then the newest, as whole facts up to 4,000 characters. Before, the oldest facts were cut off.
- **Nightly memory tidy-up** at 04:00 finds facts saved twice or replaced by a later one and archives them (never deletes). "Undo the memory tidy-up" puts the last run back. `memory_tidy` in `brain.json` changes the hour or turns it off.
- **Shared terminals keep their whole conversation.** The relay keeps 2,000 events per session, shows where a conversation was compacted with its summary, and gives each terminal its own title (the folder name, numbered when taken), which the owner can rename.
- **Coding models are marked** in `/v1/models?all=1` (`coding: true`), so coding clients can offer only those.
- **q8_0 context cache** for Ollama, for more context in the same GPU memory.
- **The time in another place is worked out before the model answers.** "What time is it in Tokyo?" or "time in India" runs the new `place_time` lookup (cities from the system time zone list plus common country names), so a small model no longer guesses the hour.
- **Agent turns can stream the model's reasoning.** A client that sends `X-Arty-Reasoning: 1` (or `"arty_reasoning": true`) with its own system message gets `reasoning_content` deltas before the reply. Reasoning is never stored or sent back to the model. See `docs/API.md`.
- The end-to-end battery is now a scored eval.

### Fixed
- The honesty check runs on the turn's own model, and no longer misfires on agent turns whose client tools worked.
- A tool server that crashes is restarted, and one that cannot start answers 503 without stopping the others.
- Other models are unloaded before loading one that will not fit on the GPU.
- The subject for a second web search goes back to the thinking call.
- Every dropped connection (connection reset, aborted, timed out) counts as a hang-up in streamed and plain replies, instead of an error in the log.
- Remote sessions accept a reused request id, refuse events after a session ends (410), drop events sent after an end in the same batch, and cap sessions per computer.

### Security
- Requests for weapon steps, guides and recipes are declined in code in many more wordings, including buying a weapon online, cheaply or from a shop. Ordinary requests that mention a weapon word (a bomb calorimeter, bomb disposal careers, a game's grenades) are no longer refused.
- Tasks, schedules, reminders and handoffs pass the same safety check as chat.
- Only Arty's own key can approve or revoke devices.
- Every tool hub route needs the key, and tool servers no longer receive the hub key in their environment.
- Requests from devices without a key may only send a small body.
- The security watch retries an alert it could not deliver instead of losing it, and spends at most a minute per run resending.
- `provision.sh` creates the alert token before the security watch reads it.

## Apps

### New
- **Computers screen** on Android and iPhone to follow and steer an Arty CLI session: see what the computer asks before you allow it, send messages, Stop.

### Fixed
- Android: a file link with spaces, brackets or a stray percent no longer crashes the app.
- Android: a second lecture recorded in the same minute no longer overwrites the first.
- Android and iPhone: a permission answer is sent once, its card hides when accepted, and a fresh card shows when the computer asks again under an answered id.
- iPhone: the Computers screen shows a terminal's earlier conversation, the compacted summary and its title.
- iPhone: the app starts on a plain-http home address; Send sends once and keeps text typed meanwhile; the Computers screen keeps at most 500 lines and says when lines were missed.
- Android: the personal build can reach a plain-http home brain, and an identity proof is reused for five minutes instead of on every request.
- Apps accept a device key's identity proof on a plain-http home address.

## Desktop app

### New
- A smoother, warmer look across the desktop, Android and iPhone apps.

### Fixed
- Connect checks that a plain-http brain knows the key before sending it.
- An IPv6 address counts as home only when it is private. Tailscale addresses and `.ts.net`, `.internal`, `.home` and `.home.arpa` names count as home.

## Voice

### New
- Requests end with Smart Turn and speech is found with Silero VAD, so a short pause no longer cuts you off.
- Requests are transcribed with Moonshine while you speak.
- "Hey Arty" is spotted with a small trained model before whisper.

## Browser

### New
- Working selectors are remembered per site and step.

## Release builder

### Fixed
- `--adopt` lists what it would remove and refuses without `--allow-removal`.
- Test fixtures and docs use neutral names, hosts and addresses.
