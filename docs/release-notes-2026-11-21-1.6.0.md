# Arty 1.6.0 release notes, 21 November 2026

Arty 1.6.0 is the biggest update since 1.5.0: Arty can read files and use a webcam, act in your own mail, calendar and drive after you say yes, run background watchers, take part in a shared chat room, and use your computer more carefully when you let it. Builds for Linux, Windows and Android are attached with the new browser extension (1.7). The same files are on the Arty download page.

## Changes you'll notice

- **Background jobs.** Arty can watch a page, a price or a server, send a daily briefing with unread counts and news headlines, and tidy your inbox by labelling and archiving (never deleting). You set them up by asking and list, pause, resume and delete them by number.
- **Reading files.** PDFs, Word and ODT files and spreadsheets get a preview in chat and can be read in detail, including scanned pages. Spreadsheet sums are worked out in code with exact decimals.
- **Camera.** Arty Desktop can take a photo from your webcam on Linux and Windows, and the Android app has a Take a photo button. Every photo needs an Allow card, shows a notice first and is discarded after use. A question about how you look gets honest, kind, practical feedback with clear limits.
- **Acting in your own apps.** Email, calendar and drive actions (send, reply, add, move, cancel, save, share) always show the exact recipients, text, time or file and run only after a plain yes to that exact text, once. They are refused in background jobs. Sending email and changing Drive and Calendar go through the apps you are signed in to on the desktop.
- **Talk over Arty.** The voice room assistant removes its own echo, stops speaking when you start, keeps only the words you heard, and speaks sentence by sentence as the reply streams.
- **Songs, pictures, video and sound.** Arty names a song from its transcribed lyrics and a web search, reads text in pictures, describes moments in a video from scene changes, and measures sounds (loudness, frequencies, beat, clicks).
- **Link a phone with a QR code.** Arty Desktop shows a one-use code that expires after two minutes and holds no key. The phone proves it scanned it, and phone actions (text, alarm, timer, open an app) show the phone's own Allow card.
- **Reading the web.** Arty can read public pages and check prices on its own, with page reads pinned to public addresses, no downloads and size caps.
- **Arty Desktop 1.6.0.** A browser of its own that is not signed in to anything of yours, optional screen control (off until you tick it, with an Allow card for every message it sends), and linking to starfire Desktop and your browsers without a code.
- **Browser extension 1.7.** Pairs with Arty Desktop by itself. It asks for the new native messaging permission when it updates, and pasting a code still works. The Gmail reader extension is 1.5 and can send an email you approved.
- **Coursework help.** Arty helps with coursework and homework in chat. Its own clicks and typing are held only on a live graded test, exam or hiring test.
- **Exact answers.** Money, measurement and date questions are worked out by a short program the model writes and the code runner runs, and thinking is switched on only for quizzes and puzzles.
- **Dates on replies.** Every reply shows Today, Yesterday, a weekday or the date before its time, in the Android, iPhone, Desktop and starfire apps.
- **A shared chat room.** A separate room key lets Arty answer in a shared AI chat room with no personal data, no memory and no personal tools.
- **Safer approvals.** A yes now approves the question that was asked (a bug cut the question off, so an approval could never be given), and a yes cannot be used twice for the same card.
- **Arty for Android 1.6.0** declares what it can do, so it gets alarms, timers, apps and the camera, while Android 1.5.8 keeps working against the new brain.

## Upgrading

Update the brain from this source. Arty Desktop, Arty for Android and the browser extension need their new builds for the features above; older apps keep working with the new brain with the features they had.

## Downloads

These are the shareable builds: they hold no server address or key, so you enter your own on first start.

| File | sha256 |
|---|---|
| Arty-1.6.0-x86_64.AppImage | `4dffa3cfb4526963cd3275dd8ae77128842ef6c3abe32c4541faac895ddb0a54` |
| Arty-Setup-1.6.0.exe | `ded25c706fb9f9077ce78d1240f76df663409b096e235f76648a343683ad824e` |
| Arty-1.6.0.apk | `17886234655567629e09234f0969bc053feb67b8fd3ab3f109402d86f7d827a0` |
| Arty-Browser-Extension-1.7.zip (load unpacked after unzipping) | `b76342d71dc13701f3d55efae6280428dd8e5f069982d7237ea2be4550c1a4fe` |
| Arty-Browser-Extension-1.7.xpi (unsigned, Firefox ESR, Developer Edition or Nightly with `xpinstall.signatures.required` off) | `b76342d71dc13701f3d55efae6280428dd8e5f069982d7237ea2be4550c1a4fe` |

`SHA256SUMS.txt` is attached too. The brain and the iPhone app are not binaries: update the brain from this source and the iPhone app arrives with it.
