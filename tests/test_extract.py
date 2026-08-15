#!/usr/bin/env python3
"""Offline checks for the price extractor, run in CI and locally without network."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_prices import extract_price, money, to_number  # noqa: E402

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
