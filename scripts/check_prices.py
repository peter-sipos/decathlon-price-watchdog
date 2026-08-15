#!/usr/bin/env python3
"""Check Decathlon product prices and report drops against the last stored price.

Standard library only, so the workflow needs no dependency install step.

Exit codes:
    0 - every item was fetched and parsed successfully
    1 - at least one item could not be fetched or parsed
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Keys that can hold a price inside the site's embedded JSON blobs.
PRICE_KEYS = (
    "price",
    "currentprice",
    "finalprice",
    "saleprice",
    "sellingprice",
    "pricevalue",
    "lowprice",
)
CURRENCY_KEYS = ("pricecurrency", "currency", "currencycode")
# Keys holding the pre-discount price, used only to describe the sale.
WAS_PRICE_KEYS = ("strikedprice", "previousprice", "originalprice", "listprice", "regularprice")

CURRENCY_SYMBOLS = {"Kč": "CZK", "€": "EUR"}

# Non-breaking space, used as the thousands separator in CZ/SK price formatting.
NBSP = "\u00a0"

MIN_PLAUSIBLE = {"CZK": 20.0, "EUR": 1.0}
MAX_PLAUSIBLE = {"CZK": 100000.0, "EUR": 4000.0}


class FetchError(Exception):
    pass


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------- fetch


def fetch(url: str, accept_language: str, timeout: int = 30, attempts: int = 3) -> str:
    """Download a page, retrying transient failures with exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(2**attempt)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": accept_language,
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                encoding = (response.headers.get("Content-Encoding") or "").lower()
                if "gzip" in encoding:
                    raw = gzip.decompress(raw)
                elif "deflate" in encoding:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, EOFError) as exc:
            last_error = exc
    raise FetchError(f"{type(last_error).__name__}: {last_error}")


# --------------------------------------------------------------------------- parsing helpers


