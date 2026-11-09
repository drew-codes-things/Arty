<div align="center">

# Arty for Quest

**Beta.** Arty in your headset, your own virtual reality AI.

**Say "Arty ..." to ask what a word means, the time, to search something up or to open an app. It answers out loud, with an orb that shows when it's listening, thinking and talking, and panels for your servers, weather, calendar and reminders that you can grab and move.**

[![Kotlin](https://img.shields.io/badge/kotlin-2.1-7F52FF?style=flat-square&logo=kotlin&logoColor=white)](https://kotlinlang.org/)
[![Meta Spatial SDK](https://img.shields.io/badge/meta%20spatial%20sdk-0.14-0467DF?style=flat-square&logo=meta&logoColor=white)](https://developers.meta.com/horizon/develop/spatial-sdk)
[![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-red?style=flat-square)](../../../LICENSE)

</div>

---

## Overview

A native Quest 3 / 3S app (Kotlin, Meta Spatial SDK, Jetpack Compose panels) in passthrough. It connects to ai-arty's
headset socket (`voice/assistant/headset.py`, e.g. `ws://192.0.2.16:8790/ws/headset`) with the headset key, streams the
microphone as 16 kHz mono PCM, and plays Arty's spoken replies. Wake word, speech-to-text, the brain and text-to-speech
all run on the server, so the headset only listens and plays.

| Panel | Shows |
|---|---|
| Orb (middle) | Pulses slowly when idle, faster while listening, thinking and speaking; what Arty heard; the wake-word switch |
| Servers (left) | Each host, container and VM with a green or red dot, refreshed every minute |
| Today (right) | Weather, the next week's calendar and upcoming reminders |

Pinch a panel to move it; it stays upright, between 0.5 m and 2.4 m high.

## What it's for

Quick answers while the headset is on. These are handled in code on the voice server
([`quick_answers.py`](../../../voice/assistant/quick_answers.py)), so they are fast and can't be made up:

| Say | Arty does |
|---|---|
| "Arty, what does ephemeral mean?" / "define ubiquitous" | Reads up to two meanings from a free dictionary ([dictionaryapi.dev](https://dictionaryapi.dev)); words it doesn't know, slang and phrases go to the brain instead |
| "Arty, what's the time?" / "what's the date?" / "what day is it?" | Answers from the voice server's clock |
| "Arty, search up Tesco opening times" / "look up ..." / "google ..." | Asks the brain to search the web and sums up what it finds |
| Anything else | Goes to the brain like any other Arty question |

## Headset commands

The server recognises these and the headset carries them out ([`headset_commands.py`](../../../voice/assistant/headset_commands.py)):

| Say | Headset does |
|---|---|
| "Arty, open YouTube" / "launch Beat Saber" | Opens the installed app with that name ("browser" finds Meta Quest Browser); if none matches, Arty says so |
| "Arty, play Timber by Pitbull on Spotify" | Asks the installed Spotify app to play it (search if it can't play directly) |
| "Arty, pause" / "resume" / "skip" / "previous" | Media keys |
| "Arty, turn it up" / "turn it down" | Media volume |

If Spotify isn't installed, Arty says "Please log in or install Spotify on the headset first." The app can't tell
whether Spotify is signed in, so a signed-out Spotify just opens on its login screen.

## Setup

> **Beta:** built and unit-tested, not yet tried on a headset.

The first time it opens, the orb panel asks for two things, typed with the headset's keyboard:

| Field | What to enter |
|---|---|
| Headset address | Your voice server's headset socket, e.g. `ws://192.0.2.16:8790/ws/headset` |
| Headset key | `HEADSET_TOKEN` from `/etc/arty-voice/secrets.env` on the voice server |

`ws://` is only accepted on your home network (private addresses and `.local`, `.lan` or `.home.arpa` names); use
`wss://` anywhere else, so the key isn't sent unencrypted. The details are saved in the app's private storage,
encrypted with a key that never leaves the headset's Android Keystore, and backups are off. **Server** next to the
wake-word switch changes them later, and a refused key opens the form again.

For your own headset you can build them in instead, from `~/.arty-android.properties`:

```
quest.headsetUrl=ws://192.0.2.16:8790/ws/headset
quest.headsetToken=<HEADSET_TOKEN>
```

Anyone with an APK built that way can reach your headset socket, so keep it private.

## Build and install

```
cd apps/beta/quest
ANDROID_HOME=~/Android/Sdk ./gradlew testDebugUnitTest assembleRelease
~/Android/Sdk/platform-tools/adb install -r app/build/outputs/apk/release/app-release.apk
```

The Quest needs Developer Mode on and a USB-C data cable; accept "Allow USB debugging" in the headset the first time.
On Debian 13 nothing else needs installing: systemd's `70-uaccess` rule already gives the logged-in user access to
adb devices. The app is in the library under "Unknown sources".

## Tests

`./gradlew testDebugUnitTest` covers the socket messages both ways, the action mapping, reconnect backoff, the setup
rules (which addresses and keys are accepted, and when the form reopens) and the microphone frame size (it must
match the server's 30 ms frames).
