[Arty](../README.md) / [Docs](README.md)

# How Arty Works

```
 Arty Desktop (your PC) ───────LAN─────────┐
 Arty for Android ───LAN or Cloudflare─────┤                       ┌─▶ ArtyEngine (models, GPU)
 Arty for iPhone ────LAN or Cloudflare─────┤                       │
 Arty for Quest (beta) ─▶ voice server ────┼─▶ Brain (:8081) ──────┼─▶ Tool hub: memory, web search, homelab, ...
 Voice assistant ("Hey Arty") ─────────────┤                       │
 Telegram / Discord bots ──────────────────┘                       └─▶ Arty's PC (VM): browser, Gmail window, terminal
```

Everything talks to the brain through one API ([API.md](API.md)) with the Arty API key. At home the apps
use the brain's LAN address; away from home the phone apps go through a Cloudflare tunnel behind Cloudflare Access
([cloudflare.md](cloudflare.md)). Notifications - reminders, briefings, task results, approvals, homelab
alerts - only ever go to **Updates** in the phone and desktop apps.

The brain and its tool servers send their model requests to the API of [ArtyEngine](https://github.com/drew-codes-things/ArtyEngine)
at `ARTY_ENGINE_URL` (`http://127.0.0.1:11438` unless set). ArtyEngine is Arty's own model engine: it downloads models by name or
from Hugging Face, and its README covers installing it. It runs on an NVIDIA card; its AMD support is untested.
ArtyEngine is a separate repository with a different licence: [PolyForm Strict 1.0.0](https://github.com/drew-codes-things/ArtyEngine/blob/main/LICENSE)
(noncommercial use, no sharing or changed versions), not Arty's Apache 2.0.
