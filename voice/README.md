# Voice: my-voice hub and "Hey Arty"

The USB mic and the speakers are plugged into the Proxmox host. Only one container can hold the mic, so:

- **my-voice (CT 108, 192.0.2.15)** owns the mic and the speakers. `hub/voice_hub.py` streams the mic over a
  WebSocket (48 kHz, 16-bit mono) and plays anything the assistant says on the server speakers and on every linked PC.
  Its website, http://192.0.2.15:8780, has its own account: the first visit asks you to choose an email and
  password (`/var/lib/my-voice/accounts.json`, bcrypt), kept the same as your starfire.ai login. It gives each PC a
  one-file setup script with its own key. Keys are stored hashed and can be removed from
  the page.
- **ai-arty (CT 109, 192.0.2.16)** runs `assistant/arty_voice.py`. It listens to the hub's mic stream, uses VAD to
  find speech, checks the start of each phrase for "Hey Arty" with whisper `tiny.en`, transcribes the request with
  `small.en`, streams the answer from Arty's brain (CT 104, `ARTY_API_KEY`) and speaks it sentence by sentence with
  Piper (`en_GB-alan-medium`) through the hub's `/speak`. It ignores the mic while it is talking, and for 6 s after
  an answer it takes a follow-up without the wake word. Nothing it hears is logged unless the wake word was said.
- **Wake switch**: the "Hey Arty" switch on the my-voice page turns the wake word off and on (saved in
  `/var/lib/my-voice/wake.json`, pushed to the assistant over its mic stream). Saying "Arty off" / "Arty on" does the
  same by voice; while it's off the assistant only listens for "Arty on". The wake check primes whisper `tiny.en`
  with "Hey Arty. Arty on. Arty off." because unprimed it mishears the name in two-word phrases.

## Room speaker volume

Said to the room assistant, "louder", "quieter", "turn the volume down a bit", "volume to 40", "mute" and "unmute"
are handled straight after transcription without asking the brain: ai-arty calls `POST /assistant/volume` on the hub
(assistant device key only) with `{"change": "up"|"down"|"mute"|"unmute"}` or `{"level": 0-100}`, and the hub sets
the `Master` control of the onboard card with `amixer` in steps of 10 (`HUB_MIXER_CARD`, `HUB_MIXER_CONTROL`). An
empty body returns the current level. Master was found at 0% and muted on 2026-11-03, which silences the line-out.

## Quest headset

`assistant/headset.py` runs inside the same process on ai-arty, port 8790: `ws://192.0.2.16:8790/ws/headset`,
with `HEADSET_TOKEN` (in `/etc/arty-voice/secrets.env`) as a Bearer key. The headset streams its mic as 16 kHz
16-bit mono PCM and gets back Arty's voice (WAV), orb states (`idle`, `listening`, `thinking`, `speaking`, `off`) and
actions. Each headset has its own conversation and wake switch, and it also wakes on a bare "Arty ...". Commands for
the headset itself ("play ... on Spotify", pause, resume, next, previous, volume) are matched by
`headset_commands.py` straight after transcription and sent as actions, without asking the brain; the headset replies
with what to say ("Paused.", or "Please log in or install Spotify."). Everything else goes to the brain as usual.
`headset_panels.py` sends each headset a `panels` message on connect and every minute: servers (the brain's
`homelab__service_status` tool), weather (`weather` tool), the next 7 days of calendar events and the next reminders
(`/v1/calendar`, `/v1/schedules`), cached for a minute and shared between headsets.

## PCs

- Debian: run the downloaded `my-voice-setup.sh` as your normal user. It adds the input "Server mic (my-voice)"
  (a PipeWire/PulseAudio pipe source) and a user service. `--uninstall` removes it.
- Windows: install VB-CABLE first, then run `my-voice-setup.ps1`. The mic shows up as "CABLE Output". `-Uninstall`
  removes it.

## Deploy

| | my-voice (CT 108) | ai-arty (CT 109) |
|---|---|---|
| code | `/opt/my-voice/app` (copy of `hub/`) | `/opt/ai-arty/app` (copy of `assistant/`) |
| venv | `/opt/my-voice/venv` (aiohttp, bcrypt) | `/opt/ai-arty/venv` (aiohttp, numpy, faster-whisper, webrtcvad-wheels, piper-tts) |
| data | `/var/lib/my-voice` (user `myvoice`, 700): accounts, sessions, devices | models in `/opt/ai-arty/models`; keys in `/etc/arty-voice/secrets.env` (root, 600) |
| service | `my-voice.service` | `arty-voice.service` (user `arty`) |

The hub needs `/dev/snd` in CT 108 (`lxc.cgroup2.devices.allow: c 116:* rwm` and a bind mount) and the host udev
rules that make the mic and the onboard sound card mode 0666.

Tests: `python -m pytest voice/tests` (needs aiohttp, bcrypt, numpy, webrtcvad-wheels).
