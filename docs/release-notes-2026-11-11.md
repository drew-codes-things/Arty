# Arty 1.3.0 release notes, 11 November 2026

Everything fixed and added since `d46e1c5`, in two rounds: the first full review of Arty and the second review that checked those fixes. Ids in brackets match the review notes.

## Changes you'll notice

- **Chrome extension needs pairing once.** After updating to 1.3, browsing through Chrome stops until you paste the pairing code into the extension's popup. The desktop app shows the code under **Chrome sites…** in the tray menu. Until it is paired the extension shows a red `!`. (N-C4, B-M15)
- **Sites are asked about again in some cases.** Sites you allowed over plain `http://` or on a non-standard port (for example `localhost:8080`) are now stored with the scheme and port, so Arty asks about them once more. (N-C11)
- **The Quest headset pairs again, by name.** A new pairing code gives the headset its own key. Old shared-key pairing keeps working for now. Once every headset has paired by name, set `HEADSET_LEGACY_PAIRING=0` on the voice server. (N-B8, S25)
- **The first voice hub account needs a setup code.** With no account yet, the hub writes a one-time code to `setup-code` in its data folder, and the setup page asks for it. Existing hubs with an account are unchanged. (S23)
- **`BRAIN_TRUSTED_PROXIES`.** Set this in the brain's environment to the address of the proxy in front of it (for example the Cloudflare tunnel host). Without it every remote device looks like it comes from the proxy, shares one rate limit, and the approve sheet shows the proxy's address. (N-R1)
- **Release builds stop on owner details.** `downloads/build_release.py` now refuses to build while owner names or homelab ids are in released files, unless `--allow-owner-details` is given. A release folder without the `.arty-release` marker needs `--adopt` once. (N-B4, S40)
- **Approved devices get their own keys.** Every device approved through the authorise flow now gets a key that starts `arty_dev_`, which you can see and turn off in the iPhone app under More > Approved devices. Shared keys still work. (N-R2)
- **Mouse and keyboard ask by default** on the desktop app until you turn the ask off in settings.

## Brain

### New
- Any downloaded model can be used by apps that opt in with the `X-Arty-Any-Model: 1` header (together with `X-Arty-Client-Tools`), and `GET /v1/models?all=1` lists them. Other apps keep the allow-listed models.
- A model other than the main one stays loaded for 3 minutes, and the main model is loaded again 200 seconds after the last such turn. (N-R4)
- `brain/settings.py` holds the settings that need no keys; `environment.py` now only reads keys. The brain was also split into smaller modules (`native_tools`, `reply_rules`, `archive`, `steering`, `schedules`, `announcements`, `api_support`) with no change in behaviour. (N-R10)
- New `brain.json` settings for anything personal: `desktop_console`, `desktop_look.persona`, `time_zone`, `time_zone_label`, `crisis_help` and `starter_deck`. The shipped values match what was hard-coded before.
- Chats now carry a client name (`desktop`, `voice`, `telegram`, `discord`) on `/v1/chat/completions` as well as on jobs. (B-M7)
- "every 1 hour" and "every 1 minute" are understood, along with intervals such as "every 45m", months, weeks and seconds.
- `/v1/identity` lets a client check it is talking to the real brain before sending its key.