def to_number(value: Any) -> float | None:
    """Coerce a price of any shape the sites use into a float."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\xa0", "").replace(" ", "").replace(" ", "")
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        # Whichever separator comes last is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".") if re.search(r",\d{1,2}$", text) else text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def normalise_currency(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[text]
    text = text.upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else None


def plausible(price: float, currency: str | None) -> bool:
    low = MIN_PLAUSIBLE.get(currency or "", 0.01)
    high = MAX_PLAUSIBLE.get(currency or "", 1_000_000.0)
    return low <= price <= high


def script_blobs(page: str) -> Iterable[tuple[str, str]]:
    """Yield (kind, json_text) for every embedded JSON payload in the page."""
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.DOTALL | re.IGNORECASE,
    ):
        yield "ld+json", html.unescape(match.group(1))
    for match in re.finditer(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.DOTALL | re.IGNORECASE
    ):
        yield "__NEXT_DATA__", match.group(1)
    for match in re.finditer(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        page,
        re.DOTALL | re.IGNORECASE,
    ):
        yield "application/json", match.group(1)


def iter_nodes(node: Any) -> Iterable[Any]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_nodes(value)


def subtree_text(node: Any, limit: int = 20000) -> str:
    try:
        return json.dumps(node, ensure_ascii=False)[:limit]
    except (TypeError, ValueError):
        return ""


def lowered(node: dict) -> dict:
    return {str(key).lower(): value for key, value in node.items()}


# --------------------------------------------------------------------------- price extraction


def candidates_from_ld_json(data: Any, skus: list[str]) -> list[dict]:
    """Pull offers out of schema.org Product markup — the most reliable source."""
    found: list[dict] = []
    for node in iter_nodes(data):
        keys = lowered(node)
        node_type = keys.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if not any(isinstance(t, str) and t.lower() == "product" for t in types):
            continue
        product_text = subtree_text(node)
        matches_sku = any(sku in product_text for sku in skus) if skus else False
        offers = keys.get("offers")
        offer_nodes = [o for o in iter_nodes(offers) if isinstance(o, dict)] if offers else []
        for offer in offer_nodes:
            offer_keys = lowered(offer)
            currency = normalise_currency(offer_keys.get("pricecurrency"))
            for price_key in ("price", "lowprice"):
                price = to_number(offer_keys.get(price_key))
                if price is None or not plausible(price, currency):
                    continue
                found.append(
                    {
                        "price": price,
                        "currency": currency,
                        "method": f"ld+json:{price_key}",
                        "sku_match": matches_sku,
                        "was_price": next(
                            (
                                to_number(offer_keys[k])
                                for k in WAS_PRICE_KEYS
                                if to_number(offer_keys.get(k)) is not None
                            ),
                            None,
                        ),
                        "product_name": keys.get("name") if isinstance(keys.get("name"), str) else None,
                    }
                )
                break
    return found


def candidates_from_generic_json(data: Any, skus: list[str]) -> list[dict]:
    """Fallback: any dict carrying both a price-ish key and a currency-ish key."""
    found: list[dict] = []
    for node in iter_nodes(data):
        keys = lowered(node)
        currency = next(
            (normalise_currency(keys[key]) for key in CURRENCY_KEYS if normalise_currency(keys.get(key))),
            None,
        )
        if currency is None:
            continue
        for price_key in PRICE_KEYS:
            if price_key not in keys:
                continue
            value = keys[price_key]
            price = to_number(value.get("value") if isinstance(value, dict) else value)
            if price is None or not plausible(price, currency):
                continue
            node_text = subtree_text(node)
            found.append(
                {
                    "price": price,
                    "currency": currency,
                    "method": f"json:{price_key}",
                    "sku_match": any(sku in node_text for sku in skus) if skus else False,
                    "was_price": next(
                        (
                            to_number(keys[k])
                            for k in WAS_PRICE_KEYS
                            if to_number(keys.get(k)) is not None
                        ),
                        None,
                    ),
                    "product_name": None,
                }
            )
            break
    return found


def candidates_from_meta(page: str) -> list[dict]:
    def meta(prop: str) -> str | None:
        pattern = (
            r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']+)["\']'
        )
        match = re.search(pattern, page, re.IGNORECASE)
        if match:
            return match.group(1)
        pattern = (
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']'
            + re.escape(prop)
            + r'["\']'
        )
        match = re.search(pattern, page, re.IGNORECASE)
        return match.group(1) if match else None

    found: list[dict] = []
    for amount_prop, currency_prop in (
        ("product:price:amount", "product:price:currency"),
        ("og:price:amount", "og:price:currency"),
    ):
        price = to_number(meta(amount_prop))
        currency = normalise_currency(meta(currency_prop))
        if price is not None and plausible(price, currency):
            found.append(
                {
                    "price": price,
                    "currency": currency,
                    "method": f"meta:{amount_prop}",
                    "sku_match": False,
                    "was_price": None,
                    "product_name": None,
                }
            )
    return found


def candidates_from_visible_text(page: str) -> list[dict]:
    """Last resort: the first rendered price with a currency symbol next to it."""
    found: list[dict] = []
    pattern = r"(\d{1,3}(?:[\s  ]?\d{3})*(?:[.,]\d{1,2})?)\s*(Kč|€)"
    for match in re.finditer(pattern, page):
        currency = CURRENCY_SYMBOLS[match.group(2)]
        price = to_number(match.group(1))
        if price is not None and plausible(price, currency):
            found.append(
                {
                    "price": price,
                    "currency": currency,
                    "method": "visible-text",
                    "sku_match": False,
                    "was_price": None,
                    "product_name": None,
                }
            )
        if len(found) >= 5:
            break
    return found


def pick_best(candidates: list[dict], expect_currency: str | None) -> dict:
    """Choose the candidate most likely to be this product's current price."""
    if expect_currency:
        preferred = [c for c in candidates if c["currency"] in (expect_currency, None)]
        if preferred:
            candidates = preferred

    sku_matched = [c for c in candidates if c["sku_match"]]
    pool = sku_matched or candidates

    method_rank = {"ld+json": 0, "json": 1, "meta": 2, "visible-text": 3}
    def rank(candidate: dict) -> int:
        return method_rank.get(candidate["method"].split(":", 1)[0], 9)

    best_rank = min(rank(c) for c in pool)
    pool = [c for c in pool if rank(c) == best_rank]

    # Within the same source, a genuine sale price is the lowest quoted figure.
    return min(pool, key=lambda c: c["price"])


