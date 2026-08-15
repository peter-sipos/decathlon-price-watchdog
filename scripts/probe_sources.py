#!/usr/bin/env python3
"""Report which price sources are reachable from this machine.

Decathlon and Heureka both sit behind Cloudflare, which challenges GitHub's
datacenter IP ranges. Rather than discover that one site per workflow run, this
probes a whole list at once and prints what each one returned.

Run it from the Actions tab ("Probe price sources") and read the summary table.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_prices import (  # noqa: E402
    FetchError,
    fetch_curl,
    fetch_urllib,
    looks_blocked,
    page_title,
)

CZ_PRODUCT = "https://www.decathlon.cz/p/polstar-ultim-comfort-xl/_/R-p-348187"
CZ_SEARCH = "https://www.heureka.cz/?h%5Bfraze%5D=ultim+comfort+polstar"

# Each entry is (label, url). Homepages are probed alongside search URLs because a
# guessed search path returning 404 still proves the domain itself is reachable.
CANDIDATES: list[tuple[str, str]] = [
    ("decathlon.cz product (control)", CZ_PRODUCT),
    ("heureka.cz search (control)", CZ_SEARCH),
    # Reader proxies fetch the page from their own servers, so the origin sees
    # their IP rather than GitHub's.
    ("r.jina.ai -> decathlon.cz", f"https://r.jina.ai/{CZ_PRODUCT}"),
    ("r.jina.ai -> heureka.cz", f"https://r.jina.ai/{CZ_SEARCH}"),
    ("allorigins -> decathlon.cz", "https://api.allorigins.win/raw?url=" + CZ_PRODUCT),
    # Czech/Slovak comparison sites that may not be behind Cloudflare.
    ("zbozi.cz home", "https://www.zbozi.cz/"),
    ("zbozi.cz search", "https://www.zbozi.cz/hledej/?q=ultim+comfort+polstar"),
    ("pricemania.sk home", "https://www.pricemania.sk/"),
    ("pricemania.sk search", "https://www.pricemania.sk/hladaj?q=ultim+comfort"),
    ("najnakup.sk home", "https://www.najnakup.sk/"),
    ("najnakup.sk search", "https://www.najnakup.sk/vyhladavanie/?q=ultim+comfort"),
    ("glami.cz search", "https://www.glami.cz/?q=ultim+comfort+polstar"),
]

PRICE = re.compile(r"(\d{1,3}(?:[\s ]?\d{3})*(?:[.,]\d{1,2})?)\s*(Kč|€|EUR|CZK)", re.IGNORECASE)


def probe(url: str, timeout: int) -> dict:
    """Try the cheap clients and report what came back, without judging it."""
    attempts: list[str] = []
    for name, strategy in (("urllib", fetch_urllib), ("curl", fetch_curl)):
        try:
            body = strategy(url, "cs-CZ,cs;q=0.9,en;q=0.6", timeout)
        except FetchError as exc:
            attempts.append(f"{name}={exc}")
            continue
        blocked = looks_blocked(body)
        prices = [f"{m.group(1)} {m.group(2)}" for m in PRICE.finditer(body)][:3]
        return {
            "ok": blocked is None,
            "via": name,
            "note": blocked or "reachable",
            "bytes": len(body),
            "title": page_title(body)[:40],
            "has_product": "ultim comfort" in body.lower(),
            "prices": prices,
            "attempts": attempts,
        }
    return {
        "ok": False,
        "via": "-",
        "note": "; ".join(attempts),
        "bytes": 0,
        "title": "",
        "has_product": False,
        "prices": [],
        "attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()

    results: list[tuple[str, str, dict]] = []
    for label, url in CANDIDATES:
        print(f"probing {label} ...", flush=True)
        outcome = probe(url, args.timeout)
        results.append((label, url, outcome))
        print(f"    {outcome['note'][:160]}", flush=True)
        if outcome["prices"]:
            print(f"    prices seen: {', '.join(outcome['prices'])}", flush=True)

    print("\n" + "=" * 78)
    print(f"{'SOURCE':34} {'OK':4} {'VIA':7} {'BYTES':>8}  {'PRODUCT':8} PRICES")
    print("=" * 78)
    for label, _url, outcome in results:
        print(
            f"{label:34} {'yes' if outcome['ok'] else 'no':4} {outcome['via']:7} "
            f"{outcome['bytes']:>8}  {'yes' if outcome['has_product'] else 'no':8} "
            f"{', '.join(outcome['prices']) or '-'}"
        )

    usable = [label for label, _u, o in results if o["ok"] and o["has_product"] and o["prices"]]
    print("=" * 78)
    if usable:
        print("Usable sources (reachable, mention the product, and show a price):")
        for label in usable:
            print(f"  - {label}")
    else:
        print("No candidate returned a usable page from this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
