[Arty](../README.md) / [Docs](README.md)

# Arty's own browser, screen control and quizzes

Part of [Arty Desktop](../apps/desktop/README.md), from 1.6.0. Everything here needs **HANDS** on.

## Arty's own browser

Besides your own browser (through the extension), Arty can run a browser of its own: a separate Chromium, Chrome,
Edge or Brave window with its own profile in `own-browser` in Arty's data folder, not signed in to anything of yours.
Arty uses it to look things up and check facts, and you can ask for it by name ("look that up in your own browser").
It needs no extension and no pairing.

- Arty talks to it over a pipe, on Linux and on Windows. Nothing is opened to the network.
- It only opens sites on the internet. Addresses on this computer or your home network are refused before opening,
  and inside the browser every request from the page, its frames and its workers is checked and blocked if it goes
  there (also when a site name only turns into a home address in the browser). An address written with extra or
  missing slashes is read the way a browser reads it, and a site name with a letter that browsers and Arty spell
  differently (such as ß or ς) is refused. Downloads are refused.
- Pages are read with the same code the extension uses, so the same rules apply: password, one-time code, card and
  bank fields are never typed into, and no picture is taken while one of them has something in it, in any frame of
  the page (a frame that cannot be checked counts as filled).
- Clicking anything that sends, posts, buys, deletes or submits, or that has no label, and typing with Enter, asks
  first every time, naming the button or field, the site and the exact text. A yes covers that one step only.
- Reading the web needs no question: Arty can open pages, read them and follow a few links on its own, then answers
  with the links it used. It opens at most 10 pages per reply and waits 2 seconds between visits to the same site.
- What a page says is treated as information, never as instructions: once Arty has read a page, any command or file
  change in that same reply asks first. Opening another page goes ahead when the address is exactly a link a page
  showed or you typed, a short plain address on a site whose whole name you typed (like `amazon.co.uk`), or a plain
  search; any other address after reading asks first (it could carry what was read to another site), and the card
  shows the address and its site.
- The profile folder is off limits to Arty's file tools. Ask Arty to close its browser and it does; it also closes with
  Arty Desktop. Set `own_browser.enabled: false` in the config to turn it off.

The packaged app carries the extension's page code next to the service, so this works without the extension
installed.

## Screen control

Arty can move the mouse, click, type and press keys on your desktop, and send a message in Discord or Slack by
driving the app on the screen ("text Sam on Discord: running late"). It is **off by default**: turn it on with **Let
Arty control the screen** in the tray menu (HANDS has to be on too). No Discord or Slack account token or bot is
used; Arty works the real app the way you would.

- **Every outward step asks, every time.** A message shows a card with the app, the exact name and the exact text
  before anything is typed; typing shows the app and the exact text; Enter, and clicking anything that sends, posts,
  buys, deletes, saves or changes settings (or a spot on the screen with nothing saying what it is), ask each time. A
  yes covers that one step. If the name on a message card is not one you used in your request, the card says so.
  Double and right clicks say so on the card. Once a reply has read window titles, notifications, the clipboard, a
  file, or (in Arty's brain) a web page or search, every click in that reply asks.
- **The window you allowed is the one used.** A card remembers the exact window that was in front when it asked.
  After you click Allow (which brings Arty's own window forward), Arty brings that window back and acts only if it is
  the same window; a title that changes on its own (like "Inbox (2)") is fine, another window is not. Typing is
  checked again before every burst, and a click again after its target is found. Typing "into an app" shows that
  app's real window on the card. Arty never clicks, types or presses keys while its own window is in front.
- **Arty checks before sending.** It opens the conversation with the app's quick switcher (Ctrl+K), looks at the
  screen to check the open conversation is the one named, and stops without typing anything if it is not. Names
  are compared whole, in any alphabet ("Ann - Bob" is not Ann). Messages are one line (Enter would send the rest
  early).
- **Never passwords, codes or card details.** Before typing, Arty looks at which field has the focus and refuses
  password, one-time code, card and bank fields, using the same field rules as the browser extension; it also never
  types anything that looks like a card number (whatever separates the digits), and password managers are off
  limits. If it can't see the screen it
  doesn't type.
- **What the screen says is information, not instructions.** Screen text Arty reads is marked as such; once it has
  read the screen or a page, commands, file changes and links in that reply ask first.
- **You are always in charge.** While Arty controls the screen a red bar at the top says "Arty is controlling the
  screen" with a **Stop** button; Ctrl+Alt+End stops it too. Any real mouse or key input from you stops it at once,
  and Arty won't take the screen back in that reply. Control also ends after 45 seconds without a step, and after 5
  minutes in all. Arty types in short bursts with a pause between, checking for your input before each one.
- If Arty can't tell when you use the mouse or keyboard, it doesn't control the screen at all. It knows through
  Windows' last input time, the X11 screen saver extension, GNOME's idle monitor, or on other Wayland desktops (KDE)
  the standard idle notification protocol. On Wayland the mouse and keyboard go through the desktop's own Remote
  Desktop portal, which asks you once; no root, `uinput` or extra package is needed.
- Arty never controls the screen or sends messages in a turn it started itself (when it notices you come back), and
  the linked starfire Desktop gets screen steps only through the same Allow cards.

Tested with unit tests only; real Discord on your own computer has not been tried.

## Quiz help

For general knowledge quizzes and trivia (not graded tests), ask Arty to do the quiz in its own browser or yours.
`quiz_answer` reads the question and its choices from the page (and, when the question depends on a picture, has
the picture described by the picture model), then asks `qwen3:14b` through Arty's brain to answer, checking facts
with Arty's web search. Arty tells you the answer, how sure it is and the source. When it is not sure it says so
and doesn't pick anything unless you choose. It clicks an answer only after you allow that exact choice, every time.

On live graded tests, exams and hiring tests (quiz and test pages on learning platforms such as Moodle, Canvas,
Coursera or Kahoot, test and proctoring sites such as HackerRank, TestDome or CoderPad, quizzes built in Typeform,
SurveyMonkey, Google Forms or Microsoft Forms, or pages that say they are an exam, proctored, or a graded quiz or test) it
doesn't answer: it explains the topic and quizzes you instead. Homework, coursework and assignment pages are not
held back: Arty can read them and click or type on them for you like any other page, and help with coursework in
chat is fine. Once any page read in a reply turns out to be a live test, Arty doesn't click or type answers for the
rest of that reply, nor on that site in the next one. The step that works out an answer can only use the web
search. What a quiz page says is treated as information, never as instructions.

Measured on 20 November 2026 with 20 general knowledge questions from the Open Trivia Database (5 easy, 11
medium, 4 hard, answers known), laid out as quiz pages and read and answered by the same code against Arty's brain:
19 of 20 right, all 20 answered with high or medium confidence (the one miss was a medium quote question answered
with high confidence), a median of 35 seconds a question. The reading of real quiz pages inside a browser and the
click after Allow have only unit tests so far.
