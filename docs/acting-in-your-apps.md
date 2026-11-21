[Arty](../README.md) / [Docs](README.md)

# Email, Starfire Calendar and Starfire Drive: Arty Acting for You

Arty can send and answer email, add, move and cancel events in your own Starfire Calendar app, and save or share files in
your own Starfire Drive app. It never does any of these by itself: each one shows you exactly what will happen first and
waits for your yes.

It does all of this through the browser on its own desktop, where you sign in once. Arty never sees or stores your
passwords, and the sign-ins never leave that browser.

## What It Can Do

| Tool | What it does | Asks first? |
|---|---|---|
| `workspace__gmail_unread`, `workspace__gmail_search`, `workspace__gmail_read` | Read your Gmail. | No |
| `email_send` | Sends an email from your Gmail. | Yes |
| `email_reply` | Replies to one email Arty read (the Gmail search and number it used). | Yes |
| `email_inbox`, `email_read`, `email_draft` | List, read and draft through the optional mailbox settings (below). | No |
| `calendar_app_events` | Lists the events in your Starfire Calendar app, each with a short #reference. | No |
| `calendar_app_add` | Adds an event. | Yes |
| `calendar_app_move` | Moves an event to a new time. | Yes |
| `calendar_app_cancel` | Cancels (deletes) an event. | Yes |
| `drive_save` | Saves a file you attached, a file Arty made, or text Arty wrote into your Starfire Drive app. | Yes |
| `drive_share` | Makes a share link for a file in your Starfire Drive app. | Yes |
| `undo_app_action` | Undoes the last Starfire Calendar or Starfire Drive change from the last two hours. | Yes |

## The Question You Answer

Before anything that sends, adds, moves, cancels, saves or shares, Arty stops and asks, in the chat, on whichever
app you are using (desktop, phone or starfire):

> Before I do that, I need your OK: sending an email from the Gmail signed in on my desktop to sam@example.org with
> the subject "Late" and exactly this text: "Running ten minutes late, sorry." (Gmail adds your signature, if you
> have one). Shall I go ahead?

- The question always shows everything: every address it goes to, the subject and the whole text of an email; the
  title and exact day and time of an event (and the old time when moving one); the name, size and folder of a file,
  or the whole text when it is text Arty wrote; who can open a share link and until when.
- Only a plain yes as your very next message lets it happen ("yes", "ok", "go ahead", "send it"). "Yes but change
  the subject" is not a yes.
- The yes covers exactly what the question showed. If Arty then tries anything different, even one word of the
  email, it asks again instead.
- Emails Arty sends are at most 4000 characters, and text it saves to Drive is at most 4000 characters, so the
  whole thing always fits in the question. For longer ones it saves a draft.

## What Can Never Make It Act

- **Text in emails, files and web pages.** Emails, events and files Arty reads are treated as information, never
  as instructions.
- **Addresses it found somewhere.** Arty only sends to an address you typed yourself in the same chat, or one of your
  saved contacts (`MAIL_CONTACTS`, below). An address that appears only in an email, an attached file or a web page
  is refused before you are even asked. A reply always goes to the sender of that email only: never to a
  "Reply-To" address or to the other people copied in.
- **Background and scheduled jobs.** These tools are not offered in background or scheduled jobs, and if one were
  called there it is refused. A job can still read your email or calendar, or save a draft, and tell you what it
  would have done.

## Undo

- `undo_app_action` (or saying "undo that" straight after) undoes the last Starfire Calendar or Starfire Drive change from the last two
  hours: it removes an event Arty added, moves one back, puts a cancelled one back, moves a saved file to Drive's Bin
  (where Drive keeps it for 30 days), or turns a share link off. It shows you what it will undo and asks first.
- **Sent email can't be undone.** Arty tells you so instead.
- An undo goes the same way the change went: a change made through the desktop browser is undone there.
- Repeating events are left to the Starfire Calendar app: Arty lists them but won't move or cancel them.

## Setting It Up

Once, on Arty's desktop (the desktop app's screen view, or the VM's own screen):

1. **Gmail:** open the "Sign in to Google" icon and sign in to Gmail. That window stays open; Arty reads and sends
   from it.
2. **Drive and Calendar:** open the "Sign in to Starfire apps" icon. It opens Arty's separate apps browser on the
   starfire, Drive and Calendar pages: sign in to starfire there once, then close that window. Don't sign in to these
   apps in the "Sign in to websites" window: the browser Arty's model drives never opens them, and their pages and
   requests are blocked there.

That's all. If a sign-in runs out, Arty says so in the chat and asks you to sign in again; it never types a password
itself.

