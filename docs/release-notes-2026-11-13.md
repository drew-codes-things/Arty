# Arty 1.5.0 release notes, 13 November 2026

Everything added and fixed since 1.4.0: Arty can now use your browser properly (scroll, look, find, and Firefox, Edge
and Brave as well as Chrome), the apps have a new look, and the brain gives the models a bigger window and stops
going quiet after browser steps.

## Changes you'll notice

- **Arty for Chrome is now version 1.4 and works in Firefox, Edge and Brave too.** After updating, reload it on its
  card in your browser's extensions page; Firefox needs the pairing code once. Arty Desktop 1.5.0 is needed for the
  new browser steps.
- **The browser extension is on the Releases page.** `Arty-Browser-Extension-1.5.0.zip` is for Chrome, Edge, Brave
  and Chromium (unzip it and choose **Load unpacked**). `Arty-Browser-Extension-1.5.0.xpi` is for Firefox. It is not
  signed by Mozilla, so normal Firefox only loads it as a temporary add-on; Firefox ESR, Developer Edition and
  Nightly can install it once `xpinstall.signatures.required` is `false`.
- **The models get a 24,576-token window** instead of 12,288. `provision.sh` sets the same size for Ollama, so
  models are no longer loaded twice at two sizes.
- **New settings in `brain.json`:** `agent_options` (sampling for coding agents, per model) and `model_windows` (a
  window size for one model, off by default).

## Brain

### New
- **A bigger window.** Every model gets 24,576 tokens, and `/v1/models?all=1` says how big the window is, so coding
  clients such as Arty CLI know when to shorten a long conversation.
- **Coding agent turns use their own settings.** They use each model's recommended sampling, a longer reply limit
  and the same thinking choice for the whole session, instead of the chat settings.
- **Agent turns report how long each step took** on the GPU, including whether a model had to be loaded.

### Fixed
- **No more empty replies after browser steps.** When Ollama dropped a tool call the model asked for, the brain now
  recovers it instead of answering with nothing. If a reply is still empty after steps on your computer, Arty says
  how far it got instead of "I lost my train of thought".
- Steps Arty already took in your browser count as using tools, so an honest answer after them is no longer
  questioned.
- Long chats and background tasks are shortened to fit the window before they are sent, so a reply is not cut off
  after a few words.
- An agent conversation that does not fit the window is refused with a clear message instead of being cut silently.
- A reply cut off by a full window is told apart from one that hit the reply limit.
- The coding model stays loaded at one size for a whole session, so steps do not pay for a reload.
- Questions about a page, a tab or the screen ("what's on this page?") no longer go to the calendar or the service
  status lookups.
- "What time is it in ..." only looks up a time for a clock question naming a place, reads place names from current
  time zones, and counts from the brain's own time zone.
- The honesty check leaves the brain's own fallback sentence alone.

## Desktop app

### New
- **Browser control is much more capable.** Arty can scroll a page (`chrome_scroll`), look at what a tab shows
  (`chrome_look`, never while a password or card field has something typed in it), and search a page for an item by
  its words (`chrome_find`).
- **Pages are read the way you see them.** A page's main results come first, with ads, menus and short videos
  marked, so Arty clicks the first video and not a sidebar link.
- Arty now reads a page after it finishes drawing, and says whether a video on it is playing, loading or stopped.
- The window, the Android app, the iPhone app, the my-voice pages and the browser extension's popup share
  starfire.ai's look: its cool greys, buttons, fields, menus and motion.

### Fixed
- The "tools you used" note no longer shows up inside Arty's replies.
- When the same step keeps repeating, Arty stops and says what it kept doing instead of going quiet.
- Tab 0 means the tab in front, and a tab number that isn't open is an error Arty can fix instead of a question
  for you.
- Asked about the open tab or page, Arty looks in the browser again instead of answering from earlier in the chat.
- Item numbers from an old read of a page are refused, so a click cannot land on something no longer shown.
- Scrolling keeps a paragraph's words on one line.

## Browser extension (version 1.4)

### New
- Works in Firefox, Edge, Brave and Chromium as well as Chrome.
- Scrolling, looking at a tab and finding items, for the new desktop steps above.
