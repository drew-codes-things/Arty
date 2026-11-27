# Arty 1.8.0 release notes

Arty 1.8.0 makes the Qwen3.6 35B the main model, keeps the smaller qwen3:14b out of the way unless you choose it, and lets you name a model right in a message. Builds for Linux, Windows and Android are attached.

## Changes you'll notice

- **A new main model.** Arty answers on the Qwen3.6 35B. It is the model every chat, tool turn and background job uses unless you pick another one.
- **qwen3:14b only when you pick it.** Arty never falls back to qwen3:14b on its own. When the main model can't answer, the reply says so plainly ("The main model couldn't answer just now ...") instead of moving to a smaller model. A model you picked that is no longer listed gets a one-line note instead of a quiet swap.
- **One retry on the main model.** A single server error on the main model is tried once more on the same model before Arty gives up.
- **Naming a model in a message.** In Arty Desktop, the phone apps and starfire's chat, "use qwen3:14b to summarise this" runs that turn on the named model. "From now on use X" or "switch to X" also moves the model picker to X. Only what you type counts, a name has to be a model's whole or friendly name, and when two models match Arty asks which one you meant.
- **Allow cards show < and > as written.** Text on an Allow card keeps its angle brackets instead of losing them.
- **Paging Allow cards.** When more than one Allow card is waiting, `<` and `>` (and the arrow keys) page between them in Arty Desktop, Arty for Android and the iPhone app.
- **Room changes.** A room turn the main model can't take now passes, rather than moving to another model by itself, and every refusal from the room's checks is logged with its reason.
- **Model pickers.** The picker has two tabs, Arty and Choose a model, with remote models in their own section, and replies show the model's friendly label instead of its raw id.
- **The old ArtyCode copy is gone.** The early copy of the coding assistant that lived inside the Arty repository has been removed. ArtyCode has its own repository and releases.

## Versions

| App | Version |
|---|---|
| Arty Desktop | 1.8.0 |
| Arty for Android | 1.8.0 (build 16) |
| iPhone app | 1.8.0 |
| Browser extension | 1.8 (unchanged) |
| Gmail reader | 1.6 (unchanged) |
| Quest app | 1.2 (build 3, unchanged) |

## Upgrading

Install Arty Desktop 1.8.0 and Arty for Android 1.8.0. The iPhone app arrives with the brain. The browser extension, the Gmail reader and the Quest app have no new version. Older apps keep working with this brain, but only the new ones can name a model in a message and show the new Allow card paging.

## Downloads

These are the shareable builds: they hold no server address or key, so you enter your own on first start.

| File | sha256 |
|---|---|
| Arty-1.8.0-x86_64.AppImage | `b979e95252527ec540c02b638dc75fbda7519bf8d9ffc80106b95fed5d2f9850` |
| Arty-Setup-1.8.0.exe | `19750365626cb892bb31c0d7712103e08394391e1c5f862f7fa9756d22821ea4` |
| Arty-1.8.0.apk | `60e2826f84a133c079ba50bb0c9fe3f24d911b0bb4bd3cebfb5fea63a39983ba` |
| Arty-Browser-Extension-1.8.zip (load unpacked after unzipping) | `8850af5efa96513e55d4641a508ca4c5c01a20cd4b63d464440da32bac15d1ad` |
| Arty-Browser-Extension-1.8.xpi (unsigned, Firefox ESR, Developer Edition or Nightly with `xpinstall.signatures.required` off) | `8850af5efa96513e55d4641a508ca4c5c01a20cd4b63d464440da32bac15d1ad` |

`SHA256SUMS.txt` is attached too.
