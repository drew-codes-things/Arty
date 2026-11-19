# Arty 1.5.8 release notes, 19 November 2026

Arty 1.5.8 rolls up everything since 1.5.2: Arty Auto, a status bar, editable memory, a link to starfire Desktop and a long list of brain and safety fixes. Builds for Linux, Windows and Android are attached; the same files are on the Arty download page.

## Changes you'll notice

- **Arty Auto** picks the vision, code, agent or chat model for each message. Every reply carries a short report (model, role, steps, tokens, speed, context window) that the apps show in a status bar.
- **Editable memory.** Saved memories can be listed, edited, added and deleted from the Android, iPhone and Desktop apps.
- **Presentation mode** on Arty Desktop hides private models and addresses, for screen sharing.
- **A link to starfire Desktop.** Pairing uses a 6 digit code that is never sent: both apps prove they know it with SPAKE2, and Arty Desktop proves itself before the token is used. Allow cards show the whole step, and window-only screenshots name the real window and refuse when two match or something covers it.
- **Tighter Allow cards.** They refuse blank-looking characters, pairing shares are parsed strictly, and abandoned asks count toward the refusal pause.
- **Arty keeps its own instructions private.** Replies or tool calls that would repeat its set-up are held back, while text you or an app sent can still be quoted back.
- **Honest stop answers.** When Arty stops partway through an agent turn, it names the steps that did run.
- **Too-long prompts** get a clear reply instead of an error.
- **Checked code answers.** Code answers are tested against the question's own examples in the sandbox, which needs the code runner's shared token and firewall.
- **Model tools** (ai-use, ai-pull, ai-models, ai-toolless) talk to ArtyEngine; pulls show progress and can be stopped cleanly.
- **Arty for Android** is numbered 1.5.8 to match the desktop builds.

## Downloads

These are the shareable builds: they hold no server address or key, so you enter your own on first start.

| File | sha256 |
|---|---|
| Arty-1.5.8-x86_64.AppImage | `026a06cac172023b22ca6703d52fe738121a24c77bcfeb1d6a9654d3ddea37c8` |
| Arty-Setup-1.5.8.exe | `0e8b3d786be4d94d05848d7e7819319ade2a0a27dde88ce480d1ab63749657e9` |
| Arty-1.5.8.apk | `12f47d6217740fc1ca22946e29dc379dbae2a828a6e3897398e3c02baa571aeb` |

`SHA256SUMS.txt` is attached too. The brain and the iPhone app are not binaries: update the brain from this source and the iPhone app arrives with it.