def extract_price(page: str, item: dict) -> dict:
    skus = [str(sku) for sku in item.get("skus", [])]
    candidates: list[dict] = []

    for kind, blob in script_blobs(page):
        try:
            data = json.loads(blob.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if kind == "ld+json":
            candidates.extend(candidates_from_ld_json(data, skus))
        else:
            candidates.extend(candidates_from_generic_json(data, skus))

    if not candidates:
        candidates.extend(candidates_from_meta(page))
    if not candidates:
        candidates.extend(candidates_from_visible_text(page))
    if not candidates:
        raise ParseError("no price found in JSON-LD, embedded JSON, meta tags or page text")

    best = pick_best(candidates, item.get("expect_currency"))
    best["candidate_count"] = len(candidates)
    return best


# --------------------------------------------------------------------------- reporting


def money(price: float, currency: str | None) -> str:
    if currency == "CZK":
        return f"{price:,.0f} Kč".replace(",", NBSP)
    if currency == "EUR":
        return f"{price:,.2f} €".replace(",", NBSP)
    return f"{price:,.2f} {currency or ''}".strip()


def build_issue_body(drops: list[dict], checked_at: str) -> str:
    lines = [
        "Price drop detected on the watched Decathlon items.",
        "",
        "| Item | Previous | Now | Change |",
        "| --- | ---: | ---: | ---: |",
    ]
    for drop in drops:
        currency = drop["currency"]
        previous = drop["previous_price"]
        pct = (previous - drop["price"]) / previous * 100
        lines.append(
            f"| [{drop['name']}]({drop['url']}) "
            f"| {money(previous, currency)} "
            f"| **{money(drop['price'], currency)}** "
            f"| −{pct:.0f}% |"
        )
    lines.append("")
    for drop in drops:
        currency = drop["currency"]
        lines.append(f"### {drop['name']}")
        lines.append(f"- Current price: **{money(drop['price'], currency)}**")
        if drop.get("was_price"):
            lines.append(f"- Listed original price: {money(drop['was_price'], currency)}")
        lines.append(
            f"- Previous price seen by the watchdog: {money(drop['previous_price'], currency)}"
        )
        lines.append(f"- Link: {drop['url']}")
        lines.append("")
    lines.append(f"_Checked at {checked_at}._")
    return "\n".join(lines)


def write_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        if "\n" in value:
            handle.write(f"{name}<<__EOF__\n{value}\n__EOF__\n")
        else:
            handle.write(f"{name}={value}\n")


# --------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Decathlon prices for drops.")
    parser.add_argument("--items", default=str(REPO_ROOT / "items.json"))
    parser.add_argument("--state", default=str(REPO_ROOT / "data" / "prices.json"))
    parser.add_argument("--issue-body", default=str(REPO_ROOT / "issue-body.md"))
    parser.add_argument(
        "--save-html",
        metavar="DIR",
        help="Dump each fetched page to DIR, for debugging selector breakage.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report findings without writing the state file.",
    )
    args = parser.parse_args()

    items = json.loads(Path(args.items).read_text(encoding="utf-8"))

    state_path = Path(args.state)
    state: dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("::warning::state file is corrupt, starting from an empty baseline", flush=True)

    checked_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    drops: list[dict] = []
    failures: list[str] = []

    for item in items:
        url = item["url"]
        name = item.get("name", url)
        try:
            page = fetch(url, item.get("accept_language", "en;q=0.9"))
        except FetchError as exc:
            failures.append(f"{name}: fetch failed ({exc})")
            print(f"::error::{name}: fetch failed ({exc})", flush=True)
            continue

        if args.save_html:
            debug_dir = Path(args.save_html)
            debug_dir.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", url)[-80:]
            (debug_dir / f"{slug}.html").write_text(page, encoding="utf-8")

        try:
            result = extract_price(page, item)
        except ParseError as exc:
            failures.append(f"{name}: {exc}")
            print(f"::error::{name}: {exc}", flush=True)
            continue

        price = result["price"]
        currency = result["currency"] or item.get("expect_currency")
        previous = state.get(url, {})
        previous_price = previous.get("price")
        previous_currency = previous.get("currency")

        note = ""
        if (
            isinstance(previous_price, (int, float))
            and previous_currency == currency
            and price < previous_price
        ):
            note = " — PRICE DROP"
            drops.append(
                {
                    "name": name,
                    "url": url,
                    "price": price,
                    "previous_price": float(previous_price),
                    "currency": currency,
                    "was_price": result.get("was_price"),
                }
            )
        elif previous_price is None:
            note = " — first run, recording baseline"

        print(
            f"{name}: {money(price, currency)} "
            f"(previous: {money(previous_price, previous_currency) if previous_price else 'n/a'}, "
            f"source: {result['method']}, candidates: {result['candidate_count']}){note}",
            flush=True,
        )

        entry = {
            "name": name,
            "price": price,
            "currency": currency,
            "source": result["method"],
            "last_checked": checked_at,
        }
        if result.get("was_price"):
            entry["listed_original_price"] = result["was_price"]
        entry["last_changed"] = (
            checked_at if previous_price != price else previous.get("last_changed", checked_at)
        )
        state[url] = entry

    if not args.dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
        )

    if drops:
        body = build_issue_body(drops, checked_at)
        Path(args.issue_body).write_text(body, encoding="utf-8")
        headline = drops[0]["name"] if len(drops) == 1 else f"{len(drops)} items"
        write_output("has_drops", "true")
        write_output("issue_title", f"Price drop: {headline} ({checked_at[:10]})")
        print(f"\n{len(drops)} price drop(s) detected.", flush=True)
    else:
        write_output("has_drops", "false")
        print("\nNo price drops detected.", flush=True)

    if failures:
        write_output("failures", "; ".join(failures))
        print(f"\n{len(failures)} item(s) could not be checked.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
