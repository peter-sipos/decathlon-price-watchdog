#!/usr/bin/env python3
"""Offline checks for the price extractor, run in CI and locally without network."""

import json
import os
import pathlib
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_prices  # noqa: E402
from check_prices import (  # noqa: E402
    FetchError,
    ParseError,
    build_fetch_url,
    build_fetch_urls,
    extract_from_listing,
    extract_price,
    fold,
    matches_item,
    fetch,
    looks_blocked,
    money,
    redact,
    to_number,
)

CZ_ITEM = {"url": "https://www.decathlon.cz/p/x/_/R-p-348187", "skus": ["348187"], "expect_currency": "CZK"}
SK_ITEM = {"url": "https://www.decathlon.sk/p/348187-368243-x.html", "skus": ["348187"], "expect_currency": "EUR"}


def shipped_items():
    root = pathlib.Path(__file__).resolve().parent.parent
    return json.loads((root / "items.json").read_text("utf-8"))


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


class CloudflareDetectionTests(unittest.TestCase):
    """Regression: the localised interstitial was passing as a successful fetch."""

    def cf_page(self, title):
        return (
            f"<html><head><title>{title}</title></head><body>"
            "<div id=\"challenge-running\"></div>" + "padding " * 500 + "</body></html>"
        )

    def test_czech_interstitial_is_blocked(self):
        self.assertIsNotNone(looks_blocked(self.cf_page("Okam\u017eik\u2026")))

    def test_slovak_interstitial_is_blocked(self):
        self.assertIsNotNone(looks_blocked(self.cf_page("Len chv\u00ed\u013eu...")))

    def test_english_interstitial_is_blocked(self):
        self.assertIsNotNone(looks_blocked(self.cf_page("Just a moment...")))

    def test_challenge_platform_script_is_blocked(self):
        body = "<html><head><title>Pol\u0161t\u00e1\u0159</title></head><body>"
        body += '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'
        body += "padding " * 500 + "</body></html>"
        self.assertIn("challenge-platform", looks_blocked(body))

    def test_real_page_mentioning_turnstile_is_not_blocked(self):
        """A login widget reference must not be mistaken for an interstitial."""
        body = (
            "<html><head><title>Pol\u0161t\u00e1\u0159 Ultim Comfort XL</title></head><body>"
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
            + "product copy " * 500
            + "</body></html>"
        )
        self.assertIsNone(looks_blocked(body))


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


class ListingExtractionTests(unittest.TestCase):
    """Heureka search results: many products on one page, pick the right row."""

    CZ_XL = {
        "match_all": ["ultim comfort", "xl"],
        "match_none": [],
        "expect_currency": "CZK",
    }
    CZ_PLAIN = {
        "match_all": ["ultim comfort"],
        "match_none": ["xl"],
        "expect_currency": "CZK",
    }

    LISTING = """<html><head><title>ultim comfort polstar - Heureka.cz</title></head><body>
      <h1>Vysledky hledani pro ultim comfort polstar</h1>
      <article><a href="/x">Quechua Polstar Ultim Comfort XL</a>
        <span>Hodnoceni 4,5</span><span>od&nbsp;399&nbsp;K&#269;</span></article>
      <article><a href="/y">Quechua Pol&#353;t&#225;&#345; Ultim Comfort</a>
        <span>Hodnoceni 4,7</span><span>od&nbsp;249&nbsp;K&#269;</span></article>
      <article><a href="/z">Coleman Nafukovaci polstar Comfort</a>
        <span>od&nbsp;199&nbsp;K&#269;</span></article>
    </body></html>"""

    def test_picks_the_xl_variant(self):
        result = extract_from_listing(self.LISTING, self.CZ_XL)
        self.assertEqual(result["price"], 399.0)
        self.assertIn("XL", result["matched_name"])

    def test_excludes_xl_for_the_plain_variant(self):
        """'Ultim Comfort' is a substring of 'Ultim Comfort XL' - match_none guards it."""
        result = extract_from_listing(self.LISTING, self.CZ_PLAIN)
        self.assertEqual(result["price"], 249.0)
        self.assertNotIn("XL", result["matched_name"])

    def test_ignores_unrelated_products(self):
        for item in (self.CZ_XL, self.CZ_PLAIN):
            self.assertNotEqual(extract_from_listing(self.LISTING, item)["price"], 199.0)

    def test_diacritics_are_folded(self):
        """The listing writes 'Polstar' with diacritics; match_all does not."""
        result = extract_from_listing(self.LISTING, self.CZ_PLAIN)
        self.assertIn("Ultim Comfort", result["matched_name"])

    def test_slovak_euro_prices(self):
        listing = """<html><body>
          <article><a href="/a">Quechua Vankus Ultim Comfort XL</a>
            <span>od&nbsp;12,99&nbsp;&euro;</span></article>
        </body></html>"""
        item = {"match_all": ["ultim comfort", "xl"], "match_none": [], "expect_currency": "EUR"}
        self.assertEqual(extract_from_listing(listing, item)["price"], 12.99)

    def test_missing_product_reports_what_was_on_the_page(self):
        item = {"match_all": ["nonexistent product"], "match_none": [], "expect_currency": "CZK"}
        with self.assertRaises(ParseError) as caught:
            extract_from_listing(self.LISTING, item)
        self.assertIn("Ultim Comfort", str(caught.exception))

    def test_jsonld_listing_is_preferred(self):
        payload = {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "Product",
                    "name": "Quechua Polstar Ultim Comfort XL",
                    "offers": {"@type": "Offer", "price": "379", "priceCurrency": "CZK"},
                }
            ],
        }
        page_html = f'<html><body><script type="application/ld+json">{json.dumps(payload)}</script>'
        page_html += "<article><a>Quechua Polstar Ultim Comfort XL</a><span>od 399 Kc</span></article>"
        page_html += "</body></html>"
        result = extract_from_listing(page_html, self.CZ_XL)
        self.assertEqual(result["price"], 379.0)
        self.assertEqual(result["method"], "listing-json")


