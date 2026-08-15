#!/usr/bin/env python3
"""Offline checks for the price extractor, run in CI and locally without network."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_prices  # noqa: E402
from check_prices import (  # noqa: E402
    FetchError,
    extract_price,
    fetch,
    looks_blocked,
    money,
    to_number,
)

CZ_ITEM = {"url": "https://www.decathlon.cz/p/x/_/R-p-348187", "skus": ["348187"], "expect_currency": "CZK"}
SK_ITEM = {"url": "https://www.decathlon.sk/p/348187-368243-x.html", "skus": ["348187"], "expect_currency": "EUR"}


def page(*blocks: str) -> str:
    return "<html><head>" + "".join(blocks) + "</head><body></body></html>"


def ld(payload) -> str:
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


class ToNumberTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(to_number("1.299,00"), 1299.0)
        self.assertEqual(to_number("1 299"), 1299.0)
        self.assertEqual(to_number("1\xa0299 Kč"), 1299.0)
        self.assertEqual(to_number("24,99"), 24.99)
        self.assertEqual(to_number("1,299.00"), 1299.0)
        self.assertEqual(to_number(13.99), 13.99)
        self.assertIsNone(to_number("zdarma"))
        self.assertIsNone(to_number(True))


class ExtractTests(unittest.TestCase):
    def test_prefers_sku_matched_product_over_recommendations(self):
        html = page(
            ld({"@type": "Product", "sku": "999999", "name": "Related tent",
                "offers": {"@type": "Offer", "price": "9.99", "priceCurrency": "EUR"}}),
            ld({"@type": "Product", "sku": "348187", "name": "Ultim Comfort XL",
                "offers": {"@type": "Offer", "price": "13.99", "priceCurrency": "EUR"}}),
        )
        result = extract_price(html, SK_ITEM)
        self.assertEqual(result["price"], 13.99)
        self.assertTrue(result["method"].startswith("ld+json"))

    def test_graph_and_offer_list(self):
        html = page(ld({"@graph": [{"@type": ["Product"], "sku": "348187", "offers": [
            {"@type": "Offer", "price": 349, "priceCurrency": "CZK"},
            {"@type": "Offer", "price": 399, "priceCurrency": "CZK"},
        ]}]}))
        self.assertEqual(extract_price(html, CZ_ITEM)["price"], 349.0)

    def test_ignores_implausible_prices(self):
        html = page(
            ld({"@type": "Product", "sku": "348187",
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "CZK"}}),
            '<meta property="product:price:amount" content="349">'
            '<meta property="product:price:currency" content="CZK">',
        )
        self.assertEqual(extract_price(html, CZ_ITEM)["price"], 349.0)

    def test_generic_json_fallback(self):
        html = page(
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"product": {"id": "348187",
                "currentPrice": {"value": "12,49"}, "currency": "EUR",
                "originalPrice": "16.99"}}})
            + "</script>"
        )
        result = extract_price(html, SK_ITEM)
        self.assertEqual(result["price"], 12.49)
        self.assertEqual(result["was_price"], 16.99)

    def test_visible_text_last_resort_skips_shipping_banner(self):
        # "50 €" free-shipping copy must not win over the real product price.
        html = "<html><body><p>Doprava zdarma nad 50 €</p><p>13,99 €</p></body></html>"
        self.assertEqual(extract_price(html, SK_ITEM)["price"], 13.99)

    def test_raises_when_no_price_present(self):
        with self.assertRaises(Exception):
            extract_price("<html><body>Produkt nie je dostupný</body></html>", SK_ITEM)


class MoneyTests(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual(money(1299.0, "CZK"), "1\u00a0299 Kč")
        self.assertEqual(money(13.99, "EUR"), "13.99 €")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class BlockDetectionTests(unittest.TestCase):
    def test_akamai_denial_is_detected(self):
        body = "<html><body><h1>Access Denied</h1><p>Reference #18.abc</p></body></html>" + "x" * 3000
        self.assertIn("access denied", looks_blocked(body))

    def test_truncated_response_is_detected(self):
        self.assertIn("suspiciously short", looks_blocked("<html>nope</html>"))

    def test_real_product_page_passes(self):
        self.assertIsNone(looks_blocked("<html>" + "product copy " * 500 + "</html>"))


class FetchFallbackTests(unittest.TestCase):
    """The whole point of the chain: a 403 on one client must try the next."""

    def setUp(self):
        self.calls = []
        self._original = check_prices.FETCH_STRATEGIES
        self.addCleanup(setattr, check_prices, "FETCH_STRATEGIES", self._original)

    def install(self, *behaviours):
        def make(name, behaviour):
            def strategy(url, accept_language, timeout):
                self.calls.append(name)
                if isinstance(behaviour, Exception):
                    raise behaviour
                return behaviour
            return (name, strategy)
        check_prices.FETCH_STRATEGIES = tuple(make(n, b) for n, b in behaviours)

    def test_falls_through_403_to_the_next_client(self):
        good = "<html>" + "product " * 500 + "</html>"
        self.install(("urllib", FetchError("HTTP 403", "<h1>Access Denied</h1>")), ("chrome", good))
        text, strategy = fetch("https://example.test/p", "cs-CZ")
        self.assertEqual(strategy, "chrome")
        self.assertEqual(text, good)
        self.assertEqual(self.calls, ["urllib", "chrome"])

    def test_bot_check_body_counts_as_failure_not_success(self):
        blocked = "<html><h1>Access Denied</h1>" + "x" * 3000 + "</html>"
        good = "<html>" + "product " * 500 + "</html>"
        self.install(("urllib", blocked), ("chrome", good))
        _, strategy = fetch("https://example.test/p", "cs-CZ")
        self.assertEqual(strategy, "chrome")

    def test_reports_every_strategy_when_all_fail(self):
        self.install(("urllib", FetchError("HTTP 403")), ("chrome", FetchError("no Chrome")))
        with self.assertRaises(FetchError) as caught:
            fetch("https://example.test/p", "cs-CZ")
        self.assertIn("urllib: HTTP 403", str(caught.exception))
        self.assertIn("chrome: no Chrome", str(caught.exception))
