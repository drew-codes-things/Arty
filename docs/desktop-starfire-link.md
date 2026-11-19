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
