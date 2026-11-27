[Arty](../README.md) / [Docs](README.md)

# Limitations

Know these before relying on it:

- **Small models are riskier** - models under about 7B follow instructions and Arty's rules less reliably and are
  easier to steer with text hidden in a web page, an email, a file or the screen (prompt injection). The approval
  checks in code still apply whatever the model, but with tools that act on your PC, browser and accounts a small
  model makes a wrong or manipulated action more likely, so don't use one as the main model. Arty runs on the
  Qwen3.6 35B (main, code and pictures), with `qwen3:14b` to pick by hand and `qwen2.5vl:7b` for its pictures; it was
  first built and tested on `qwen3:14b` (main) and `mimo-v2.6:9b` (code and pictures). All are in the recommended
  range; a bigger model is safer but not immune, so the approvals stay on either way.
- **It can still be wrong** - outside the things handled in code, it can misunderstand a request, pick the wrong
  tool or state something false with confidence. Check anything important, and read commands before running them.
- **Speed** - `qwen3:14b` writes about 33 tokens a second on an RTX 3060. Replies usually start within a few
  seconds, but the first message after the model has unloaded, or one that needs a new group of tools, can take
  20-50 seconds.
- **Short memory within a chat** - 24,576 tokens of context, so long chats are trimmed. Older chats and saved facts
  are recalled by search, and it can miss details.
- **One model on the GPU at a time** - looking at an image swaps the vision model in for that one look, then the main
  model loads back in the background (about a minute in all).
- **Limited actions** - it sends email, changes your own Starfire Calendar app and saves to your own Starfire Drive app only once
  they are connected and only after your yes to exactly what will happen ([details](acting-in-your-apps.md)); sent
  email can't be undone. It can't create VMs
  or change the homelab on its own, and installing software on its own PC (sudo, apt, global npm or pip installs and
  the like) waits for your approval.
- **Home network for most things** - away from home only the phone apps work (through Cloudflare). Arty Desktop,
  voice and the chat bots need the LAN, and nothing works while the brain's container is off. There's no cloud
  fallback.
- **One person** - it's built for a single owner; the bots and apps don't do multiple users.