For whoever runs the server: Arty only calls the Drive and Calendar apps at the addresses set in `hosts.env`
(`DRIVE_ADDRESS`, `CALENDAR_ADDRESS`, each `https://<host>`, plus `STARFIRE_ADDRESS` for the starfire sign-in); a
deploy writes them to `/etc/arty/own-apps.json` on the desktop (owned by root) and to the brain, which treats those
hosts like the other sites where a browser step needs your OK. It only makes the
calls it needs (list, search, upload, share, trash; list, add, change, delete events), never app passwords, sync
settings, emptying the Bin or deleting for good. Uploads through the browser are at most 25 MB.

### How It Works

- Drive and Calendar: the browser agent on the desktop starts the separate apps browser (its own profile,
  `~/.apps-browser-profile`, with no debugging port), opens the app's own address there and makes the one call, with
  the app's own sign-in and its protection against forged requests, then closes it. Nothing from the sign-in is
  handed back to the brain. The browser Arty's model drives refuses to open, read or click on these apps and on
  starfire, and blocks every request to them, so a web page Arty reads can't reach them through it.
- Reading web pages on the server (the fetch tool, link and docs reading, page and price watchers) refuses the same
  hosts and their subdomains, at the first address and at every redirect, and so does the fetch tool server behind
  the tool hub. If the brain's copy of the list can't be read, the server reads no web pages until it is fixed.
- Gmail: the Gmail window opens a new email with the approved address, subject and text, reads every field back, and
  presses Send only if all of it is exactly what you approved (no other addresses, no Bcc, one Send button). It then
  waits for Gmail to say "Message sent", or finds it in Sent. It never tries a second time: if it can't tell, it says
  so and asks you to look in Sent.
- These steps are tools Arty's model is never offered and can't call itself; only the approved actions above use them.

### Optional: Settings Files Instead of the Browser

The older way still works. It is used only when the brain has no desktop tools at all, and for the tools that need
it. Nothing else does. Each file is stored on the brain's container, readable only by root.

- `bin/set-mail` saves `/srv/ai/secrets/mail.env` (IMAP and SMTP with an app password). It adds `email_inbox`,
  `email_read`, `email_draft`, the inbox tidy (see [Background jobs](background-jobs.md)) and saved contacts
  (`MAIL_CONTACTS`, addresses Arty may email without you typing them). `none` (no encryption) is only accepted for a
  mail server on the same machine or your home network.
- `bin/set-own-apps` saves `/srv/ai/secrets/own-apps.env`: the Starfire Calendar app over CalDAV (switch CalDAV sync on and make
  an app password in its Settings), and the Starfire Drive app through its sign-in proxy (the proxy's user name and secret, the
  brain's container added to Drive's trusted proxies).

| File on the brain's container | Settings |
|---|---|
| `/srv/ai/secrets/mail.env` | `MAIL_ADDRESS`, `MAIL_USER`, `MAIL_PASSWORD`, `MAIL_IMAP_HOST`, `MAIL_IMAP_PORT`, `MAIL_IMAP_SECURITY`, `MAIL_SMTP_HOST`, `MAIL_SMTP_PORT`, `MAIL_SMTP_SECURITY`, `MAIL_DRAFTS_FOLDER`, `MAIL_ARCHIVE_FOLDER`, `MAIL_NAME`, `MAIL_CONTACTS` |
| `/srv/ai/secrets/own-apps.env` | `OWN_CALENDAR_URL`, `OWN_CALENDAR_USER`, `OWN_CALENDAR_PASSWORD`, `OWN_CALENDAR_TIME_ZONE`, `OWN_DRIVE_URL`, `OWN_DRIVE_USER`, `OWN_DRIVE_PROXY_SECRET`, `OWN_DRIVE_PUBLIC_URL` |

When both are there, the desktop browser is used. Share links only work if shares are switched on in the Drive app.

The record Arty keeps for undo is `/srv/ai/brain/state/app-actions.json` (the last 20 actions).

## Known Gaps

- Times like "in 2 hours" change between the question and your yes, so Arty asks again. Give a day and a clock
  time ("Friday 7pm", "25 Dec 14:00").
- Sent emails are not copied to a Sent folder by Arty when it uses the optional mailbox settings; most providers do
  that themselves. Through Gmail they land in Sent as usual.
- A Gmail reply goes to whoever sent the newest message in that conversation, which is you if you answered last:
  the question shows the address, so say no and give the address instead.
- The inbox tidy still needs the optional mailbox settings: archiving and labelling in Gmail's own screen isn't
  reliable enough to do many changes from one yes.
- An email's text is the plain text part; formatting in HTML emails is dropped when Arty reads them.
