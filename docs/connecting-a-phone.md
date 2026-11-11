<div align="center">

# Connecting a Phone

**How Arty for Android and Arty for iPhone get the brain's address, the API key and the Cloudflare Access token: scan a QR code, or type them in.**

</div>

---

## The Quick Way: Scan the Setup Code

1. On the computer, open **Arty Desktop**, then its tray menu (the Arty icon by the clock) and choose **Phone setup
   code…**. Arty's window shows a QR code made from the connection this computer already uses.
2. On the phone, open Arty and tap **Scan QR code**:
   - **Android** (shareable app): on the **Connect to Arty** screen. Later, from the chat menu's **Brain
     connection**. The scanner is Google's, so the app needs no camera permission.
   - **iPhone**: on the first screen. If Arty is already set up, use **More > Forget the keys on this iPhone**
     first. Allow the camera when asked.
3. **Android** fills in the fields and names the address the details will be sent to. Check it's your Arty, then tap
   **Connect**. The **iPhone** connects straight away, because it only talks to the place it was opened from. Either
   way, the app checks the details with Arty before saving them.
4. Tap **Done** on the computer, or leave it: the code closes itself after two minutes.

The code holds your API key and Cloudflare token. Treat it like a password: don't photograph it, screenshot it or
show it on a shared screen. Only the Arty window can ask for it. The local service refuses everyone else, including
browser extensions.

The phone gets the address from anywhere (`https://arty.<your domain>`) when the computer has one, otherwise the home
address. The iPhone ignores the address, because it already talks to the place it was opened from.

## Typing Them In

Choose **Enter details manually** instead. **Where do I find these?** under the fields gives this list in short.

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
recorded one.

### Cloudflare Access client ID and secret

These are a Cloudflare Access **service token**:

1. Cloudflare dashboard > **Zero Trust** > **Access controls** > **Service credentials** > **Service Tokens**.
2. The **Client ID** is shown next to the token's name.
3. The **Client Secret** is only shown once, when the token is made. Arty Desktop keeps a copy as
   `access_client_secret` (and the ID as `access_client_id`) in `arty_config.yaml`.
4. If the secret is lost, open the token's menu and choose **Rotate secret**. You can keep the old secret working for
   a while (one hour to 30 days), so the other apps keep working until you've given them the new one. Or make a new
   token and add it to the **Arty API** application's **Service Auth** policy (Access controls > Applications).

A token per device lets you cut one device off without touching the others: see
[cloudflare.md](cloudflare.md#revoking-access).

## If a Phone Is Lost

Delete its service token in Zero Trust > Access controls > Service credentials, and rotate `ARTY_API_KEY` in
`/srv/ai/secrets/brain.env` on the brain. Then show the new setup code to the phones you still have.