class TokenMatchingTests(unittest.TestCase):
    def test_short_tokens_match_whole_words_only(self):
        item = {"match_all": ["xl"], "match_none": []}
        self.assertTrue(matches_item("Polstar Ultim Comfort XL", item))
        self.assertFalse(matches_item("Polstar Ultim Comfort XLarge Deluxe", item))

    def test_fold_strips_diacritics(self):
        self.assertEqual(fold("Polštář XL"), "polstar xl")


class PlainTextListingTests(unittest.TestCase):
    """A reader proxy returns markdown, not HTML; lines become the segments."""

    MARKDOWN = """# Vysledky hledani

Quechua Pol\u0161t\u00e1\u0159 Ultim Comfort XL
Hodnoceni 4,5
od 399 K\u010d

Quechua Pol\u0161t\u00e1\u0159 Ultim Comfort
od 249 K\u010d
"""

    def test_markdown_listing_is_parsed(self):
        item = {"match_all": ["ultim comfort", "xl"], "match_none": [], "expect_currency": "CZK"}
        self.assertEqual(extract_from_listing(self.MARKDOWN, item)["price"], 399.0)

    def test_markdown_respects_match_none(self):
        item = {"match_all": ["ultim comfort"], "match_none": ["xl"], "expect_currency": "CZK"}
        self.assertEqual(extract_from_listing(self.MARKDOWN, item)["price"], 249.0)


class ProxyTemplateTests(unittest.TestCase):
    """A scraping service is wired in by config; its key must never leak."""

    URL = "https://www.heureka.cz/?h%5Bfraze%5D=ultim+comfort"

    def setUp(self):
        check_prices._SECRETS.clear()
        self.addCleanup(check_prices._SECRETS.clear)
        os.environ.pop("SCRAPER_API_KEY", None)
        self.addCleanup(lambda: os.environ.pop("SCRAPER_API_KEY", None))

    def test_no_template_returns_url_unchanged(self):
        self.assertEqual(build_fetch_url({}, self.URL), self.URL)

    def test_key_and_encoded_url_are_substituted(self):
        os.environ["SCRAPER_API_KEY"] = "secret123"
        item = {"proxy_template": "https://api.example.com/?api_key={key}&url={url_encoded}"}
        built = build_fetch_url(item, self.URL)
        self.assertIn("api_key=secret123", built)
        self.assertIn("https%3A%2F%2Fwww.heureka.cz", built)
        self.assertNotIn("?h%5Bfraze", built.split("url=")[1])  # fully encoded, not raw

    def test_missing_key_fails_loudly(self):
        item = {"proxy_template": "https://api.example.com/?api_key={key}&url={url_encoded}"}
        with self.assertRaises(FetchError) as caught:
            build_fetch_url(item, self.URL)
        self.assertIn("SCRAPER_API_KEY", str(caught.exception))

    def test_custom_key_env(self):
        os.environ["MY_KEY"] = "abc"
        self.addCleanup(lambda: os.environ.pop("MY_KEY", None))
        item = {"proxy_template": "https://x/?k={key}", "proxy_key_env": "MY_KEY"}
        self.assertEqual(build_fetch_url(item, self.URL), "https://x/?k=abc")

    def test_key_is_redacted_from_text(self):
        os.environ["SCRAPER_API_KEY"] = "supersecret"
        build_fetch_url({"proxy_template": "https://x/?k={key}"}, self.URL)
        self.assertNotIn("supersecret", redact("error calling https://x/?k=supersecret"))
        self.assertIn("***REDACTED***", redact("k=supersecret"))

    def test_keyless_template_needs_no_secret(self):
        item = {"proxy_template": "https://r.jina.ai/{url}"}
        self.assertEqual(build_fetch_url(item, self.URL), f"https://r.jina.ai/{self.URL}")


