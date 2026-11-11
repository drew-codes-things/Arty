# Arty API

Everything Arty can do, for your own apps. Home network: http://192.0.2.13:8081. Anywhere: https://arty.example.com, which also needs the Cloudflare Access service-token headers (`CF-Access-Client-Id`, `CF-Access-Client-Secret`).

- Base URL: `http://192.0.2.13:8081`
- Key: `Authorization: Bearer <ARTY_API_KEY>`. Use your brain's API key: `provision.sh` saves it to `/srv/ai/secrets/arty-api.txt` on the machine running the brain.
- Errors: `{"error": {"message": "..."}}` with a matching HTTP status.

## Chat (OpenAI-compatible)

Any OpenAI client library works: set `base_url` to `http://192.0.2.13:8081/v1` and the key above.

`POST /v1/chat/completions`

```json
{"model": "assistant-core", "messages": [{"role": "user", "content": "What's on my server?"}]}
```

- `model`: `assistant-core` uses Arty's main model; `arty/<ollama model>` (e.g. `arty/qwen2.5-coder:14b`) uses that model for this request only. `GET /v1/models` lists them.
- `GET /v1/models?all=1` lists every downloaded chat model. A request sending `X-Arty-Client-Tools: 1` and `X-Arty-Any-Model: 1` may name any of them; a model that isn't downloaded or only makes embeddings is a 400, and Ollama being down is a 503. See [Who Can Run Arty's Tools](tools-trust-boundary.md).
- `client` (optional, on chats and chat jobs): your app's name, such as `desktop`, `voice`, `telegram` or `discord`. Lowercase letters, digits, `_` and `-`, up to 32 characters.
- Arty adds its persona, long-term memory, recent conversations and all its tools itself, and remembers the exchange. Send only your app's messages; a `system` message is added as extra instructions.
- The reply has the usual `choices[0].message.content`, plus `tools_used`.
- Behind Cloudflare (https://arty.example.com, which cuts requests off at 100 s), use chat jobs instead: `POST /v1/chat/jobs` with the same body returns `{"id"}` at once (202); poll `GET /v1/chat/jobs/{id}` until `status` is `done` (`answer`, `tools_used`) or `failed` (`error`); while it's `running`, `partial` holds the answer's first sentences so far. Jobs live in memory for an hour.
- `"stream": true` sends the answer as SSE chunks: each sentence as soon as nothing can rewrite it, then the rest. Sent text is never taken back (the final answer starts with it), so it can be shown and spoken as it arrives. "Remind me" answers, and replies that may yet be sent back to use tools, arrive only at the end.
- Outward actions (posting, sending, buying) aren't done until the user's latest message is a plain "yes"/"go ahead": Arty asks first.

## Files and attachments

- `POST /v1/files`: upload one file as the raw request body, with `Content-Type` and its name URL-encoded in `X-Filename`. Max 95 MB (Cloudflare's limit). Returns `{"id", "name", "mime", "kind", "size", "download"}` (201); `kind` is `image`, `audio`, `video` or `other`. The same content uploaded again gets the same id.
- `GET /v1/files/{id}`: the same details. Files are deleted 24 hours after upload.
- Attach files to a chat message as content parts: `{"type": "file", "file": {"file_id": "..."}}`. `image_url` parts with `data:` URLs and `input_audio` parts work too. Arty sees a description of images, a description of video frames plus a transcript of the soundtrack, and a transcript of audio. Each is worked out once per file and reused on later turns.
- Arty can convert attachments (`convert_file`: images via ImageMagick, audio/video via ffmpeg) and replies with a download link: `https://192.0.2.13:8443/arty-files/{id}/{token}/{name}` (through Caddy, so it works from https pages). The token in the link is the only credential, like the task hooks; `/arty-files/...` on the brain's own address (or via Cloudflare) serves the same file.
- Apps that send their own tools can add `X-Arty-Skip-Groups: browser,screen,windows` (tool group names from brain.json) so Arty doesn't also offer its own versions; the desktop app does, since it drives the user's own browser and screen.
- Images no longer go straight to the vision model. Send `X-Arty-Direct-Vision: 1` (or `"arty_direct_vision": true`) for the old behaviour: a precise question about a picture answered by the vision model alone, without Arty's persona or tools (the desktop app's screen reading uses this).
- starfire.ai uploads audio and video you attach there to `/v1/files` and sends them as file parts; photos arrive as images.

## Tools

- `GET /v1/tools`: every tool in OpenAI function format, each with the `groups` it belongs to.
- `POST /v1/tools/{name}` with `{"arguments": {...}}`: run one directly. Returns `{"result": "..."}`.
  If it would act in your name (see approvals below) you get `409` with `type: needs_approval` and a `reason`; resend with `"approved": true` once the user agrees.

Tool names are `server__tool` (e.g. `research__web_search`, `workspace__browser_open`, `homelab__proxmox_status`) plus Arty's own (`schedule_task`, `start_background_task`, `list_models`, ...).

Any key holder can approve its own request, so a key is as good as the owner's password: see [Who Can Run Arty's Tools](tools-trust-boundary.md).

## Background tasks

Arty works on these alone, one at a time, and sends the result to the phone.

| | |
|---|---|
| `POST /v1/tasks` `{"instructions": "...", "title": "..."}` | queue a task (201) |
| `GET /v1/tasks?status=waiting&limit=30` | list (statuses: queued, running, waiting, done, failed, cancelled) |
| `GET /v1/tasks/{id}` (`?history` for the full transcript) | one task, with `result` or `waiting` |
| `POST /v1/tasks/{id}/approve` / `deny` | answer a held action (`waiting.kind` = `approval`) |
| `POST /v1/tasks/{id}/done` `{"message": "optional note"}` | after doing what Arty asked (`waiting.kind` = `user`, e.g. sign in) |
| `POST /v1/tasks/{id}/cancel` | stop it |