### Fixed
- A model that is not downloaded, or an embedding model, is a clear 400 instead of a quiet swap to the main model. A model kept for plain chats can't be given Arty's tools. (N-R5)
- Ollama being down gives a 503 on `?all=1` and on the any-model path. (N-R6)
- Task links, `/v1/identity` and the calendar answer cleanly on a database error; `days=inf` or `nan` on `/v1/calendar` is a 400. (N-R9)
- A one-shot reminder is retried at most 10 times. (N-R8)
- Calendar events can't repeat more than once a day, can't last more than 366 days, and a 29 February schedule is found again after a leap year. (N-A5, N-A6, N-A7)
- Schedules, reminders, all-day repeats, leap years and written dates were checked and fixed across the board.
- The email watch forgets an email after 4 days and no longer sends the same email twice when its shown date changes. (N-A8)
- A certificate problem is reported again if it comes back after a fix. (N-A9)
- Note search shows 1,500 characters per note with the full length, and results from Arty's own tools are capped. (N-A2)
- File details are written to a temporary file and swapped in whole; uploading the same file from several places at once works. (N-A11, B-L15)
- Follow-up questions ("what about that?") no longer start a fresh web search. (B-M9, S20)
- A call or text needs a number, "my" or "the", a relation word or a capitalised name. (B-M8)
- At most 16 quizzes and 6 direct chats run at once. (B-M7, S3)
- Background tasks save after every tool result, so a restart carries on after the tools already run.
- An approval for a long command covers the full command, not just its start.
- The self-harm check catches "kill/hurt/harm myself" and "end my life" again, while "cut myself shaving" and statistics questions stay normal.

### Security
- An approval is a short list of plain yeses, and a yes only approves the exact action asked about. (B-M5)
- Key presses, desktop clicks, downloads and Space or Enter in the browser ask first on sensitive sites; a download on a page that hasn't been read also asks. (N-A4)
- Typed text is only checked for commands on lines that look like commands. (N-A12)
- PDF and binary EPS pictures are refused, and scripted images are refused before resizing. (S10, N-A10)
- Download tokens are cut from the request log. (B-M3)
- Pandoc runs sandboxed and without raw HTML or raw attributes. (S14)
- The three browser profile folders are private. (S13)
- `docs/tools-trust-boundary.md` explains what the checks can and can't stop. (N-R7, S12)

## Devices and keys

### New
- Device login: `POST /v1/device/start`, then approve the code in the iPhone app, then `/v1/device/token` hands the key over once.
- Each approved device gets its own key. Only a hash is stored. `GET /v1/devices` lists them and `DELETE /v1/devices/{id}` turns one off. (N-R2)
- `/v1/identity` also proves itself to device keys. The HMAC key is the SHA-256 hex of the device key. (N-R3)

### Security
- `Cf-Connecting-Ip` is only believed from addresses in `BRAIN_TRUSTED_PROXIES`. (N-R1)
- At most 3 pending requests per address and 20 new requests a minute overall. Approve must be a true or false value.

## Tools and hub

### Fixed
- `reindex_library` returns at once and indexes in the background; two indexes can't start together. (S22)
- Library indexing no longer retries a file the embedder refuses until it changes.
- `read_document` doesn't overwrite an existing download.
- Homelab tools reach each guest on its own Proxmox node, report an unreachable Proxmox or bad certificate clearly, and keep at least one snapshot.
- `read_file` explains an empty file, and `edit_file` keeps line endings and refuses files that aren't UTF-8.

### Security
- git: each action has a list of exact allowed options, so shortened forms can't slip through. `pull` and `clone` take no extra names. (N-B3, B-L48)
- One private-folder check for the Gmail Chrome profile, the browser profile, the browser agent folder and `.ssh`, by text and again by real path on the desktop VM, so a link into them is refused. (N-B5, S21)
- Writes are atomic and keep normal permissions; a write onto a folder is refused. (N-B6)
- PC command refusals understand heredocs, `eval`, `xargs` and `busybox`; `systemctl status reboot` is allowed, rebooting is not. (B-L38)
- The mcpo key is read from the environment, not the command line.
- Every page and email result is marked as data, not instructions.

## PC (desktop VM) and egress

### New
- `bin/desktop-egress apply | status | undo` keeps the desktop VM off the home network except DNS and DHCP, with the internet left open. The rules live on the Proxmox host, so the VM can't undo them.
- `bin/pin-desktop-host-key` pins the VM's ssh key for the tool hub. A changed key is pinned with `--trust-fingerprint` after checking it on the VM console. (N-B10)

