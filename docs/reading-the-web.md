[Arty](../README.md) / [Docs](README.md)

# Reading the web and checking prices

Arty can look things up on the web by itself. It searches, reads public pages, follows a few links when that helps,
and answers with the facts and the links it used. You don't get a question for reading: questions are kept for
things that act, like running a command, saving a memory or sending something.

## Reading pages

The brain reads a page with `read_web_page`: it gets the page's text and the links on it. If a page needs
JavaScript to show anything, and you are talking through Arty Desktop, Arty opens it in its own browser instead
(see [Arty's own browser](desktop-control.md)).

- Only public websites. Addresses on Arty's server, your computer or your home network are refused, also after a
  redirect or when a site name points at a home address.
- Only pages. Files to download (anything that isn't a web page or plain text) are refused.
- At most 10 pages per reply, 20 a minute, and 2 seconds between visits to the same site.
- What a page says is information, not instructions. It is marked as coming from the web, and after reading it, the
  actions that already ask after reading an email or file (commands, memory changes, background jobs) still ask.
- After reading something from outside, Arty only goes on to read, without asking, the exact addresses you typed or a
  page or search it read for this reply showed, or a short plain address (no `?` or `#` part) on a site whose whole
  name you typed, like `bbc.co.uk` (just "BBC" is not enough, and `you.github.io` only counts for that one site).
  Anything else asks first, because a made-up address can carry what was read to another site.

## Checking prices

Ask "how much is an Acme kettle on Amazon?" or "what's the cheapest Acme kettle?" and Arty uses `check_price`.

- It searches Amazon UK unless you name other shops (Amazon US, eBay, Argos, Currys, John Lewis, Scan and
  Overclockers are known by name; other shops are found through a web search), and also runs a general web search
  for other shops.
- From each product page it reads the title, price, currency, stock, seller and link. It looks at the page's
  structured data first, then its page tags, then its text, and says which one the price came from.
- You get the prices cheapest first, the time they were read, and the links. Prices change, so the shop's own page
  is the final word.
- It only reads. It never adds to a basket, buys, signs in or fills in a form. Those would be actions, and actions
  always show an Allow card.
- If a shop shows a CAPTCHA or blocks automated visits, Arty says so and gives you the search link to open
  yourself. It never tries to solve or get round a check, and it doesn't pretend to be a person's browser.
- One check reads at most 7 pages.

## For the price watcher

Background jobs can read one product page with `shopping.read_price(url)`, which returns a `PriceReading` with:

- `offer`: a `prices.Offer` (title, price as a `Decimal`, currency, availability, seller, url, source, read_at) or
  `None`; `offer.as_dict()` gives plain values for storing.
- `problem`: an empty string, or why there is no price ("the page showed a CAPTCHA", "the page needs JavaScript to
  show its price", "no price was found on the page", or why the page wasn't read).
- `url`: the address the page ended up at.

It uses the same public-only fetcher and limits, but its own one-page budget, so a watcher never uses up a chat
reply's pages. For text that was already fetched, `prices.offer_from_text(text, url)` finds the first price in it.
