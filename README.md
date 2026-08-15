# Decathlon price watchdog

A free GitHub Actions workflow that checks four Decathlon CZ/SK pillow listings once a
day and opens a GitHub issue — which GitHub emails to you — whenever one of them gets
cheaper.

Prices are read from **Heureka** search results, not from Decathlon directly: both
Decathlon shops sit behind a Cloudflare challenge that an unattended runner cannot
clear. See *Why Heureka* below.

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

## Why Heureka

Decathlon's own product pages are served through **Cloudflare**, which returns a managed
challenge (`Just a moment…` / `Okamžik…` / `Len chvíľu…`) instead of the page. Three
clients were tried and all were blocked from GitHub's runners: plain HTTP and curl get a
`403`, and a real browser driven by Playwright still sat on an unsolved interactive
challenge. That is an IP-reputation decision by Cloudflare, so no client-side change
fixes it.

Heureka lists the same products with prices and is not challenged, so each item names a
Heureka search page plus the matching row in the results:

```json
{
  "id": "cz-ultim-comfort-xl",
  "search_url": "https://www.heureka.cz/?h%5Bfraze%5D=ultim+comfort+polstar",
  "product_url": "https://www.decathlon.cz/p/polstar-ultim-comfort-xl/_/R-p-348187",
  "match_all": ["ultim comfort", "xl"],
  "match_none": []
}
```

`match_all` and `match_none` together pin the right result. They matter because
*Ultim Comfort* is a substring of *Ultim Comfort XL* — without `match_none: ["xl"]` the
non-XL item would happily match the XL row. Matching folds diacritics, so `polstar`
matches `Polštář`, and short tokens like `xl` match whole words only.

Two caveats worth knowing:

- **Heureka shows the cheapest offer across all shops**, which need not be Decathlon's.
  For these Decathlon own-brand pillows Decathlon is normally the only seller, but the
  alert says where the price came from so you can confirm before buying.
- The alert still links to the **Decathlon** product page, since that is where you buy.

The fetch chain (urllib → curl → Playwright → headless Chrome) is unchanged and still
detects challenge pages, so if Heureka ever starts challenging too, the run fails loudly
rather than inventing a price.

## If a check breaks

The job **fails loudly** rather than silently reporting nothing: if any page cannot be
fetched or no price can be found, the script exits non-zero and GitHub emails you about
the failed scheduled run.

The run also uploads a `fetched-pages` artifact (kept 7 days) containing the body of
every failed attempt, named `<slug>.<strategy>.failed.html` or `.blocked.html`. That is
what identifies the blocker — open it and the bot manager usually names itself.

## Price extraction

For a Heureka listing, in order of reliability:

1. **JSON-LD / embedded JSON** — any object whose `name` matches the item and that
   carries a price. Preferred, because the pairing is explicit.
2. **Visible text** — find the product name among the page's text nodes, then take the
   first price within the following 25 nodes, which is the same card.

Every candidate is bounds-checked per currency, so a `0`, a free-shipping threshold or a
stray banner figure cannot become your baseline. When several rows match, the cheapest
wins and the others are printed in the log. The run also prints and stores which listing
row a price came from, so a mismatch is visible rather than silent.

## Local use

```bash
python3 -m unittest discover -s tests -v      # offline parser tests, no network needed
python3 scripts/check_prices.py --dry-run     # check prices without writing state
python3 scripts/check_prices.py --save-html debug   # keep the fetched HTML to inspect
```

## Adding items

Append to `items.json` using the shape shown under *Why Heureka*. Give every item a
unique `id` — it keys the stored price, so changing it resets that item's baseline.

Check the log after adding one: it prints the listing row each price was matched from.

## Cost

Free. One run a day, well under a minute. The only dependency is Playwright, kept as a
fallback fetch strategy; extraction itself is standard library only.
Public repositories get unlimited Actions minutes; private ones bill this at roughly a
minute a month against the free tier.
