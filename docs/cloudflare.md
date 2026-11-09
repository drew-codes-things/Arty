<div align="center">

# Linking Arty Through Cloudflare

**Reach Arty's brain from your phone anywhere, through a Cloudflare Tunnel locked down with Cloudflare Access - no open ports, and two keys on every request.**

[![Cloudflare](https://img.shields.io/badge/cloudflare-zero%20trust-F38020?style=flat-square&logo=cloudflare&logoColor=white)](https://www.cloudflare.com/zero-trust/)

</div>

---

## Overview

At home, everything reaches the brain on its LAN address (`http://192.0.2.13:8081`): Arty Desktop, the Telegram
and Discord bots and Open WebUI. The phone also needs it from anywhere, so the brain is published through a
Cloudflare Tunnel and locked down twice:

```
Arty for Android ──HTTPS──▶ Cloudflare ──▶ Access: "Arty API" ──tunnel──▶ cloudflared on core-sys ──LAN──▶ brain :8081
                           (arty.<your domain>)  service token only        (outbound only, no open ports)   API key checked
```

1. **Cloudflare Access** only lets through requests carrying the app's service token. Everything else is turned
   away at Cloudflare and never reaches your network.
2. **The brain** then requires its Arty API key (`Authorization: Bearer ...`), the same as on the LAN.

The tunnel only connects outwards from core-sys, so no ports are opened on the router.

---

## Setup

You need a domain on Cloudflare and a Zero Trust account (the free plan is enough).

1. **Tunnel.** Zero Trust > Networks > Tunnels > Create a tunnel (Cloudflared). Install `cloudflared` on a machine on
   the LAN (here core-sys, CT 100) and run the command it shows. That installs a `cloudflared` service with the
   tunnel's token, and the tunnel is managed from the dashboard, with no local config file.
2. **Route.** In the tunnel's Public Hostnames, add `arty.<your domain>` with service `http://192.0.2.13:8081` (the
   brain).
3. **Service token.** Zero Trust > Access > Service credentials > Create a service token, named e.g. "Arty Android
   app". Copy the Client ID and Client Secret now; the secret is only shown once.
4. **Access application.** Zero Trust > Access > Applications > Add > Self-hosted, named "Arty API", on
   `arty.<your domain>`. Give it one policy with action **Service Auth** that includes that service token. No other
   policies, so no one can log in with an email or a browser.
5. **The app.** In the shareable Android app's **Connect to Arty** screen, enter `https://arty.<your domain>`, the API
   key, turn on **Behind Cloudflare Access** and paste both halves of the token. (For the personal build, put them
   in `~/.arty-android.properties` instead and rebuild - see [apps/android/README.md](../apps/android/README.md).)
   The app sends them as the `CF-Access-Client-Id` and `CF-Access-Client-Secret` headers on every request,
   alongside the API key.

Check it: a request without the token gets Cloudflare's 401/403, and one with the token but no API key gets the
brain's 401.

### Arty for iPhone

Safari can't add the service token to the page itself, only to the requests the page makes, so the iPhone app's
files need their own Access application that lets them through:

6. **Access application for the page.** Zero Trust > Access > Applications > Add > Self-hosted, named "Arty for
   iPhone", on `arty.<your domain>` with path `ios`. Give it one policy with action **Bypass** for Everyone. A more
   specific path wins, so only `/ios/...` is affected: those are static files with no keys in them, and the brain
   only serves known file types from `apps/beta/ios` there. Everything else, including `/v1/...` and `/arty-files/...`,
   stays behind "Arty API" and the service token.
7. Open `https://arty.<your domain>/ios/` in Safari and follow [apps/beta/ios/README.md](../apps/beta/ios/README.md). Give the
   iPhone its own service token if you want to be able to revoke it separately.

Check it: `/ios/` loads without the token, and `/v1/models` still gets Cloudflare's 401/403 without it.

## Things to Know

- Cloudflare ends any request that waits more than 100 seconds, so the app starts chat turns as jobs and polls them
  (`/v1/chat/jobs`, see [API.md](API.md)). Anything else that goes through the tunnel should do the same.
- Updates use long-polling under that limit, so they arrive straight away through the tunnel too.
- Files Arty links to (`/arty-files/...`) are downloaded through the app's own connection, which carries the token.
  A browser can't open them from outside.

## Revoking Access

If a phone with the app is lost, delete its service token (Access > Service credentials): it stops working
immediately. Rotate `ARTY_API_KEY` in `/srv/ai/secrets/brain.env` on the brain too; the shareable app then asks
for the new key, and the personal build needs rebuilding with it.

---

## Tested On

`cloudflared` 2026.10.3 on core-sys (CT 100, Debian 12 bookworm). Used with Arty for Android on Android 16
(Realme RMX5085).
