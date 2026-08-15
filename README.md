# Decathlon price watchdog

A free GitHub Actions workflow that checks four Decathlon CZ/SK pillow listings once a
day and opens a GitHub issue — which GitHub emails to you — whenever one of them gets
cheaper.

## Watched items

| Item | Shop |
| --- | --- |
| [Polštář Ultim Comfort XL](https://www.decathlon.cz/p/polstar-ultim-comfort-xl/_/R-p-348187) | decathlon.cz |
| [Polštář Ultim Comfort](https://www.decathlon.cz/p/polstar-ultim-comfort/_/R-p-308736) | decathlon.cz |
| [Kempingový vankúš XL Ultim Comfort](https://www.decathlon.sk/p/348187-368243-kempingovy-vankus-xl-ultim-comfort.html) | decathlon.sk |
| [Kempingový vankúš Ultim Comfort](https://www.decathlon.sk/p/308736-333552-kempingovy-vankus-ultim-comfort.html) | decathlon.sk |

## Setup

There are no secrets to configure. Two one-time steps:

1. **Allow the workflow to open issues and push.**
   Settings → Actions → General → *Workflow permissions* → **Read and write permissions**.
2. **Make sure you get the email.**
   Watch this repository (**Watch → All Activity**, or at minimum *Issues*) and check that
   Settings → Notifications → *Email* is enabled for your account. Every alert issue is
   also assigned to you, which notifies you regardless of watch settings.

Then run it once by hand: Actions → *Decathlon price watch* → **Run workflow**. The first
run records a baseline and will not alert. Every later run compares against it.

## How alerting works

- **Trigger:** the current price is lower than the last price the watchdog saw.
- **Baseline:** `data/prices.json`, committed back to the repo after each run.
- **Contents:** the issue gives you the item link, the new price, the previous price and
  the percentage off — plus the listed original price when the page advertises one.

Once an alert fires, the new lower price becomes the baseline, so you get one issue per
drop rather than a daily repeat. Price increases are recorded silently.

## Getting past the bot protection

Both shops sit behind **Cloudflare**, which serves a managed challenge (`Just a moment…`
/ `Okamžik…` / `Len chvíľu…`) instead of the product page. The script escalates through
four clients and uses the first that returns a real product page:

1. **urllib** with a full Chrome header set — instant when it works; currently gets 403.
2. **curl** with HTTP/2 — a different TLS fingerprint; currently gets 403.
3. **Playwright** driving the runner's Chrome — loads the page and *waits* for the
   interstitial to clear, polling until the challenge markers disappear.
4. **headless Chrome** `--dump-dom` — a dependency-free fallback for local runs.

Steps 1 and 2 fail against a challenge by design; step 3 is the one that has to work.

A response is only treated as success if it is not a challenge page. Detection keys on
locale-independent markers (`/cdn-cgi/challenge-platform`, `_cf_chl`) plus known
interstitial titles in English, Czech and Slovak — matching English text alone let a
localised challenge masquerade as a real page. A page merely *referencing* Turnstile for
a login widget is not treated as blocked.

## If a check breaks

The job **fails loudly** rather than silently reporting nothing: if any page cannot be
fetched or no price can be found, the script exits non-zero and GitHub emails you about
the failed scheduled run.

The run also uploads a `fetched-pages` artifact (kept 7 days) containing the body of
every failed attempt, named `<slug>.<strategy>.failed.html` or `.blocked.html`. That is
what identifies the blocker — open it and the bot manager usually names itself.

## Price extraction

Prices are read in order of reliability, stopping at the first source that yields a
plausible figure:

1. `schema.org` **JSON-LD** `Product.offers.price` — preferred, and filtered to the offer
   whose product blob mentions the item's SKU, so recommended-product carousels on the
   same page cannot be mistaken for the item you are watching.
2. Embedded JSON (`__NEXT_DATA__` and other JSON script blocks), for any dict carrying
   both a price-like and a currency-like key.
3. `product:price:amount` / `og:price:amount` meta tags.
4. Visible page text with a `Kč` or `€` symbol, as a last resort.

Every candidate is bounds-checked per currency, so a `0`, a free-shipping threshold, or a
stray banner figure cannot become your baseline.

## Local use

```bash
python3 -m unittest discover -s tests -v      # offline parser tests, no network needed
python3 scripts/check_prices.py --dry-run     # check prices without writing state
python3 scripts/check_prices.py --save-html debug   # keep the fetched HTML to inspect
```

## Adding items

Append to `items.json`:

```json
{
  "name": "Product name",
  "url": "https://www.decathlon.cz/p/...",
  "skus": ["123456"],
  "accept_language": "cs-CZ,cs;q=0.9,en;q=0.6",
  "expect_currency": "CZK"
}
```

`skus` should list the numeric IDs in the product URL — they are what pins extraction to
the right product on the page.

## Cost

Free. One run a day, a few seconds each, no dependency install (standard library only).
Public repositories get unlimited Actions minutes; private ones bill this at roughly a
minute a month against the free tier.
