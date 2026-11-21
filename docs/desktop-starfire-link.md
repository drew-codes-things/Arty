[Arty](../README.md) / [Docs](README.md)

# Linking Arty Desktop to starfire Desktop

Part of [Arty Desktop](../apps/desktop/README.md). starfire Desktop on the same PC can pair with Arty and ask for computer steps.
**Link starfire…** in the tray menu shows a 6 digit code (one use, two minutes, five tries and it is gone). The code
is never sent: both apps prove they know it with SPAKE2 (`/link/pair/start`, `/link/pair/finish`) and each works out
the same token, which only opens `/link/*` on 127.0.0.1, is kept as a hash here, and is refused from any web page
(Origin, Fetch Metadata and Host are checked). `/link/proof` answers a challenge with an HMAC keyed by that hash, so
starfire Desktop can check it is talking to this Arty before it sends the token. Each step a linked app asks for
shows an Allow / Not now card first, whatever Arty would have done on its own, and needs **HANDS** on. The card shows
the whole step, every argument in full; a step that cannot be shown exactly (more than one line, over 1,000
characters, control, direction, invisible or blank-looking characters such as fillers and combining marks, a run of
more than three spaces, or arguments the tool does not take) is refused before anything is asked. After three
refusals, unanswered cards or cards the app stopped waiting for in a row, new steps are turned away for a minute. If
the app stops waiting, the card is withdrawn and a late Allow does nothing (the card says it was too late).
**Unlink** in the same screen, or in starfire Desktop, ends it. A linked app can also ask for a picture
(`/link/screenshot`, with an app name when it wants one app): where Arty can find the window (X11, or X11 windows
under Wayland, with `xprop`, `xwininfo` and `xwd` installed) it finds it before asking, and the card names that
window's real title and app; only that window is captured, by itself, so nothing on top of it is sent. The name has
to match the app, the whole title, or the title up to a " - " or " | "; when more than one window matches, nothing
is asked and the app is told to use the full title. If the window closes or changes its title before the Allow,
nothing is captured. On plain X11 a window with anything over it, a menu or tooltip included, is not captured (the
cover could show through). Otherwise the card says plainly that the whole screen would be sent. An app with no
window is reported as not found and nothing is captured, and a password manager is never looked at.

## Linking by itself

From Arty Desktop 1.6.0 and starfire Desktop 1.1.0 there is usually no code to type. While nothing is linked, Arty
Desktop leaves a one-use secret (32 hex characters) in `.arty_link_offer` in its data folder, readable only by you.
starfire Desktop, running as the same user on the same computer, reads it and pairs with exactly the same SPAKE2
exchange as the code (marked `local`), so the secret itself is never sent and both sides still prove they know it.
The secret is used up when the link is made, five wrong tries replace it, and Arty says in its window that starfire
Desktop linked by itself; the link screen shows "Linked with starfire Desktop. It linked by itself". Unlinking makes
a fresh secret. **Stop linking by itself** in the same screen removes the file and leaves only the code.
A web page cannot read the file, and other users on the computer cannot either. Any program running as you could
already read Arty's own token, so this gives such a program nothing it did not have.

## Capabilities and opening a page in your browser

From Arty Desktop 1.6.0, `/link/status` also lists what the linked app may ask for, so starfire Desktop can show it
and offer only what works right now: `screen.look` (a picture of the screen or one window, above), `browser.open`
(open a page in one of your browsers, with the names of the browsers whose Arty extension is connected) and Arty
Desktop's version. Each entry says whether it is available; with computer control off, nothing is.

`/link/browser` opens a page in one of your connected browsers. It takes exactly an action (`open`), an address and a
browser name, and refuses anything else. Only `http` and `https` addresses with a site in them are taken; an address
with a name and @ before the site, a backslash, an encoded site name, spaces, control or invisible characters, or more
than 2,000 characters is refused before anything is asked, never cut. The browser has to be connected (with one
connected, the name can be left out). The card names the browser and shows the whole address, and the page opens in a
new tab only after **Allow**. Nothing from the page goes back to the linked app: only which browser and which tab.
The same busy rule and one-minute pause after three refusals in a row apply as for the other steps.
