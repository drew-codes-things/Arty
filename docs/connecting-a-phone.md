[Arty](../README.md) / [Docs](README.md)

<div align="center">

# Connecting a Phone

**Link Arty for Android or Arty for iPhone by scanning a QR code from Arty Desktop. Nothing to type, and the code holds no keys.**

</div>

---

## The Quick Way: Scan the Link Code

1. On the computer, open **Arty Desktop**, then its tray menu (the Arty icon by the clock) and choose **Link a
   phone…**. Arty's window shows a QR code and the list of phones already linked.
2. On the phone, open Arty and tap **Scan QR code**:
   - **Android**: on the **Connect to Arty** screen, or later in the chat menu's **Brain connection**. The scanner
     is Google's, so the app needs no camera permission.
   - **iPhone**: on the first screen. If Arty is already set up, use **More > Forget the keys on this iPhone**
     first. Allow the camera when asked.
3. **Android** asks "Link this phone with Arty at *address*?". Check it's your Arty, then tap **Link**. The
   **iPhone** links straight away, because it only talks to the place it was opened from.
4. Arty's window says **Linked** with the phone's name, and the phone is in the **Linked phones** list. Tap **Done**.

### Why the code is safe to show

- The code holds the brain's address and a **one-use offer**, nothing else: no API key, no Cloudflare secret.
- It works **once**, and only for **two minutes**. A photo of it taken later, or a second phone scanning it, gets
  "This code has run out or was already used".
- The phone proves it read the code without ever sending the code: the phone and Arty each work out the same new
  key from it (the same kind of pairing Arty Desktop uses to link starfire). The key itself never crosses the
  network. Something that pretends to be Arty can't finish the link, and the phone refuses its answer.
- The phone gets **its own key**, so it can be cut off on its own (below) without touching any other device.
- If the computer reaches Arty through Cloudflare, Arty hands the phone the Cloudflare service token only after
  the phone has proved it scanned the code. The token is never in the QR code.

Where the key is kept: on Android, encrypted with a key that never leaves the phone's Keystore; on the iPhone, in the
Home Screen app's own storage, as before.

### Removing a phone

In Arty Desktop, tray menu > **Link a phone…** > **Linked phones**: tap **Remove** and then **Tap again to remove**.
The phone can't use Arty from that moment. Its chats stay on the phone. Phones also show in the iPhone app's
**More > Approved devices**, where **Turn off** does the same.

If a phone is lost, remove it there. Nothing else needs changing: no key was shared with it.

## Linking Away From Home (Cloudflare)

The phone does the link through the brain's public address (`https://arty.<your domain>`) when the computer has one.
Cloudflare Access blocks anything without the service token, and a phone that has not linked yet has none, so the
link page needs its own small Cloudflare rule. Do this once:

1. Cloudflare dashboard > **Zero Trust** > **Access controls** > **Applications** > **Add an application** >
   **Self-hosted**.
2. Name it "Arty phone link". Domain `arty.<your domain>`, path `link/phone`.
3. Give it one policy with action **Bypass** for **Everyone**.

Only `/link/phone/...` is opened. Those two steps take nothing but the one-use offer, refuse anything that can't
prove it scanned a code, and stop after five tries per code and thirty a minute. Everything else, including
`/v1/...`, stays behind the "Arty API" application and its service token. See [cloudflare.md](cloudflare.md).

Without that rule:

- **Android** tries the public address, and when Cloudflare stops it, tries the home address in the code instead.
  So linking works on your home Wi-Fi, and the phone still saves the public address for later.
- **iPhone** can only link through the address it was opened from, so it needs the rule, or open Arty from the home
  address on your home Wi-Fi to link.

## Typing Them In Instead

Choose **Enter details manually**. **Where do I find these?** under the fields gives this list in short.

### Brain address (Android only)

- Away from home: `https://arty.<your domain>`, the hostname routed through the tunnel ([cloudflare.md](cloudflare.md)).
- Home network only: `http://<AI_ADDRESS>:8081`.

### App key (`ARTY_API_KEY`)

On the machine running the brain, `provision.sh` saves it to `/srv/ai/secrets/arty-api.txt`. From the Proxmox host:

```bash
pct exec 104 -- cat /srv/ai/secrets/arty-api.txt
```

A computer with Arty Desktop connected has it as `api_key` under `brain:` in its `arty_config.yaml`
(`~/.config/Arty/` on Linux, `%APPDATA%\Arty\` on Windows). Run these in your own terminal, not in a shared or
recorded one. A phone set up this way shares that key, so it can't be removed on its own: the link code is better.

### Cloudflare Access client ID and secret

These are a Cloudflare Access **service token**:

1. Cloudflare dashboard > **Zero Trust** > **Access controls** > **Service credentials** > **Service Tokens**.
2. The **Client ID** is shown next to the token's name.
3. The **Client Secret** is only shown once, when the token is made. Arty Desktop keeps a copy as
   `access_client_secret` (and the ID as `access_client_id`) in `arty_config.yaml`.
4. If the secret is lost, open the token's menu and choose **Rotate secret**. You can keep the old secret working for
   a while (one hour to 30 days), so the other apps keep working until you've given them the new one. Or make a new
   token and add it to the **Arty API** application's **Service Auth** policy (Access controls > Applications).

### Older setup codes

starfire's **Make a key** and older Arty Desktops show a code with a key in it. The phones still read those. Treat
such a code like a password: don't photograph it or show it on a shared screen.

## Asking the Phone to Do Things

Once linked, Arty can ask the phone to do a few things. Arty only suggests them; **the phone shows its own Allow
card with exactly what will happen, and nothing happens until you press the button on it**.

| Ask | Android | iPhone |
|---|---|---|
| "Text Sam saying I'm running late" | Card shows the words. You pick Sam from your own contacts on the card (or **Pick from contacts**), then **Send**. The first time, Android asks to let Arty send texts; say no and Messages opens filled in instead | Card shows the words; **Open Messages** opens them, and you pick who in Messages |
| "Set an alarm for 7am" | Card shows the time and label; **Set alarm** sets it in the Clock app | Not possible from a Home Screen app: Arty says so |
| "Set a timer for 10 minutes" | Card shows the length; **Start timer** | Not possible: Arty says so |
| "Open Spotify" | Card lists the matching apps on the phone; pick one, then **Open** | Not offered |
| "Call Mum" | Opens the dialler; you press call | Opens the call for a number |

Safety rules behind it:

- **Recipients come only from the phone.** Arty sends a name to search for, never a number to use: you choose the
  contact on the phone.
- **Only from a chat you are in.** Only Arty's own code adds these requests, only to the reply to a message you sent
  from the phone. Scheduled tasks and other background work never can, and if a model is tricked by a web page into
  writing one into an answer, the brain removes it before the phone sees it.
- The card ignores taps for its first second on screen ("New question"), so a tap meant for something else can't
  press it.
