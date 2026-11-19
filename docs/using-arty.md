[Arty](../README.md) / [Docs](README.md)

# Using Arty

Day-to-day use of a running Arty: chatting, models, pictures, approvals, documents, memory and where things live.

- **Chat**: any of the apps, or your own code through [the Arty API](API.md).
- **In the browser**: [starfire.ai](https://github.com/drew-codes-things/starfire.ai) works with Arty. In starfire,
  Settings → Connections → "Add API provider" → "Other (OpenAI-compatible)", with the brain's address
  (`http://<AI_ADDRESS>:8081/v1`) and `ARTY_API_KEY`, then pick `Arty` (the main model; the old name `assistant-core` still works).
- **Models**: on the brain's container, `ai-pull <model>` downloads one (no arguments: suggestions), `ai-models`
  lists them and `ai-use <model>` switches Arty's main model. Or tell Arty "switch to <model>" in any app;
  any installed model that can use tools is accepted (list some in `arty_models` in `brain.json` to limit them). Only models that can use tools can run Arty, unless you run
  `ai-toolless on`. Hugging Face GGUFs work too: `ai-pull hf.co/<user>/<repo>:Q4_K_M`, and
  `ai-pull qwen2.5vl:7b --from hf.co/<user>/<repo>:Q4_K_M` installs that repository and quant under the short name
  given (the same source again downloads nothing; another source replaces the model once its download is checked).
  A download shows its progress,
  Ctrl-C (or stopping the script with a signal) stops it in the engine too, and it gives up after
  `AI_PULL_MAX_SECONDS` (6 hours unless set) or when nothing arrives for `AI_PULL_STALL_SECONDS` (5 minutes unless set).
- **Pictures**: `vision_model` in `brain.json` names the model that looks at pictures (`mimo-v2.6:9b`). If it isn't
  downloaded, the first downloaded Arty model that can see is used (never a solo model). The main model and the vision
  model don't fit on the 12 GB card together, so with `route_picture_chats` (on by default) a chat with plain `Arty`
  that brings a new picture is answered by the vision model too, when it can use tools, instead of swapping the main
  model back in for the answer. A picture uploaded through the apps starts the vision model loading while you type.
  Set it to `false` to keep picture answers on the main model.
- **Picking a model per job**: Arty CLI's agent mode runs on `coding_model` (`qwen3:14b`, the main model). For
  anything else the main model can hand a job to another model in `arty_models` with `ask_other_model`;
  `model_strengths` says what each one is best at. A model that isn't downloaded is never picked: Arty falls back to
  the coding model in agent mode, or the main model, and logs it.
- **Coding questions in chat**: `route_coding_chats` in `brain.json` (on by default when `coding_model` is set) sends a
  chat to `coding_model` when it picks plain `Arty` and is clearly about code: a fenced code block, a pasted traceback or
  compiler error, or asking to write, fix, debug, refactor, review or explain code, a script, a regex, a SQL query or a
  shell command. A short question only moves when the coding model is already loaded; a long paste, a whole app or a
  change across a project always does, so a quick question doesn't cost two model swaps. Every app gets it with no update, the reply still comes from Arty, and the main model is loaded again
  200 seconds later. A model picked by name, solo models and the private add-on are never moved; if the coding model
  isn't downloaded, the main model answers and the brain logs it. Set it to `false` to keep every chat on the main model.
  As shipped, `coding_model` is the main model, so coding chats stay on it; pictures still go to the vision model. A coding
  question gets its code in the reply: Arty doesn't write it to a file or run it, on its PC or yours, unless you ask it
  to run, test, save, install or build it.
- **Models on their own**: anything listed in `solo_models` in `brain.json` shows up in the apps' model pickers as a
  plain chat, with no Arty persona, tools or memory.
- **Approvals**: in chat Arty asks and acts when you say yes; for background tasks, Updates in the phone and desktop
  apps shows Approve and Deny. The rules are under `approval` in `brain.json`.
- **Documents**: put files in `~/library` on Arty's PC (or ask Arty to) and `search_library` finds them by meaning.
- **Changing how it behaves**: personality in `brain/persona.md`, instructions in `brain/system-prompt.md`, settings
  in `brain/brain.json` (in `/srv/ai/brain` on the container). Changes apply on the next message.
- **Memory**: each message gets the saved facts that match its words first, then the newest facts, up to 4,000
  characters. Every night at 04:00 a tidy-up moves facts saved twice, and facts a later fact replaces (a new address,
  a new job), out of memory into `/srv/ai/memory/archived-facts.jsonl`, with the reason. Nothing is deleted: say "undo
  the memory tidy-up" to put the last night's facts back. `memory_tidy` in `brain.json` takes `"enabled": false` or
  another `"hour"`.
- **Where things live**: memory in `/srv/ai/memory/memory.json`, past chats in `/srv/ai/memory/conversations/`,
  tasks and schedules in `/srv/ai/brain/state/arty.db`.

## Safety

- The Proxmox token reads status everywhere and can only snapshot Arty's PC. Background tasks snapshot the PC before
  using it (the newest 3 are kept; roll back in Proxmox > the VM > Snapshots).
- Pages with logins, CAPTCHAs or bot checks are reported as blocked and Arty asks you instead of getting around them.
- Secrets live in `/srv/ai/secrets` (root-only) and are never printed by the scripts.