class ProxyEscalationTests(unittest.TestCase):
    """Cheapest tier first; escalate only when the response comes back blocked."""

    URL = "https://www.heureka.cz/?q=x"

    def setUp(self):
        check_prices._SECRETS.clear()
        self.addCleanup(check_prices._SECRETS.clear)
        os.environ["SCRAPER_API_KEY"] = "k"
        self.addCleanup(lambda: os.environ.pop("SCRAPER_API_KEY", None))

    def test_templates_expand_in_order(self):
        item = {"proxy_templates": ["https://a/?k={key}&u={url_encoded}", "https://b/?k={key}"]}
        built = build_fetch_urls(item, self.URL)
        self.assertEqual(len(built), 2)
        self.assertTrue(built[0].startswith("https://a/"))
        self.assertTrue(built[1].startswith("https://b/"))

    def test_singular_template_still_works(self):
        item = {"proxy_template": "https://a/?k={key}"}
        self.assertEqual(build_fetch_urls(item, self.URL), ["https://a/?k=k"])

    def test_no_template_yields_the_plain_url(self):
        self.assertEqual(build_fetch_urls({}, self.URL), [self.URL])

    def test_build_fetch_url_returns_the_cheapest(self):
        item = {"proxy_templates": ["https://cheap/?k={key}", "https://dear/?k={key}"]}
        self.assertEqual(build_fetch_url(item, self.URL), "https://cheap/?k=k")

    def test_missing_key_reported_once_for_the_whole_ladder(self):
        os.environ.pop("SCRAPER_API_KEY")
        item = {"proxy_templates": ["https://a/?k={key}", "https://b/?k={key}"]}
        with self.assertRaises(FetchError):
            build_fetch_urls(item, self.URL)

    def test_configured_ladder_is_cheapest_first(self):
        """The shipped config must not pay for rendering before trying without."""
        for item in shipped_items():
            ladder = item["proxy_templates"]
            self.assertNotIn("render=true", ladder[0], item["id"])
            self.assertNotIn("ultra_premium", ladder[0], item["id"])
            self.assertIn("render=true", ladder[1], item["id"])


class ShippedConfigTests(unittest.TestCase):
    """Guard the config itself: a typo here silently watches the wrong thing."""

    def test_every_item_has_a_unique_id(self):
        ids = [item["id"] for item in shipped_items()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_four_products_are_watched(self):
        urls = {item["url"] for item in shipped_items()}
        self.assertEqual(len(urls), 4)
        for fragment in ("348187", "308736"):
            self.assertTrue(
                any(fragment in url for url in urls), f"{fragment} is not watched"
            )

    def test_each_item_targets_its_own_sku(self):
        """Both shops list the XL and non-XL pillow; the SKU keeps them apart."""
        for item in shipped_items():
            self.assertTrue(item["skus"], item["id"])
            self.assertTrue(
                any(sku in item["url"] for sku in item["skus"]),
                f"{item['id']}: no SKU appears in its own URL",
            )

    def test_currency_matches_the_shop(self):
        for item in shipped_items():
            expected = "CZK" if ".cz/" in item["url"] else "EUR"
            self.assertEqual(item["expect_currency"], expected, item["id"])


class RealDecathlonPageTests(unittest.TestCase):
    """Against markup captured from the live CZ product page.

    The price sits at offers.priceSpecification.price, not offers.price — a
    naive `offers["price"]` lookup finds nothing here and falls through to
    guessing from page text.
    """

    def setUp(self):
        fixture = pathlib.Path(__file__).resolve().parent / "fixtures" / "decathlon_cz_308736.html"
        self.page = fixture.read_text(encoding="utf-8")
        self.item = next(i for i in shipped_items() if i["id"] == "cz-ultim-comfort")

    def test_extracts_the_real_price(self):
        result = extract_price(self.page, self.item)
        self.assertEqual(result["price"], 499.0)
        self.assertEqual(result["currency"], "CZK")
        self.assertTrue(result["method"].startswith("ld+json"))

    def test_price_is_nested_under_price_specification(self):
        """Guards the recursion into offers; a flat lookup would miss it."""
        offers = json.loads(
            re.search(
                r'<script type="application/ld\+json">(.*?)</script>', self.page, re.DOTALL
            ).group(1)
        )["@graph"][1]["offers"]
        self.assertNotIn("price", offers)
        self.assertEqual(offers["priceSpecification"]["price"], 499)

    def test_bundle_and_shipping_prices_are_not_picked_up(self):
        """The page also shows 149/249 Kč bundle items and a 2 000 Kč threshold."""
        result = extract_price(self.page, self.item)
        self.assertEqual(result["candidate_count"], 1)
        self.assertNotIn(result["price"], (149.0, 249.0, 2000.0))

    def test_real_page_is_not_flagged_as_blocked(self):
        self.assertIsNone(looks_blocked(self.page))
