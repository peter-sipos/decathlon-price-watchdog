# Decathlon price watchdog

A free GitHub Actions workflow that checks four Decathlon CZ/SK pillow listings once a
day and opens a GitHub issue — which GitHub emails to you — whenever one of them gets
cheaper.

Prices come straight from the Decathlon product pages, fetched through a scraping
service because Decathlon blocks GitHub's runners at the Cloudflare layer. See *Why a
scraping service* below — it needs one repository secret.

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

Decathlon fronts both shops with Cloudflare, which challenges GitHub's datacenter IP
ranges. Every client-side approach was tried from a runner and blocked: urllib with a
full Chrome header set and curl over HTTP/2 both got `403`, and Playwright and headless
Chrome both got an interactive challenge they could not clear. It is an IP-reputation
decision, so no client-side change fixes it — Scrapy included, since that is a scraping
*framework* running on your own machine, from the same blocked IP.

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

One run costs **4 requests**, one per product — roughly 120 a month, which the cheapest
tier handles comfortably inside a typical 1,000-credit allowance. If requests start
escalating to the most expensive tier, a daily schedule could exceed it; switch to every
other day (`0 5 */2 * *`).

Challenge pages are still detected on the way in, so if the service ever returns one, the
run fails loudly rather than inventing a price.

## If a check breaks

The job **fails loudly** rather than silently reporting nothing: if any page cannot be
fetched or no price can be found, the script exits non-zero and GitHub emails you about
the failed scheduled run.

The run also uploads a `fetched-pages` artifact (kept 7 days) containing the body of
every failed attempt, named `<slug>.tierN.failed.html` or `.blocked.html`. That is what
identifies the blocker — open it and the bot manager usually names itself.

## Price extraction

From a Decathlon product page, in order of reliability:

1. `schema.org` **JSON-LD**, filtered to the offer whose product blob mentions the
   item's SKU — so a bundle or recommended-product carousel on the same page cannot be
   mistaken for the pillow you are watching. Note that Decathlon nests the figure at
   `offers.priceSpecification.price` rather than `offers.price`, so extraction walks
   into the offer rather than reading one flat key; `tests/fixtures/` holds markup
   captured from the live page to keep that honest.
2. Embedded JSON (`__NEXT_DATA__` and other JSON blocks) carrying both a price-like and a
   currency-like key.
3. `product:price:amount` / `og:price:amount` meta tags.
4. Visible page text with a `Kč` or `€` symbol, as a last resort.

Every candidate is bounds-checked per currency, so a `0`, a free-shipping threshold or a
stray banner figure cannot become your baseline — the real CZ page carries a 2 000 Kč
free-shipping line and 149/249 Kč bundle items, and the fixture test asserts none of them
win. The log records which source each price came from, so a silent change in the page
structure shows up as a changed source rather than a wrong number.

**When an item is on sale**, JSON-LD carries the discounted figure and no trace of the
pre-sale one — a fixture captured from a live discounted page confirms it. The crossed-out
"was" price is read separately, from the barred amount in the buy box or from
`referenceValueWithTaxes` in the page's flight data, and only when it sits next to the
price actually extracted. That keeps a bundle carousel's own sale pair out of your alert,
at the cost of no "was" line if Decathlon restyles the buy box; the drop percentage
against the watchdog's own baseline is reported either way.

## Local use

```bash
python3 -m unittest discover -s tests -v      # offline parser tests, no network needed
SCRAPER_API_KEY=... python3 scripts/check_prices.py --dry-run   # check without writing state
SCRAPER_API_KEY=... python3 scripts/check_prices.py --save-html debug   # keep the HTML
```

The script is standard library only, so there is nothing to install.

## Adding items

Append to `items.json` using the shape of the existing entries. Give every item a
unique `id` — it keys the stored price, so changing it resets that item's baseline, and
`skus` so the right offer is picked out of a page that also advertises bundles.

Check the log after adding one: it prints which source each price came from and how many
candidates were in play. Every extra item costs one more request per run.

## Cost

GitHub Actions itself stays free: one run a day, well under a minute. Public repositories
get unlimited minutes; private ones bill this at roughly a minute a month against the
free tier.

The scraping service is the only thing that meters usage — see *Watch your credit
budget*. If you would rather spend nothing at all, run the same workflow on a
[self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners) on your
own machine: a residential IP is not challenged, so no service is needed. The trade-off
is that checks only happen while that machine is on.
