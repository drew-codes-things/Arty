# Arty 1.7.0 release notes

Arty 1.7.0 tells you when a newer version is out. The apps you download from GitHub ask GitHub for the latest Arty release; the iPhone app and the owner's own builds ask the Arty site they belong to. When there is something newer, a small notice shows with a button to the release or download page. Builds for Linux, Windows and Android are attached with the browser extension 1.8.

## Changes you'll notice

- **New version notice.** Arty Desktop, Arty for Android, the iPhone app, the browser extension, the Gmail reader and the Quest app each check once shortly after they start and then at most once a day. When a newer version is out they show "Version X is available" with a way to open the release or download page in your normal browser. Nothing is downloaded or installed for you.
- **Where each app looks.**
  - Arty Desktop (AppImage and Windows) and the shareable Android app: the tag of the latest release of drew-codes-things/Arty on GitHub.
  - Browser extension: the `Arty-Browser-Extension-<version>.zip` attached to that release.
  - iPhone app: `/download/manifest.json` on the site it is served from.
  - Personal Android and Quest builds: `/download/manifest.json` on the Arty site they were built for (`arty.baseUrl` in `~/.arty-android.properties`). The shareable Quest build does not check.
  - Gmail reader: `/download/manifest.json` on the site named by `ARTY_SITE_ADDRESS` in `hosts.env`, which `deploy.sh` passes to it. Empty means no check.
- **Dismiss once per version.** Dismissing the notice hides it for that version only. The next version shows it again.
- **A switch to turn it off.** "Tell me about new versions" is on by default:
  - Arty Desktop: the tray menu.
  - Arty for Android: the speaker menu on the chat screen.
  - iPhone: More.
  - Browser extension: its pop-up.
  - Gmail reader: its options page.
  - Quest app: the orb panel.
- **What the check sends.** One plain request for the list of builds, over https only, with a five second limit. It sends no account, key, id or cookie; Arty Desktop, Android and the Quest app name only the app and its version. If the check fails (offline, GitHub's hourly limit, a broken answer) nothing is shown and one line is written to the app's log. In Firefox, the browser may add the extension's own origin (`moz-extension://` followed by an id Firefox picks for each install) to the update request; Arty does not send it, and the request carries nothing else about you. The Gmail reader's request to your Arty site likewise names the extension's own origin (`chrome-extension://` and its id), which Chrome adds because the request goes to another site.
- **The extension's badge.** While a newer extension is out, the extension's toolbar badge says NEW. A missing pairing still shows "!" first.

## Versions

| App | Version |
|---|---|
| Arty Desktop | 1.7.0 |
| Arty for Android | 1.7.0 (build 15) |
| iPhone app | 1.7.0 |
| Browser extension | 1.8 |
| Gmail reader | 1.6 |
| Quest app | 1.2 (build 3) |

## Upgrading

Install Arty Desktop 1.7.0 and Arty for Android 1.7.0, and load the browser extension 1.8. The iPhone app arrives with the brain. The Gmail reader 1.6 arrives with the next deploy to Arty's PC; set `ARTY_SITE_ADDRESS` in `hosts.env` first if it should check for new versions. Older apps keep working with this brain; they just don't show the notice.

## Downloads

These are the shareable builds: they hold no server address or key, so you enter your own on first start.

| File | sha256 |
|---|---|
| Arty-1.7.0-x86_64.AppImage | `36c65929278b9d5663ebbf2d3fecd1758ae00fa6839c3ca86f589b6665d0112b` |
| Arty-Setup-1.7.0.exe | `13138ac8843328a52a085334409ef49f757da9b2dd20e42f9d87d60dd84bb545` |
| Arty-1.7.0.apk | `21234bfeb39e7ec074102d30fc09c57161ce2be30e895e34aa4957d0b95f15d6` |
| Arty-Browser-Extension-1.8.zip (load unpacked after unzipping) | `8850af5efa96513e55d4641a508ca4c5c01a20cd4b63d464440da32bac15d1ad` |
| Arty-Browser-Extension-1.8.xpi (unsigned, Firefox ESR, Developer Edition or Nightly with `xpinstall.signatures.required` off) | `8850af5efa96513e55d4641a508ca4c5c01a20cd4b63d464440da32bac15d1ad` |

`SHA256SUMS.txt` is attached too.