When a task needs you, `waiting.message` says what for, and Updates in the phone and desktop apps shows the same buttons.

## Schedules

| | |
|---|---|
| `POST /v1/schedules` `{"instructions": "...", "when": "weekdays 08:00", "title": "..."}` | create (201). `when`: `daily 08:00`, `weekdays 07:30`, `mondays 09:00`, `every 2 hours`, `hourly`, `in 30 minutes`, `tomorrow 18:00`, `2026-12-24 09:00`, or cron `0 8 * * 1-5` |
| `GET /v1/schedules` (`?all` includes finished/cancelled) | list with `next_run` (Unix time) |
| `DELETE /v1/schedules/{id}` | cancel |

Each run becomes a background task.

## Memory and history

- `GET /v1/memory`: the long-term memory graph (entities and observations).
- `GET /v1/conversations?q=key+words&limit=10`: past exchanges matching all the words (no `q`: the most recent).

## Notifications

`POST /v1/notify` `{"title": "...", "message": "..."}` sends to Updates in the Arty phone and desktop apps. Every notification goes there and nowhere else (not Telegram or Discord).

## Announcements (what Arty tells you)

- `GET /v1/announcements?after=<id>&wait=<seconds>`: everything Arty has notified you about since `after` (reminders, briefings, task results, approvals, Uptime Kuma alerts), oldest first, as `{"announcements": [{id, kind, title, message, task_id, actions, created}], "last": <id>}`. With `wait` (up to 55 s) it waits for one to arrive. Call with `after=0&wait=0` first to learn `last`, then follow from there. `kind` is `reminder`, `approval` (with `task_id` and `actions`, e.g. `approve`/`deny`), `alert` or `message`.
- `POST /hooks/alert?token=<ALERT_TOKEN>`: Uptime Kuma's webhook (secret in brain.env). Recorded in the feed only.
- `POST /hooks/notify?token=<ALERT_TOKEN>` `{title, message, urgent}`: alerts from the homelab (the Proxmox host's `arty-notify`, its notification target "arty", the security watch), shown as alerts.
- The morning (08:00) and evening (22:00) briefings are ordinary schedules; ask Arty to move or cancel them.

## Calendar

- Arty has its own calendar (the `events` table): tell it "put the dentist in for Tuesday 3pm" from any app. Tools: `calendar_add` (title, when, duration_minutes, location, notes), `calendar_events` (days), `calendar_change` and `calendar_remove` (by the #number calendar_events shows). Times like "Tuesday 3pm", "25 Dec 14:00", "24/12" (all day), "tomorrow 18:00", "every Monday 6pm" (repeats). Also callable with `POST /v1/tools/<name>`.
- For apps: `GET /v1/calendar?days=30` (Arty's and Google's events as JSON: id (Arty's only), title, start, end, all_day, location, repeats, calendar), `POST /v1/calendar` with `{title, when, duration_minutes, location}`, `DELETE /v1/calendar/{id}`.
- Google Calendar is optional and shown alongside: each calendar's "secret address in iCal format", stored root-only in `/srv/ai/secrets/calendar.env` by `bin/set-calendar` (run from the laptop; it never prints the links).
- 15 minutes before each timed event, Arty sends "📅 Coming up" to Updates in the phone and desktop apps. The briefings include the day's events.

## Lectures

- `POST /v1/lectures` `{"file_id": "...", "title": "..."}`: turn an uploaded recording (`POST /v1/files`, audio or video) into notes and revision cards (202). One lecture is worked on at a time; the rest wait.
- `GET /v1/lectures`: the 30 most recent, without the text: `{"lectures": [{id, title, status, duration, cards, error, created, updated}]}`. `status` goes `queued`, `transcribing`, `writing notes`, `making cards`, then `done` or `failed` (with `error`).
- `GET /v1/lectures/{id}`: one lecture, with its `transcript` and `notes`.

## Voice

Attach audio to a chat message (above) and Arty transcribes it. The server mic and speakers, and "Hey Arty", are in [voice/README.md](../voice/README.md).

## Approvals

Held for approval: clicking post/send/connect/apply/delete-style buttons, submitting forms, downloading, or pressing Enter or Space (in the browser or on the desktop, and desktop clicks) while a social, mail, shopping or code-hosting site is open (list in `brain.json` > `approval`), any buy/pay/checkout button anywhere, and shell commands that push code, SSH out, POST data or send mail. Typed text is checked like a command only on lines that look like one. Reading and browsing never are.

## Approved computers

- `POST /v1/device/start` (no key) with `{"name"}` starts the "approve this computer" flow and returns a `device_code`, a `user_code` and a link for a signed-in app; `POST /v1/device/token` (no key) with `{"device_code"}` returns the computer's own key once the owner has approved it.
- `GET /v1/device/pending?code=` and `POST /v1/device/approve` with `{"user_code", "approve": true | false}` are for the signed-in app.
- `GET /v1/devices` lists the approved computers (`id`, `name`, `address`, `created`, `last_used`; never the key). `DELETE /v1/devices/{id}` turns one key off at once.
- A device key works everywhere the shared key does. Its `/v1/identity` proof is the HMAC keyed with the SHA-256 hex of the key.

## Health

`GET /health` (no key) returns `{"ok": true}`.

## Arty for iPhone

`GET /ios/...` (no key) serves the iPhone web app's files from `apps/ios` (`ARTY_IOS_DIR` to move it): only known
file types, never hidden files or its tests, with a Content-Security-Policy that keeps the page talking to this
server only. `/ios` redirects to `/ios/`. See [apps/ios/README.md](../apps/ios/README.md).