### Fixed
- The egress rules load after bridge connection tracking and reload in one step, so a restart never leaves the VM without rules. (N-B1, N-B7)
- The sign-in page check uses the hostname and path only. (B-L42)
- The browser agent's restart delay resets after a run that stayed up 5 minutes. (B-L55)

### Security
- IPv6 from the VM is refused except router and neighbour discovery; fake DHCP replies, multicast and broadcast are dropped, and the VM may only answer ARP for its own address. (N-B2)
- The browser agent needs its token, checks Host and Origin, and only accepts JSON.

## Desktop app

### New
- **Chrome sites…** in the tray lists each allowed site with a Forget button and shows the extension's pairing code.
- AppImage 1.2.1 was rebuilt with the first round of fixes. The second round's desktop changes need the next build.

### Fixed
- A refused site is no longer saved as allowed. (N-C2)
- Saying yes to running a program no longer also allows the mouse and keyboard for the rest of the reply. (N-C7)
- Tool labels in history are capped at 80 characters and 30 actions per reply. (N-C9)
- Config writes: a refused write doesn't change the running setting, hand-written lists are replaced cleanly, and a broken file gives a clear error. (N-C10)
- The "something else is using Arty's port" box shows once, with Quit and Keep waiting. (N-C12)
- Windows paths and `echo` text are typed exactly. (B-L62)

### Security
- File search skips secret files. (N-C1, B-M18)
- More commands ask first: downloads to files, redirects to files, piping into a shell, inline scripts, `unlink`, `gio trash`, `git config --global`, `http.server` and more. The secret-path check follows `cd`, variables, braces and wildcards. (N-C3, B-L60, B-L61, N-C13)
- Writes to startup files ask (Windows Startup folder, PowerShell profiles, `.envrc`, editor task files, desktop launchers). (B-M17)
- `chrome_open` refuses tricky addresses, sites are named the way Chrome reports them, and the tab is rechecked at action time. (N-C11)
- `chrome_tab` asks per site and only shows titles of allowed sites. (N-C6)
- The window only talks to the service after it proves it holds this launch's token. (N-C5, B-L71)
- A plain-http home address that isn't on this computer must prove itself before anything is sent. (B-M19)
- `/whoami` refuses web page requests. Slow regular expressions are refused. (B-L64)

## Browser extension

### New
- Version 1.3: a popup to paste the pairing code, and a red `!` until paired.

### Security
- The extension routes need the pairing code as well as the extension's origin. (N-C4, B-M15)
- Click, type and tab actions are refused if the tab moved to another site. (N-C11)
- Password, card, one-time code and token fields are never read out.

## Voice and headset

### New
- Per-headset keys: pairing with a device name gives that headset its own key and id. `HEADSET_TOKEN` is the owner key that makes codes and lists or forgets headsets (`GET /pair/devices`, `POST /pair/forget`). (N-B8, S25)
- `HUB_HOST` and `HEADSET_HOST` pick the listen addresses.

### Fixed
- The assistant keeps listening after a failed turn, a failed sentence or a dropped headset socket, and crashed loops restart.
- Speech cut at 15 seconds no longer starts a second request without the wake word.
- Wake checks no longer wait behind a satellite transcription.

### Security
- The voice hub's first account needs a one-time setup code, and the login cookie is Secure over https. (S23)
- Hub passwords use bcrypt with a SHA-256 pre-hash; failed logins are limited per address and email.
- Headset pairing is limited per address and overall; the identity check is limited to 30 a minute per address; the headset server won't start with a `HEADSET_TOKEN` under 32 characters.

## iOS (web app, `apps/ios`)

### New
- The app moved out of beta from `apps/beta/ios` to `apps/ios`.
- More > **Approved devices** lists every approved device and turns one off with a second tap.
- Approving a device: the approve sheet, a Retry button after a failed approve, and a hint when the link carries a code but this browser isn't set up yet. (N-D10)

### Fixed
- The app keeps waiting through 429, 500 and 520 to 530 instead of giving up. (N-D12)
- Authorise codes in the address are never cached. (N-D11)
- Phone layout: 44-pixel buttons and a 44-pixel composer.

