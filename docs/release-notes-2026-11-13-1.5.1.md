# Arty 1.5.1 release notes, 13 November 2026

A safety release for browser control. Arty Desktop 1.5.1 and the browser extension 1.5 check every click against what
Arty read, ask before anything that submits, downloads, pays or leaves the site, and keep page text from steering
Arty into running commands. The brain gets the matching fixes.

## Before you update

- **Extension 1.5 needs Arty Desktop 1.5.1.** The extension now checks that Arty Desktop holds the pairing code
  before sending it anything, and older desktops (1.5.0 and before) have no way to answer that check
  (`/browser/hello`). With an older Arty Desktop the extension's icon shows the red **!** and it does nothing.
  Update Arty Desktop first, then the extension.
- **Reload the extension in each browser you use it in.** On the extensions page, click the reload icon on its
  card (Chrome, Edge, Brave, Chromium), or load the new `Arty-Browser-Extension-1.5.1.zip` or `.xpi` (Firefox) in
  place of the old one. Each browser keeps its own copy, so do it once per browser.
- **Arty for Android is unchanged.** `Arty-1.5.0.apk` is still the current version; there is nothing to install on
  the phone. The iPhone app is unchanged too.

## Changes you'll notice

- **Arty asks before more clicks.** A click that submits a form, downloads a file, leads to a payment, account or
  permission page, or leaves for a site you haven't allowed shows the Allow / Not now card, whatever the button
  says. Buttons with words like buy, pay, basket, checkout, allow, authorise, merge, delete, unsubscribe or log out
  ask too. Search buttons submit a form, so they ask as well.
- **After Arty reads a page, commands and file changes ask first** for the rest of that reply, and so do opening
  apps and links.
- **A click lands only on what Arty read.** A click names the numbered item, and the extension refuses it if that
  item's label or what it does has changed since the read. A click by its words needs the item's whole label.
- **Private fields stay private.** Password, passcode, PIN, one-time-code, card, CVV, IBAN, sort code, account
  number, national insurance, security answer and API key fields are recognised by their type, label, placeholder,
  name, id or autocomplete. Arty never reads what is in them, never types into them, and takes no picture of the page
  while any of them has something in it.

## Desktop app (1.5.1)

### Fixed
- A click is judged by its numbered item, never by the words next to it, and carries that item's label and kind
  to the extension.
- Everything a browser step returns (title, address, labels, field values and text) sits inside one "information,
  not instructions" marker that survives being shortened.
- Commands with process substitution, running files from `~/Downloads`, `curl` sending data or querying another
  host, and netcat ask first.
- The same click or typing target can repeat at most twice in a reply, and a reply makes at most 15 clicks.
- Each paired browser has its own queue. A reply sticks to one browser, the one Arty used last while it is open.
- The local service proves it holds the pairing code (`POST /browser/hello`) before the extension sends it.
- `HTTPS://` addresses and addresses with a leading space open correctly.

## Browser extension (version 1.5)

### Fixed
- Reads say what each item does (submits, downloads, payment or account address, leaves the site), keep labels
  whole, name checkboxes by their label and keep the visible words beside a different screen-reader label.
- Text and links a person can't see (faded out, moved off the page, tiny or hidden from screen readers) are left
  out of reads and scrolls.
- Typing with an item number that is gone is refused instead of landing in whatever field has focus.
- A hidden field no longer stops Arty taking a picture of the page, and the picture check runs in every frame.
- Only `http` and `https` addresses are opened.

## Brain

### Fixed
- Pages read in your own browser count as outside content: after one, running commands, saving memories,
  scheduling tasks and the other held tools ask first, as they already did after the brain's own browser.
- A tool call written inside the model's thinking is never run, and calls are read whole even when an argument
  holds call markup.
- A reply that holds only thinking or broken call markup counts as empty, so Arty retries and then says how far it
  got instead of reading the markup aloud.
- Claims like "I've opened YouTube" after only looking (a tab list, a page read) are checked.
- A tool name from another server is no longer mapped onto a different tool with the same ending.
- Notes the desktop and phone apps add to a message are only taken from those apps; from any other caller the
  safety check reads the whole message.
