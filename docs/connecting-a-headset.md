<div align="center">

# Connecting a Quest Headset

**How Arty for Quest finds your voice server: a short setup code you type once, or, in the personal build, the long owner key if you prefer.**

</div>

---

## The Quick Way: A Setup Code

1. **Make a code on the voice server.** Run this where the voice assistant lives (for example on the Proxmox host):

   ```bash
   pct exec 109 -- /opt/ai-arty/quest-code
   ```

   On the voice server itself it is just `/opt/ai-arty/quest-code`. If the command isn't there yet, install it once
   from this repo's `voice` folder:

   ```bash
   pct push 109 voice/quest-code /opt/ai-arty/quest-code --perms 755
   ```

   It prints one code, like `K7Q9-XM3P`. The script
   reads the voice server's own key from `/etc/arty-voice/secrets.env`, so only someone who already controls the
   server can make a code. Nothing secret is printed.
2. **Open Arty on the headset.** On a first launch the orb panel shows **Connect to Arty**. Later, tap **Server**
   next to the wake-word switch.
3. **Fill in two boxes** with the headset keyboard:
   - **Server address**: your voice server's address, like `192.0.2.16`. The port (8790) and the rest are added for
     you. Use the full `wss://...` address instead if the server is reached over the internet.
   - **Setup code**: the code from step 1. Capitals, spaces and the dash don't matter.
4. **Tap Connect.** The headset hands the code and its model name to the server, which answers with a key and id for
   this headset alone. Arty saves them encrypted on the headset and connects. You never see or type the long key.

## What the Code Does and Doesn't Do

- It works **once** and runs out after **10 minutes**. Make a new one if you need to try again.
- Up to five codes can be waiting at a time. Making a sixth cancels the oldest.
- Ten wrong codes from one device within 5 minutes make the server refuse codes from that device for **5 minutes**, and
  100 wrong codes from all devices together pause every code for 5 minutes, so a stranger can't guess their way
  in. Wrong guesses never reveal anything.
- The code is only a short-lived way to hand over a headset key. Whoever redeems it gets a working key, so treat a
  live code like a password: don't leave it on screen or in a chat.
- Each headset paired by name has its own key. On the voice server, `GET /pair/devices` with the owner key lists them
  and `POST /pair/forget` turns one off without touching the others (details in [voice/README.md](../voice/README.md)).
  Once every headset has paired by name, set `HEADSET_LEGACY_PAIRING=0` so codes stop handing out the shared key.
- A code only works on your **own** voice server. It can't be used anywhere else.

## The Long Key Instead

In the **personal** build, if you'd rather not use a code, type the **headset key** in the third box (**Or the long
headset key**) and leave the code box empty. The **shareable** build hides this box and only pairs with a code. The key is `HEADSET_TOKEN` in `/etc/arty-voice/secrets.env` on the voice server:

```bash
pct exec 109 -- grep '^HEADSET_TOKEN=' /etc/arty-voice/secrets.env
```

Run that in your own terminal, not a shared or recorded one. For your own headset you can also build the address and
key straight into the app, so nothing needs typing: see the Setup section in
[apps/beta/quest/README.md](../apps/beta/quest/README.md).

## Addresses

- Home network: a plain address like `192.0.2.16` or `arty-server.local` is enough. Arty uses `ws://`, which is only
  accepted for home-network addresses (private numbers and `.local`, `.lan` or `.home.arpa` names).
- Away from home: use a `wss://` address, so the key and the code aren't sent unencrypted.

## If Something Goes Wrong

| The headset says | What to do |
|---|---|
| Type the setup code... | The code isn't 8 letters and numbers. Check it against the server's output. Codes never contain the letters I, L or O, or the numbers 0 or 1. |
| That code isn't right or has run out | It was already used, is older than 10 minutes, or was mistyped. Make a new one. |
| Too many wrong codes | Wait 5 minutes, then try with a fresh code. |
| Couldn't reach the voice server | Check the address, that the headset is on the same network as the server, and that the voice assistant is running (`systemctl status arty-voice` on the server). |
| Arty refused that headset key | The key was changed on the server. Make a new code and connect again. |