## Android

### Fixed
- The chat wait keeps going through 429, 500 and 520 to 530. (N-D12)
- Cloudflare Access headers are only sent with the key, never with the identity check. (N-D13)
- A saved connection is only cleared when it truly can't be read. (N-D15)

### Security
- Approval notices hide their text on the lock screen, and Approve and Deny buttons there only appear on Android 12 and later. (B-M21)

## Quest

### New
- **Personal** and **shareable** builds, like the Android app (`assemblePersonalRelease`, `assembleShareableRelease`, `testPersonalDebugUnitTest`, `testShareableDebugUnitTest`). The shareable build has no address or key baked in. (S35)
- Pairing by name: the headset sends its model name and keeps its own key and id. The shareable build can only connect with a setup code. (S25)

### Fixed
- A damaged save is cleared, a temporary key-store fault keeps it. (N-D17)
- The link gives up after 20 failures in a row and says so instead of retrying for ever; a refused service start shows a message instead of crashing. (N-D18)
- A warning shows when pairing over a connection that isn't secure. (N-D16)

## Chat bots

### Fixed
- Telegram: a locked `/pair` gets "Too many wrong pairing codes. Try again later." once per lock; groups stay silent. (N-B9)
- Both bots send their client name and handle non-JSON replies.

### Security
- Telegram pairing allows 5 wrong codes an hour across all senders.

## Release builder

### New
- `.arty-release` marker and `--adopt` for an existing release folder; refuses to build into the repo itself. (N-B4)
- Release builds blank `desktop_console` and `starter_deck` and drop the persona. (S17)

### Fixed
- Files no longer released are removed from the release folder.

### Security
- Owner names and homelab ids stop the build unless `--allow-owner-details` is given. (S40)

## Arty CLI

Arty CLI is now its own project: [ArtyCode](https://github.com/drew-codes-things/ArtyCode). It replaces the beta terminal app in `apps/beta/code`.

### New
- `arty` opens it; installers for Debian (`install.sh`) and Windows (`install.ps1`, PowerShell 5.1).
- Talks to your Arty only. `arty login` takes an Arty API key or opens the approve page in your browser.
- `/model` lists every downloaded brain model.
- Hooks (with JSON decisions), MCP servers over stdio, skills, notebooks, parallel explore helpers, saved allow and deny rules, `/undo`, and headless `-p --output-format json`.
- Retry with back-off that honours Retry-After.
- An Arty red look; `NO_COLOR` is respected.

### Fixed
- Tools and the system prompt stay the same for a whole turn; summarising starts at 75% of the model's window. (N-X2, N-X18)
- Device keys are proved with the SHA-256 hex of the key.

### Security
- Look-only commands, paths, links and git settings that run programs all ask when they should. (N-X1, N-X6, N-X7)
- Folder trust is reset when the project's settings or MCP file changes. (N-X5)
- `web_fetch` refuses local and private addresses at every redirect. Device login over plain http is refused for public addresses. (N-X15, N-X16)
- Server-side web search is off unless asked for. (N-X9)

## arty-code (beta)

Being replaced by Arty CLI. Still fixed in this round:
- Look-only commands expand wildcards and check every match; options that follow links ask. (N-D1, N-D3 to N-D7)
- Windows paths are split the Windows way. (N-D2)
- The brain's identity is checked again every 5 minutes, and a redirect never carries the key on. (N-D8)
- Line endings, Ctrl-C and Ctrl-D behave properly. (N-D9)

## Not verified yet

- Pandoc's sandbox on the desktop VM for docx, odt, pptx, epub and html. (N-A3)
- The desktop app's new screens (Chrome sites, the port dialog) by hand in Electron.
- The extension popup, badge and pairing in a real Chrome.
- The desktop app and Arty CLI on a real Windows machine (the CLI installer was tried on PowerShell 5.1 in a test VM).
- The Quest app with sound, and pairing by name on the real headset.
- Every phone layout on a real phone.
