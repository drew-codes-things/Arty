# Arty 1.5.2 release notes, 13 November 2026

Notifications sorted by kind, with a switch for each, and approval cards that can't be answered by a stray tap.
Arty for Android 1.5.2 and Arty Desktop 1.5.2 carry the app side; the brain gets honest drafts, warm greetings,
fewer model swaps, a web search before answering code questions it can't know, and a list of smaller fixes.

## Before you update

- **Arty for Android changes its notification channels.** After the update, Android's settings for Arty list nine
  kinds (Email, Server and homelab, Background tasks, Approvals, Reminders and deadlines, Calendar, Lectures, Arty's
  own notices, Other) plus **Chat replies**. The old "approvals and alerts" channel is gone. Each new kind starts as
  loud or as quiet as the channel it replaces, so one you had silenced stays silenced. Open Arty once after updating
  and look at More > **Notifications**.
- **Update the brain to get the kinds.** The apps work with an older brain, but then only approvals, reminders and
  server alerts are sorted; everything else lands under Other.
- **The browser extension stays at 1.5.** It has not changed, so there is nothing to reload. It works with Arty
  Desktop 1.5.2, and `Arty-Browser-Extension-1.5.1.zip` and `.xpi` stay on the 1.5.1 release.
- **The iPhone app updates with the brain.** It is served by your brain, so its new switches and the approval hold
  arrive when you update the brain; there is nothing to install on the phone.

## Changes you'll notice

- **A switch for each kind of notification.** More > Notifications on Android and on iPhone has one switch per kind.
  A kind you switch off makes no notification and no sound, but still shows in Updates. Switching Approvals off warns
  that Arty will wait silently for your answers.
- **Android tells you when Arty can't notify you at all.** If Arty's notifications are off in Android's settings, or
  the permission was refused, the Notifications page says so at the top.
- **A new question can't be answered by accident.** A new approval card flashes **New question** and ignores taps,
  clicks, Enter and Space for its first second on screen, on Android, iPhone and the desktop. Approve or Deny on a
  notification is ignored in the first second after it appears.
- **Hello gets a proper hello.** A bare "hi" or "good morning" gets a warm greeting straight away, without waiting for
  a model. An answer to Arty's own question is never taken for a greeting.
- **Drafts stick to what you told Arty.** Prices, dates, times and specs it would have had to invent become
  `[placeholders]` with a "Fill in ..." line, and only the remembered facts that match the request are used.
- **Picture and short coding questions answer sooner.** A picture is answered on the vision model instead of
  swapping back, and a short coding question stays on the model already loaded; only big coding jobs load the coding
  model. `route_picture_chats: false` in `brain.json` puts picture replies back on the chat model.
- **The project is now under the Apache License 2.0.** See `LICENSE` and `NOTICE`.

## Arty for Android (1.5.2)

### New
- One notification channel per kind, so Android's own settings (sound, pop-up, off) work per kind, and a switch per
  kind under More > Notifications. A kind turned off in Android's settings is flagged in the app.
- The **New question** label and the one-second hold on approval cards in Updates, Tasks and Computers, and on
  Approve / Deny in a notification.
- A warning at the top of Notifications when Arty's notifications are off altogether.

### Fixed
- `<context>` and `<screen>` tags you type are sent as plain text, so they can't pass for the app's own notes.

## Desktop app (1.5.2)

### New
- Step questions (Allow / Not now) and paused task buttons flash **New question** and ignore clicks, Enter and Space
  for their first second on screen.

### Fixed
- After a **Not now**, Arty says plainly that you said no to that step, instead of talking about a restriction. A
  question nobody answered in time is no longer reported as a no.
- Page words in activity labels and approval questions can't close the app's notes early, and typed `<context>` and
  `<screen>` tags are sent as plain text.
- The bundled backend leaves out test and lint caches.

## iPhone app

- A switch for each kind of notification under More, and the one-second **New question** hold on Computers, Tasks and
  Updates.

## Brain

### New
- Every notification carries a kind from whatever sent it, and `/v1/announcements` returns it. `POST /v1/notify`
  takes an optional `category`; an unknown one counts as other. A fix Arty offers for a server is an approval.
- Questions about a recent framework version, a named function, an error message or a command's flags get a web
  search before the answer, instead of a guess. Arty also reads the top page (the official docs when the search finds
  them) and follows its names and code over what it remembers, and says so when nothing it found mentions the tool or
  function you named.
- Questions about a rate or price right now ("the Bank of England base rate", "the price of gold today") are looked up
  on the web, and researched answers know today's date, so the latest change is given as the current one.
- Each agent round says whether the brain asked the model to think.

### Faster
- Research on the desktop notes pages without thinking, opens the next page while it reads one, stops a fact question
  once two sources agree, and falls back to fetching the page after 25 seconds.
- The honesty check and the memory step reuse the chat's cached prompt, so the first words of a reply come sooner.

### Fixed
- Drafts keep your name, your hashtags, your reasons for wanting something and any words you gave in the request,
  instead of turning them into placeholders, and a placeholder already there is never wrapped twice.
- Web search uses more engines (Bing, Mojeek and Startpage as well as SearXNG's defaults), so one blocked engine no
  longer empties every search. `provision.sh` only writes this into a new server's
  `/srv/ai/searxng/settings.yml`; on an existing server, add the same `engines:` lines from `provision.sh` there and
  restart it (`docker restart searxng`).
- Arty declines writing a scam or phishing message, and still helps with dealing with one: filters, rules, replies and
  awareness emails.
- Reminders are cancelled by name, only reminders are touched unless you name a job, and "cancel all" lists them and
  asks. Reminders are listed from the schedule.
- The weather is for the place you ask about. Days, times and words like "us" are never taken for a town, so
  "forecast for Saturday" gives home's Saturday.
- A link you give is read, instead of Arty saying it can't browse.
- In a long chat, Arty says it can't see the part that was left out instead of guessing.
- "Send a text to Sam saying ..." is understood as a text.
- How-to questions ("how do I stop all python processes?") are explained, not run on your computer, unless you ask
  Arty to run them.
- Tool-call markup never shows up in a normal reply.
- Saved task history leaves out the prompt and the tool list.
- The desktop browser opens one page at a time, and none after research has stopped.
- `brain.py` is split into smaller modules. Nothing changes in how it behaves.
