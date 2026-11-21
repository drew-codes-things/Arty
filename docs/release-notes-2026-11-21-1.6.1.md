# Arty 1.6.1 release notes, 21 November 2026

Arty 1.6.1 is a small update to 1.6.0: Arty can use models from starfire and other outside servers, shows when one of them is offline, and fixes several things found since 1.6.0, including two that affected Arty Desktop on Windows. Builds for Linux, Windows and Android are attached with the browser extension (1.7, unchanged). The same files are on the Arty download page.

## Changes you'll notice

- **Models from starfire and other servers.** Arty's model pickers now list models that starfire shares with Arty through an app key, and models from other servers that work like OpenAI's. They need no memory on your own graphics card, so picking one never unloads your local models.
- **Offline models are marked.** When the server behind an outside model can't be reached, the model stays in the list with "(offline)" after its name in Arty Desktop, the iPhone app and Arty for Android, instead of disappearing or failing without a reason.
- **Clearer model names.** Models shared through starfire get short names, and a model keeps its name when another connection is added.
- **Shared chat room replies.** Arty no longer answers "I ran out of time on this one" in the shared chat room when its model has to start up first. A room turn gets enough time for that and still answers before the room gives up waiting.
- **Describing Arty's personality.** Everyday descriptions of how Arty talks (for example "a dry sense of humour") no longer count as giving away its private set-up, so those replies are no longer cut. Real leaks are still caught.
- **Coursework help.** Asking for an essay or other written coursework gets the writing, not a flashcard or a quiz question. Flashcards and quizzes are still offered when you ask to revise, be tested ("test my knowledge") or study. Earlier refusals no longer get copied into later answers.
- **Arty Desktop on Windows.** Editing or overwriting an existing file now works (it used to fail on the Python that the Windows installer carries), and the check that stops commands from reading your private keys now works when your Windows user folder name ends in "$".
- **Models on their own.** A model listed in `solo_models` and chosen in a picker is used even when its name in `brain.json` has no tag.

## Upgrading

Update the brain from this source. Install Arty Desktop 1.6.1 for the Windows fixes and the "(offline)" mark, and Arty for Android 1.6.1 for the mark on the phone. The iPhone app gets it with the brain. Older apps keep working with the new brain.

## Downloads

These are the shareable builds: they hold no server address or key, so you enter your own on first start.

| File | sha256 |
|---|---|
| Arty-1.6.1-x86_64.AppImage | `4eaabcb25d127b8b11eb1617adbc6071c1e717fc5f8e285b4119f3eea8c1fc8d` |
| Arty-Setup-1.6.1.exe | `0297b4b73b963d18f15cbef38c851d41e2909aa29865922651ff029d2e77139d` |
| Arty-1.6.1.apk | `b0c0038c390b259278b7aff6fcfb5e39c5bcb8645ee911b92199bdd7c8275031` |
| Arty-Browser-Extension-1.7.zip (load unpacked after unzipping) | `b76342d71dc13701f3d55efae6280428dd8e5f069982d7237ea2be4550c1a4fe` |
| Arty-Browser-Extension-1.7.xpi (unsigned, Firefox ESR, Developer Edition or Nightly with `xpinstall.signatures.required` off) | `b76342d71dc13701f3d55efae6280428dd8e5f069982d7237ea2be4550c1a4fe` |

`SHA256SUMS.txt` is attached too. The brain and the iPhone app are not binaries: update the brain from this source and the iPhone app arrives with it.
