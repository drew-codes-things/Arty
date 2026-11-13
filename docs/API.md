# Arty API

Everything Arty can do, for your own apps. Home network: http://192.0.2.13:8081. Anywhere: https://arty.example.com, which also needs the Cloudflare Access service-token headers (`CF-Access-Client-Id`, `CF-Access-Client-Secret`).

- Base URL: `http://192.0.2.13:8081`
- Key: `Authorization: Bearer <ARTY_API_KEY>`. Use your brain's API key: `provision.sh` saves it to `/srv/ai/secrets/arty-api.txt` on the machine running the brain.
- Errors: `{"error": {"message": "..."}}` with a matching HTTP status.

## Chat (OpenAI-compatible)

Any OpenAI client library works: set `base_url` to `http://192.0.2.13:8081/v1` and the key above.

`POST /v1/chat/completions`

```json
{"model": "Arty", "messages": [{"role": "user", "content": "What's on my server?"}]}
```

- `model`: `Arty` uses the main model (the old name `assistant-core` still works); `arty/<ollama model>` (e.g. `arty/mimo-v2.6:9b`) uses that model for this request only. `GET /v1/models` lists them.
- `GET /v1/models?all=1` lists every downloaded chat model. A request sending `X-Arty-Client-Tools: 1` and `X-Arty-Any-Model: 1` may name any of them; a model that isn't downloaded or only makes embeddings is a 400, and Ollama being down is a 503. See [Who Can Run Arty's Tools](tools-trust-boundary.md). Each entry carries `context_tokens` (the context window the brain gives that model: `options.num_ctx` in brain.json, or the model's own size from `model_windows.num_ctx` when `model_windows.enabled` is true; it ships off, ready with 49152 for mimo-v2.6:9b. With it on, sizes differ per model, so a client should use the entry for the model it is sending to, not the smallest) and `reply_tokens` (the most tokens one agent turn reply can use, thinking included: `agent_options.num_predict`, else `options.num_predict`, else 2048). A client should keep at least `reply_tokens` of the window free when it sizes its prompt.
- `client` (optional, on chats and chat jobs): your app's name, such as `desktop`, `voice`, `telegram` or `discord`. Lowercase letters, digits, `_` and `-`, up to 32 characters.
- Arty adds its persona, long-term memory, recent conversations and all its tools itself, and remembers the exchange. Send only your app's messages; a `system` message is added as extra instructions.
- The reply has the usual `choices[0].message.content`, plus `tools_used`.
- Behind Cloudflare (https://arty.example.com, which cuts requests off at 100 s), use chat jobs instead: `POST /v1/chat/jobs` with the same body returns `{"id"}` at once (202); poll `GET /v1/chat/jobs/{id}` until `status` is `done` (`answer`, `tools_used`) or `failed` (`error`); while it's `running`, `partial` holds the answer's first sentences so far. Jobs live in memory for an hour.
- `"stream": true` sends the answer as SSE chunks: each sentence as soon as nothing can rewrite it, then the rest. Sent text is never taken back (the final answer starts with it), so it can be shown and spoken as it arrives. "Remind me" answers, and replies that may yet be sent back to use tools, arrive only at the end.
- Coding agents: a request sending `X-Arty-Client-Tools: 1` and `X-Arty-Agent: 1` is an agent turn. The model gets only the tools the request lists and the request's own `system` message as its instructions, and every tool call comes back to the client to run. Agent turns use brain.json `agent_options` laid over `options` (no repeat penalty so old text can be copied exactly, a longer reply), always with `options.num_ctx`. Sampling is per model: with `agent_options.sampling` set to `card` (the default) a model gets `agent_options.models.<name>` (else `models.default`), the values from its own model card or Ollama's bundled parameters, with `without_thinking` laid over them when the model is not thinking on that turn; `agent_options.repeat_penalty` always wins so edits stay exact. Setting `sampling` to `steady` sends `agent_options.steady` (temperature 0.15) to every model instead, for comparing the two. `GET /v1/models?all=1` shows each model's choice as `agent_sampling`: `arm`, `thinking` and `without_thinking`. Whether the model thinks is decided once per agent session, from the conversation's first user message, so nothing added later (the client's follow-ups, command output, a later request) switches it on or off part way. When a client compacts the conversation, the brain recognises it as the same session if the new first message carries the text of any earlier user message (its first paragraph, 12 characters or more) verbatim, whatever the compaction message says around it; ArtyCode's carries the request. A session the brain has not seen for 2 hours, or one compacted without any earlier user text, is decided afresh. The choice lives in the brain's memory, so a restart forgets it.
- Model residency for agent sessions: an agent turn on a model other than the main one keeps that model loaded for 15 minutes (Ollama `keep_alive`) and brings the main model back only after 10 minutes with no agent request running, so a coding session never pays a reload between steps; each new step pushes the return back. Pictures, attachments and handed-over jobs on the model serving the session keep it loaded too. Every model call the brain makes, including the main model's return, uses the same context window, so Ollama never reloads a model because the window differed.
- Conversations longer than the window: agent turns ask Ollama not to drop the oldest messages (`truncate: false`), and no brain request lets Ollama shift the window while it writes (`shift: false`; Ollama treats it as a runner setting, so every call sends the same value to avoid reloads). Every other turn (chats, background tasks) is trimmed by the brain before Ollama sees it, so the prompt and a whole reply fit: it keeps the system message, the newest user message and as many of the newest turns as fit (a tool call stays with its results), counting about 3 characters a token, and adds a one-line note that earlier messages were left out. An agent conversation that does not fit is refused instead of being cut silently. Unstreamed, the reply is HTTP 413 with `{"error": {"message", "type": "context_length_exceeded", "code": 413, "model", "context_tokens"}}`. Streamed (the `: working` notes have already started a 200), the message comes as `content`, then a last chunk with `finish_reason: "stop"` and the same `error` object beside `choices`, then `data: [DONE]`. A client should compact the conversation and send it again once. A reply that reaches `reply_tokens` ends with "(I hit my reply length limit, so this was cut short. Ask me to continue.)". A reply that runs into the end of the window instead (Ollama's `prompt_eval_count` plus `eval_count` within 16 tokens of the window) is told apart: on an agent turn it is the same `context_length_exceeded` error as above (413, or the error chunk when streamed), with a message saying the window filled up before the reply finished, so the client compacts and resends; on other turns the reply ends with a note that the conversation filled the context window.
- Timings on agent turns: the reply's `usage` (in the last chunk when streamed) keeps `prompt_tokens`, `completion_tokens` and `total_tokens` and adds `rounds`, one entry per model call the brain made for this request, oldest first: `model`, Ollama's own `load_duration`, `prompt_eval_count`, `prompt_eval_duration`, `eval_count` and `eval_duration` (durations in nanoseconds, missing ones as 0), `prompt_eval_cached_count` when Ollama reports it, and `loaded_fresh` (true when `load_duration` was 1 s or more: the model was loaded for that call, or the call waited for the GPU). Other requests' `usage` is unchanged.
- Reasoning on agent turns (opt in): a streamed agent turn that also sends `X-Arty-Reasoning: 1`, or `"arty_reasoning": true` in the body, gets the model's reasoning as it is written, in chunks whose delta is `{"reasoning_content": "..."}`. They arrive while the model thinks, so normally before the answer text and tool calls, and again if the brain sends the model back for another try within the same request; the `content` and `tool_calls` chunks are unchanged, reasoning is never put in `content`, and it is never sent back to the model, so a client must not return it in `messages`. Nothing is sent when the model did not think on that turn (the brain's `think` setting decides, as before), when the request has no `system` message of its own, when the request is not streamed, or outside agent mode. The `: working` notes keep coming every 15 s either way.
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

`POST /v1/notify` `{"title": "...", "message": "...", "category": "..."}` sends to Updates in the Arty phone and desktop apps. `category` is optional: one of `email`, `server`, `tasks`, `approvals`, `reminders`, `calendar`, `lectures`, `arty` or `other`; anything else, or none, is `other`. The apps let the user switch each category's phone notifications off. Every notification goes there and nowhere else (not Telegram or Discord).

## Announcements (what Arty tells you)

- `GET /v1/announcements?after=<id>&wait=<seconds>`: everything Arty has notified you about since `after` (reminders, briefings, task results, approvals, Uptime Kuma alerts), oldest first, as `{"announcements": [{id, kind, category, title, message, task_id, actions, created}], "last": <id>}`. With `wait` (up to 55 s) it waits for one to arrive. Call with `after=0&wait=0` first to learn `last`, then follow from there. `kind` is `reminder`, `approval` (with `task_id` and `actions`, e.g. `approve`/`deny`), `alert` or `message`. `category` is one of the `POST /v1/notify` categories, set by whatever sent it (announcements from before categories are `other`); older apps ignore it.
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
- `GET /v1/devices` lists the approved computers (`id`, `name`, `address`, `created`, `last_used`, `remote_control`; never the key). `DELETE /v1/devices/{id}` turns one key off at once.
- `POST /v1/devices/{id}/remote-control` with `{"allow": true | false}` lets that device follow and steer Arty CLI sessions (below). It is off for every new key, and only the brain's own keys may change it.
- A device key works everywhere the shared key does. Its `/v1/identity` proof is the HMAC keyed with the SHA-256 hex of the key.
- `POST /v1/devices` with `{"name"}` makes a named device key straight away and returns `{"id", "name", "api_key"}`; the key is shown only in this answer. Only the brain's own keys (`BRAIN_API_KEY` or `ARTY_API_KEY`) may call it: a device key gets 403. At most 100 device keys can be live at once (429 after that).
- `GET /v1/device/approver` (no key) returns `{"approve_page"}`: starfire's approve page when `BRAIN_STARFIRE_URL` is set, otherwise an empty string. The approve sheet uses it to send a browser with no Arty key to starfire.

## Arty CLI remote control

A computer running Arty CLI with `/remote` on shares that session through the brain, so a phone, the iPhone web app or starfire can follow it and steer it without any port open on the computer. Sessions live in memory only: a brain restart ends them.

Who may do what:

- **The computer** (the key that started the session) posts its events, reads its inbox and ends it. No other key can.
- **A controller** is the brain's own key, or a device key with `remote_control` turned on. Any other key gets 403.
- A controller can send text, answer a waiting permission request and interrupt. Nothing in the relay changes Arty CLI's permission mode.

The computer's side:

- `POST /v1/remote/sessions` with `{"name", "folder", "model", "mode"}` (and optionally `"title"`) returns the session (`id` is 16 hex characters, `title` is the name it is listed under: the title asked for, else the folder, else the computer's name, with a number added when another live session already has it, such as `greeter 2`). A key may share 5 sessions at once, 20 in all (429 after that).
- `POST /v1/remote/sessions/{id}/events` with `{"events": [...]}` (at most 50) adds to the transcript. Types: `user`, `assistant`, `tool_call`, `tool_result`, `permission` (with `request_id`), `permission_answered` (`request_id`, `allow`, `by`), `status` (`mode`, `text`), `error`, `compacted` (`text` is the summary the conversation was swapped for), `end`. Fields kept: `type`, `text`, `tool`, `request_id`, `by`, `mode`, `allow`, and `history` when it is `true` (the event is from before sharing began; a computer sends its conversation so far this way when it starts sharing); text is cut to 8000 characters and other fields are dropped.
- `GET /v1/remote/sessions/{id}/inbox?wait=` returns `{"inbox": [...]}` and empties it: `{"type": "message", "text", "by"}`, `{"type": "answer", "request_id", "allow", "by"}` and `{"type": "interrupt", "by"}`. `wait` (at most 25 s) holds the request until something arrives. A session whose computer hasn't called in for 10 minutes ends.
- `POST /v1/remote/sessions/{id}/title` with `{"title"}` renames it; the answer's `title` is the name it ended up with (numbered if taken). Readers get a `status` event.
- `DELETE /v1/remote/sessions/{id}` ends it.

A controller's side:

- `GET /v1/remote/sessions` lists live sessions, each with its `title` and `waiting_for` (the permission requests still open).
- `GET /v1/remote/sessions/{id}/events?after=&wait=` returns `{"events", "next", "missed", "ended", "session"}`. Each event has a `seq`; pass the last `next` as `after`. The brain keeps the newest 2000 events and at most about 2 MB of their text, and `missed` says some were dropped.
- `POST /v1/remote/sessions/{id}/messages` with `{"text"}` (up to 8000 characters) queues a message and shows it in the transcript as `remote_message`.
- `POST /v1/remote/sessions/{id}/answers` with `{"request_id", "allow"}` answers a waiting request. The first answer wins: a second one gets 409 naming who answered, and an answer given on the computer closes the request too.
- `POST /v1/remote/sessions/{id}/interrupt` stops the current turn, like Esc on the computer.
- An ended session answers 410 for a few minutes, then 404.

## Health

`GET /health` (no key) returns `{"ok": true}`.

## Arty for iPhone

`GET /ios/...` (no key) serves the iPhone web app's files from `apps/ios` (`ARTY_IOS_DIR` to move it): only known
file types, never hidden files or its tests, with a Content-Security-Policy that keeps the page talking to this
server only. `/ios` redirects to `/ios/`. See [apps/ios/README.md](../apps/ios/README.md).
