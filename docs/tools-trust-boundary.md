# Who Can Run Arty's Tools

This page says who the brain trusts, what a caller can make it do, and what it checks anyway. It describes the code in
`brain/` (`brain.py`, `api_keys.py`, `agent_loop.py` and `policy.py`) as it is now.

## The Short Version

**Anyone holding an API key is the owner.** The brain has two shared keys, `BRAIN_API_KEY` and the optional app key
`ARTY_API_KEY`, plus one key for each computer approved through the device flow. They all have exactly the same
power. There are no per-app permissions. Only give a key to software you would trust with your desktop, your Gmail
window and your homelab.

Device keys start with `arty_dev_`. The brain keeps only a SHA-256 hash of each one, with the name the computer gave,
the address it asked from and when it was last used. `GET /v1/devices` lists them and `DELETE /v1/devices/{id}` turns
one off at once; the shared keys keep working.

## Routes That Need No Key

| Route | What it gives | What protects it |
|---|---|---|
| `GET /health` | that the brain is up | nothing to protect |
| `GET /v1/identity?nonce=` | HMAC proofs that this brain holds the keys (for a device key, the proof is keyed with the SHA-256 hex of that key) | the keys never leave; a bad nonce is a 400 |
| `POST /v1/device/start`, `/v1/device/token` | the "approve this computer" flow | a key is only handed over after the owner types the code shown on that computer into a signed-in app; 3 waiting requests per address, 20 new requests a minute and 50 waiting in all, 10 minutes each |
| `/hooks/tasks/...` | the Approve, Deny, Done and Cancel buttons in notifications | a random token per task, in the link |
| `POST /hooks/alert`, `/hooks/notify` | the homelab's own watchers alerting you | `ALERT_TOKEN`; refused if it isn't set |
| `/arty-files/{id}/{token}/{name}` | a converted file Arty made | the token in the link; files go after 24 hours |
| `/ios/...` | the iPhone web app's files | static files only; the app asks for a key before it talks to Arty |

Everything else answers 401 without a key.

## Running a Tool Directly: `POST /v1/tools/{name}`

- **Arty's own tools** (reminders, calendar, lists, models and the rest) can only be run this way if they are in
  `API_CALLABLE_NATIVE`. Others answer 400 ("only works inside a conversation").
- **Hub tools** (`research__`, `workspace__`, `homelab__` and so on) run when the name is known to the tool hub.
- Before a hub tool runs, the brain still:
  - refuses to upload a private file (keys, passwords, a browser profile) with `workspace__browser_upload` (403);
  - reads the current browser page first for the click, type, key press and upload tools, so it knows which site it is acting on;
  - works out whether the action needs approval under `approval` in `brain.json`. If it does, it answers 409 with
    `type: needs_approval` and a `reason`.
- **`"approved": true` is trusted as the owner's answer.** The brain cannot tell whether a person really saw the
  reason, so any key holder can approve their own request. That is fine for the owner's own apps, which show the
  reason and only send `approved` after a tap. It is the reason a key must never go to anything you don't control.

## Chatting: `POST /v1/chat/completions` and `/v1/chat/jobs`

What the brain takes from the caller as given:

- **The messages.** A `system` message is added to Arty's instructions as extra notes. From the desktop and phone
  apps, `<context>` and `<screen>` blocks at the end of a message are used for that turn but kept out of memory and
  the archive. From any other caller they are left in the message as the user's own words, so the safety check reads them.
- **The caller's own tools**, only when it sends `X-Arty-Client-Tools: 1` (or `"arty_client_tools": true`). The model
  may ask the caller to run them; the brain never runs them itself. The caller is responsible for asking its user first
  (the desktop app, Arty CLI and Arty Code all do).
- **`X-Arty-Skip-Groups`**, so the brain doesn't also offer its own browser or screen tools. This only removes
  tools; it can't add any.
- **`X-Arty-Any-Model: 1`** together with client tools lets the request name any model downloaded on the AI server,
  not only the ones in `arty_models`. A model that isn't downloaded, can only make embeddings, or is listed in
  `solo_models` (and not in `arty_models`) is a 400. A model other than the main one stays loaded for 3 minutes after
  its last answer, and the main model is loaded again a little after that.
- **`"client": "phone"`** on a chat job lets the reply carry a call or text action for the phone app to offer. The
  brain never calls or texts anyone itself.
- **`X-Arty-Direct-Vision: 1`** sends a picture question straight to the vision model, without Arty's tools.

What the brain checks whatever the caller says:

- The safety check runs before the model and the web see anything.
- Outward actions in a chat (posting, sending, buying, running commands) wait for a plain "yes" as the user's latest
  message, under the same `approval` rules as above. Background tasks wait for the Approve button instead.
- Private files are never uploaded, in a chat or a background task.
- **Outside content taints the turn.** Once a turn (or a background task) has read something from outside, a web page,
  a search result, an email, a fetched or library document, a file or the screen (`untrusted_reads` under `approval`),
  the actions in `after_untrusted` wait for a yes or the Approve button even when the rules above would let them
  through: memory writes, commands, typing, key presses and clicks on the desktop, writing files, the clipboard, email
  drafts, Nextcloud uploads, and starting or scheduling background work. The reason ends "asked for after reading
  outside content", so text in a page or an email can't quietly make Arty act. Reading more is never held back. Both
  lists can be changed in `brain.json`; the defaults are in `brain/policy.py`.

## Limits of the Checks

- **Commands are checked against a deny-list.** `command_patterns` in `brain.json` catches the common ways of sending
  data out (`git push`, `ssh`, `curl` with a body, mail, piping a download into a shell and a few more). It does not
  understand the shell: a script written to a file and then run, `gh`, `npm publish`, `docker push` or a here-document
  all pass without asking. Treat it as a guard against slips, not against a model trying to get round it.
- **Private folders are a list too.** Uploads and file sends refuse the folders in `sharing.PRIVATE_FOLDERS` (SSH and
  GPG keys, browser profiles including the Gmail Chrome and the browser agent) and names that look like secrets. A
  secret kept anywhere else is not known to be private.
- **The identity proof is only as strong as the key.** `/v1/identity` answers anyone, so a short or made-up key could
  be guessed offline from a proof; keep keys long and random. On plain http the proof only shows the brain was there
  when the app connected: a machine in the middle at home could pass the nonce on to the real brain and then read the
  key from the next request. TLS on the home network side is the real fix.
- **Who is asking.** The device flow uses the `Cf-Connecting-Ip` header only when the request comes straight from an
  address in `BRAIN_TRUSTED_PROXIES` (the Cloudflare tunnel's machine); otherwise it uses the connection's own address.

## What This Means in Practice

- Treat `ARTY_API_KEY` like the owner's password. Anything that holds it can read and act as the owner.
- Away from home, keep the brain behind Cloudflare Access, so a stolen key alone isn't enough to reach it.
- The device flow is the safe way to give a new computer a key: the key is never typed or pasted, it only goes out
  after you type the code shown on that computer into the app, and you can turn that one key off later without
  changing anything else.
