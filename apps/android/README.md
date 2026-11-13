<div align="center">

# Arty for Android

**Arty in your pocket - chat by text or voice, get reminders and briefings in Updates, approve what it's waiting on, and see what it's working on, from anywhere.**

[![Kotlin](https://img.shields.io/badge/kotlin-2.0-7F52FF?style=flat-square&logo=kotlin&logoColor=white)](https://kotlinlang.org/)
[![Jetpack Compose](https://img.shields.io/badge/jetpack%20compose-ui-4285F4?style=flat-square&logo=jetpackcompose&logoColor=white)](https://developer.android.com/jetpack/compose)
[![Android](https://img.shields.io/badge/android-9%2B-3DDC84?style=flat-square&logo=android&logoColor=white)](https://developer.android.com/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](../../LICENSE)

</div>

---

## Overview

A native Android app (Kotlin + Jetpack Compose) for Arty, the self-hosted assistant at the root of this project. It
talks to Arty's brain over its API ([docs/API.md](../../docs/API.md)) - at home on the LAN, or from anywhere through
a Cloudflare tunnel locked down with Cloudflare Access ([docs/cloudflare.md](../../docs/cloudflare.md)). Every
request also carries the brain's API key.

It comes in two builds from the same code:

| APK | Package | Where it finds Arty |
|---|---|---|
| `release/Arty.apk` | `app.arty` | Built in from `~/.arty-android.properties` - the owner's own copy. **Keep it private**: anyone with the file can reach your Arty. |
| `release/Arty-<version>.apk` (e.g. `Arty-1.1.0.apk`) | `app.arty.shareable` | Nothing built in. It asks on first launch, so one APK works for anyone's Arty and is safe to share. |

Both can be installed side by side.

---

## How It Works

```
Open the app
        |
Shareable build, first time  ->  "Connect to Arty": Scan QR code (from Arty Desktop's tray menu > Phone setup code),
        |                        or Enter details manually: brain address, API key and the Cloudflare Access token
        |
Test request to the brain  ->  only saved once it works
        |
Chat / Updates / Tasks / Calendar / Lectures / Schedules / Memory
        |
Live connection  ->  new Updates arrive as notifications straight away
```

The shareable build keeps the connection details in the app's private storage, encrypted with a key held in
Android's Keystore that never leaves the phone. Backups are off, so they don't go to the cloud either. Change them
later from the chat screen's speaker menu > **Brain connection**. The personal build starts with its built-in details
and has the same menu: a connection saved there replaces the built-in one. Where each detail comes from, and how the QR code
works: [docs/connecting-a-phone.md](../../docs/connecting-a-phone.md).

Plain `http://` is only accepted for home-network addresses (`192.168.x.x`, `10.x.x.x`, `.local` and the like), so
the API key never crosses the internet unencrypted. Anything else needs `https://`.
Before each request to a plain `http://` address, the brain there has to prove it knows the key by answering a random
challenge at `/v1/identity`; neither the key nor the Cloudflare Access id and secret are sent with that challenge. The
check assumes `/v1/identity` is only reachable on your home network or behind Cloudflare Access: if it were open to the
internet, a fake server on another Wi-Fi could pass the challenge on to the real brain.

---

## Features

- **Chat** - type or tap the mic, with replies read aloud if you like. Past chats and the model picker are in the
  drawer, and the paperclip attaches photos and files.
- **Calls** - a hands-free voice call with Arty, from the app or the home-screen widget.
- **Updates** - everything Arty sends you: reminders, the morning and evening briefings, task results and homelab
  alerts, with Approve / Deny on anything waiting for you. New ones also arrive as notifications. On the lock screen
  an approval notice only says Arty needs your answer; Approve and Deny buttons on the notice itself appear on
  Android 12 and later, which asks for the unlock first. On older Android, tap the notice and decide in the app.
  A new question in the app shows a short **New question** label and its buttons stay off until it has been on
  screen for one second; a button on a notice is ignored in the first second after the notice appears.
- **Tasks**, **Schedules** and **Memory** - what Arty's working on, what's planned, and what it remembers about you.
- **Calendar** - your events, and adding new ones.
- **Computers** (under More) - follow Arty CLI on a computer that ran `/remote`: read what it's doing, answer its
  permission questions with Allow or Deny, send it a message, or Stop the current turn. A phone with its own device key
  needs **Arty CLI** switched on for it on starfire's Arty devices page first.
- **Lectures** - record a lesson (or pick a recording) and read the notes Arty writes from it.
- **Notifications** (under More) - a switch for each kind of notification: email, server and homelab, background
  tasks, approvals, reminders and deadlines, calendar, lectures, Arty's own notices and other. A switched-off kind makes
  no phone notification and no sound but still appears in Updates. Each kind is also its own Android notification
  channel, so Android's own settings (sound, pop-up, off) work per kind; a kind turned off there is flagged in the app,
  and if Arty's notifications are off altogether (or the permission is denied) the page warns at the top.
- **Share to Arty** - send text or files to Arty from any other app.
- **Widget and quick tile** - talk to or call Arty without opening the app.

---

## Download

Download `Arty-<version>.apk` (e.g. `Arty-1.1.0.apk`) from the [Releases](../../../../releases) page, or build it into `release/` from source:

- Over USB: `adb install -r Arty-1.1.0.apk` (or `release/Arty.apk` for your own copy, which has your keys built in:
  never share that one).
- Or copy it to the phone, open it and allow installing from that app when Android asks.

Android 9 (API 28) or newer.

## Build

```bash
./gradlew :core:test                      # API client tests against a local fake server
./gradlew :app:testPersonalDebugUnitTest  # theme colours, list arrivals and reduced motion
./gradlew :app:assembleShareableRelease   # app.arty.shareable: asks on first launch
./gradlew :app:assemblePersonalRelease    # app.arty: details built in (see below)
./gradlew :app:assemblePersonalDebug      # app.arty.debug, installs alongside the release build
```

The APKs land in `app/build/outputs/apk/<build>/release/`; copy them to `release/` (which isn't committed), naming the
shareable one after its version, e.g. `Arty-1.1.0.apk`.

The personal build reads its details from `~/.arty-android.properties` (mode 600), never from this repo:

```
arty.baseUrl=https://arty.example.com
arty.apiKey=...
cf.accessClientId=....access
cf.accessClientSecret=...
```

Release builds are signed with `~/.arty-release.jks` (password in `~/.arty-release.pass`).

---

## Tech

- `core/` - `ArtyClient`, a plain-Kotlin client for the brain API (no Android dependencies), with its tests.
- `app/` - the Jetpack Compose UI. `ArtyViewModel` holds state and actions; chats are JSON files in the app's
  private storage (`ConversationStore`), and a reply still being written survives the app closing.
- `Connection.kt` - where the app finds Arty: the Keystore-encrypted details saved by `SetupActivity` when there are
  any, otherwise the personal build's built-in ones.
- Chat turns run as brain jobs that the app polls, because Cloudflare ends any request that waits more than 100
  seconds. The wait rides out a dropped connection, 429, 500, 502 to 504 and Cloudflare's 520 to 530 (`ChatWait`).
- `ui/Theme.kt` and `ui/Controls.kt` - the look shared with starfire.ai: colour, shape and motion tokens, and the buttons, fields, cards
  and list arrivals every screen shares. Motion stops when the phone's animations are turned off.
- `design/` - the Arty logo source and icon previews. Fonts are IBM Plex Sans and Mono (SIL Open Font License).
- Toolchain: Gradle 8.10.2, AGP 8.7.3, Kotlin 2.0.21, JDK 21, compileSdk and targetSdk 35, minSdk 28.

## Tested On

- Realme RMX5085 on Android 16 - the personal release build, through the Cloudflare tunnel.
- The shareable build's first-launch setup on the Android emulator (Android 15, API 35).
- Built on Debian 13 (trixie) with JDK 21.

## Revoking Access

If a phone with the app is lost: delete its service token in Cloudflare Zero Trust > Access controls > Service credentials,
and/or rotate `ARTY_API_KEY` in `/srv/ai/secrets/brain.env` on the brain. A shareable install then just asks for the
new details; the personal build needs rebuilding with them, or a new code scanned under **Brain connection**.

## License

[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) - see [LICENSE](../../LICENSE) and [NOTICE](../../NOTICE). You can use, change and share Arty,
including commercially; if you share it or anything built from it, keep the LICENSE and NOTICE files, which credit Arty's author, and say what you changed.

The IBM Plex fonts are under the SIL Open Font License.
