[Arty](../README.md) / [Docs](README.md)

# Features

- **Local models** - runs on ArtyEngine on your own GPU; switch models with one command or by asking.
- **Real actions, checked in code** - email, calendar, reminders, deadlines, dates, sums, units, currency and server
  status are handled by code before the model sees them, so the model can't make results up.
- **Honesty check** - a reply that claims it did something when no tool ran (or every tool failed) is replaced with
  what really happened.
- **Briefings** - a morning and evening summary built from your real servers, weather, calendar, reminders,
  deadlines and email, with empty sections left out.
- **Its own PC** - a Debian + XFCE virtual machine with a visible browser, a shared terminal you can watch, a
  searchable `~/library` of documents, and a desktop look it picks itself.
- **Approvals** - anything that sends, buys, deletes or touches the homelab, or installs software on Arty's PC, waits
  for your Approve / Deny.
- **Checked coding answers** - code questions read the matching section of the local docs library (or the web) first,
  the code comes back in the reply, and with the optional code runner it is run in a sandbox and fixed before you see it.
  For C, Arty also writes small checks from the examples in your question, runs them against the code (with the memory
  checker on), and fixes the code when it crashes or a result the question states comes out wrong. Other languages can
  be listed in `example_checks` in brain.json; they are off by default because the checks slowed answers without
  making more of them right.
- **Safe browsing** - logins, CAPTCHAs and bot checks are reported back to you, never got around.
- **Everywhere** - desktop app, Android, iPhone and Quest (beta: your own virtual reality AI), voice, Telegram and Discord, all sharing one memory.
- **Works with [starfire.ai](https://github.com/drew-codes-things/starfire.ai)** - add Arty to starfire as an
  OpenAI-compatible provider and chat with it in the browser, next to the plain models on your engine.
  starfire.ai is getting its own update too: a cleaner look with every control styled in both themes, and
  the layout and features of the big chat apps (chat history in a sidebar, a Stop button, copy, edit and
  regenerate on messages, code blocks with a Copy button, renaming and searching chats, tabbed settings and
  keyboard shortcuts).
