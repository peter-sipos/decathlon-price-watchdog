# Decathlon price watchdog

A free GitHub Actions workflow that checks four Decathlon CZ/SK pillow listings once a
day and opens a GitHub issue — which GitHub emails to you — whenever one of them gets
cheaper.

Prices are read from **Heureka** search results through a scraping service, because both
Decathlon and Heureka block GitHub's runners at the Cloudflare layer. See *Why a scraping
service* below — it needs one repository secret.

## Watched items

| Item | Shop |
| --- | --- |
| [Polštář Ultim Comfort XL](https://www.decathlon.cz/p/polstar-ultim-comfort-xl/_/R-p-348187) | decathlon.cz |
| [Polštář Ultim Comfort](https://www.decathlon.cz/p/polstar-ultim-comfort/_/R-p-308736) | decathlon.cz |
| [Kempingový vankúš XL Ultim Comfort](https://www.decathlon.sk/p/348187-368243-kempingovy-vankus-xl-ultim-comfort.html) | decathlon.sk |
| [Kempingový vankúš Ultim Comfort](https://www.decathlon.sk/p/308736-333552-kempingovy-vankus-ultim-comfort.html) | decathlon.sk |

## Setup

Three one-time steps:

1. **Add your scraping-service API key** as the repository secret `SCRAPER_API_KEY`
   (Settings → Secrets and variables → Actions). See *Why a scraping service*.
2. **Allow the workflow to open issues and push.**
   Settings → Actions → General → *Workflow permissions* → **Read and write permissions**.
3. **Make sure you get the email.**
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

## Why a scraping service

Both Decathlon shops **and Heureka** are Cloudflare customers, and Cloudflare challenges
GitHub's datacenter IP ranges. Everything client-side was tried and blocked:

| Client | Result |
| --- | --- |
| urllib with full Chrome headers | `403` |
| curl with HTTP/2 | `403` |
| Playwright driving real Chrome | unsolved interactive challenge |
| headless Chrome `--dump-dom` | unsolved interactive challenge |

This is an IP-reputation decision, so no client-side change fixes it. Note that Scrapy
would not help either: it is a scraping *framework* that runs on your own machine, so it
would come from the same blocked IP.

What works is a service that fetches the page **from its own residential IPs**. Set the
repository secret `SCRAPER_API_KEY` (Settings → Secrets and variables → Actions), and the
workflow passes it through. Ready-made templates for `proxy_template` in `items.json`:

| Service | template |
| --- | --- |
| ScraperAPI | `https://api.scraperapi.com/?api_key={key}&url={url_encoded}` |
| ScrapingBee | `https://app.scrapingbee.com/api/v1/?api_key={key}&url={url_encoded}&premium_proxy=true` |
| ScrapingAnt | `https://api.scrapingant.com/v2/general?url={url_encoded}&x-api-key={key}` |
| ZenRows | `https://api.zenrows.com/v1/?apikey={key}&url={url_encoded}&premium_proxy=true` |

`{url}`, `{url_encoded}` and `{key}` are substituted; `proxy_key_env` overrides which
environment variable holds the key. The key is redacted from every log line, error
message and uploaded debug file, and debug filenames derive from the plain URL, so a key
cannot leak through a filename either.

### Paying the cheap tier first

`proxy_templates` is a list tried **cheapest first**, stopping at the first tier that
returns a real page. Scraping APIs charge by tier — a plain fetch costs a fraction of a
rendered one, and the hardest anti-bot tier more again — so starting at the top would
overpay on every run for capability that may not be needed. The shipped ladder is:

1. plain fetch
2. `&render=true`
3. `&ultra_premium=true`

When a run has to escalate it logs `needed proxy tier N` as a warning. If you see tier 3
every day, move it to the front of the list to stop wasting the two cheaper attempts —
and if you never see a warning, the cheapest tier is doing the job.

### Watch your credit budget

Free tiers are typically around 1,000 credits a month, and the higher tiers cost several
credits per request rather than one. Exact costs vary by provider and change over time,
so check your dashboard after the first few runs — treat the numbers below as arithmetic
to redo against your own plan, not as quoted prices.

The default configuration is deliberately frugal: it reads the **two Heureka search
pages** (one CZ, one SK), each covering two products, so a run costs 2 requests — roughly
60 a month. Watching the four Decathlon pages directly would double that.

At 2 requests a day on the cheapest tier that is comfortable. If every request has to
escalate to the most expensive tier, a daily schedule can exceed a 1,000-credit
allowance; drop the schedule to every other day (`0 5 */2 * *`) if so.

Two caveats about reading from Heureka:

- **Heureka shows the cheapest offer across all shops**, which need not be Decathlon's.
  For these Decathlon own-brand pillows Decathlon is normally the only seller, but the
  alert names its source so you can confirm before buying.
- The alert still links to the **Decathlon** product page, since that is where you buy.

To read Decathlon directly instead, give each item a `url` (the product page) instead of
`search_url`/`match_all`/`match_none`, plus `skus`; that extractor is still present and
tested.

### Choosing the right listing row

`match_all` and `match_none` pin the right search result. They matter because *Ultim
Comfort* is a substring of *Ultim Comfort XL* — without `match_none: ["xl"]` the non-XL
item would happily match the XL row. Matching folds diacritics, so `polstar` matches
`Polštář`, and short tokens like `xl` match whole words only.

The fetch chain still detects challenge pages, so if the service ever returns one, the
run fails loudly rather than inventing a price.

### Probing alternatives

`Actions → Probe price sources → Run workflow` tests reader proxies and other CZ/SK
comparison sites from a runner and prints which are reachable. Use it if you would rather
find a free source than spend credits.

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

Append to `items.json` using the shape of the existing entries. Give every item a
unique `id` — it keys the stored price, so changing it resets that item's baseline.

Check the log after adding one: it prints the listing row each price was matched from.

## Cost

GitHub Actions itself stays free: one run a day, well under a minute. Public repositories
get unlimited minutes; private ones bill this at roughly a minute a month against the
free tier.

The scraping service is the only thing that meters usage — see *Watch your credit
budget*. If you would rather spend nothing at all, run the same workflow on a
[self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners) on your
own machine: a residential IP is not challenged, so no service is needed. The trade-off
is that checks only happen while that machine is on.
