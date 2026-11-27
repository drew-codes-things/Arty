[Arty](../README.md) / [Docs](README.md)

# What's In the Repository

| Folder | What it is |
|---|---|
| `brain/`, `mcp/`, `bin/`, `services/`, `tests/` | The brain - persona, memory, the model and tool loop, tasks, schedules, briefings, the Arty API and its MCP tool servers (the [README](../README.md) and these docs) |
| [`arty-pc/`](../arty-pc/) | Scripts for Arty's own computer - its browser agent, terminal, document reader and desktop look |
| [`apps/desktop/`](../apps/desktop/) | Arty Desktop - voice, a small window and hands on your Linux PC, with the AppImage in `release/` |
| [`apps/android/`](../apps/android/) | Arty for Android - chat, Updates, tasks, schedules and memory, with the APKs in `release/` |
| [`apps/ios/`](../apps/ios/) | Arty for iPhone - a Home Screen web app, served by the brain at `/ios/` |
| [`apps/beta/quest/`](../apps/beta/quest/) | **Beta.** Arty for Quest 3 and 3S, your own virtual reality AI - an orb and panels in passthrough, talking through the voice server |
| [**Arty CLI**](https://github.com/drew-codes-things/ArtyCode) | Arty in your terminal on Linux and Windows, its own repository (ArtyCode): reads, edits and runs code in your project, asking first. Runs on your Arty; `arty` starts it |
| [`voice/`](../voice/) | The room mic and speakers hub, and the "Hey Arty" voice assistant |
| [`extensions/gmail/`](../extensions/gmail/) | Chrome extension that lets Arty's PC read your Gmail |
| [`extensions/browser/`](../extensions/browser/) | Browser extension (Chrome, Edge, Brave, Chromium, Firefox; Opera and Vivaldi from the unreleased 1.6) that lets Arty Desktop use your own browser |
| [`chat-bots/telegram/`](../chat-bots/telegram/), [`chat-bots/discord/`](../chat-bots/discord/) | Arty on Telegram and Discord, owner-only |
| `doclib/` | The docs library: official documentation (Python, MDN, Node.js, Rust, Go, Java, C# from Microsoft's .NET docs, PostgreSQL, Docker, Git, Linux man pages, Proxmox, Debian and more) downloaded into a local search index that coding answers read first |
| `code-runner/` | The code runner: a small container that runs the code in Arty's coding answers in a fresh sandbox (no network, capped time and memory) so Arty can fix real errors before it answers |
| [`docs/`](README.md) | [The Arty API](API.md), [linking everything through Cloudflare](cloudflare.md), [connecting a phone](connecting-a-phone.md) and [connecting a Quest headset](connecting-a-headset.md), plus the guides listed in the [index](README.md) |

The beta apps are built and unit-tested. Arty for Quest has run on a real headset (it connects and shows its panels) but not every feature has been tried.
Arty CLI's Windows installer has been run on Windows 10 (PowerShell 5.1).
