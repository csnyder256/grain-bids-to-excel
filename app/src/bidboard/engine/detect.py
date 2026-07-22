"""Vendor fingerprints. Cheap string checks on Tier-1 HTML that (a) decide
whether JS rendering is needed and (b) supply DOM selector hints. Hints
accelerate; the generic extractor is always the fallback."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .types import VendorHints

_BARCHART_MARKERS = (
    "websol.barchart.com", "agricharts.com", "barchart.com/ondemand",
    "bc-cash-bids", "bc-datatable", "acappinstance", "barchart-",
)
_DTN_MARKERS = (
    "hosted.dtn.com", "dtn.cashbids", "createcashbidstablewidget", "dtn-cash",
)
_BUSHEL_MARKERS = ("sevencolumnsbigfirst", "cbcommodity", "provided by bushel",
                   "/ws/ws.asmx")
_CLOUDFLARE_MARKERS = ("just a moment", "cf-chl", "_cf_chl_opt",
                       "challenge-platform")

_IFRAME_BID_RE = re.compile(
    r"(?i)cashbid|cash-bid|grain|bids|dtn|barchart|agricharts"
)


def detect_vendor(html: str, url: str) -> VendorHints:
    low = html.lower()

    iframe_urls: list[str] = []
    try:
        soup = BeautifulSoup(html, "lxml")
        for frame in soup.find_all("iframe", src=True):
            src = frame["src"]
            if _IFRAME_BID_RE.search(src):
                iframe_urls.append(urljoin(url, src))
    except Exception:
        pass

    if any(m in low for m in _CLOUDFLARE_MARKERS):
        return VendorHints(vendor="cloudflare", needs_js=True,
                           iframe_urls=tuple(iframe_urls))

    if any(m in low for m in _BARCHART_MARKERS):
        return VendorHints(
            vendor="barchart", needs_js=True,
            wait_selectors=("table.bc-datatable", "[class*=bc-cash] table",
                            "[class*=cashbids] table", "table"),
            container_selectors=("table.bc-datatable", "[class*=bc-cash]",
                                 "[class*=cashbids]"),
            iframe_urls=tuple(iframe_urls),
        )

    if any(m in low for m in _DTN_MARKERS):
        return VendorHints(
            vendor="dtn", needs_js=True,
            wait_selectors=("[class*=dtn] table", ".dtn-cash-bids table",
                            "table"),
            container_selectors=("[class*=dtn]", ".dtn-cash-bids"),
            iframe_urls=tuple(iframe_urls),
        )

    if any(m in low for m in _BUSHEL_MARKERS):
        return VendorHints(
            vendor="bushel_ssr", needs_js=False,
            container_selectors=("div.cbCommodity", "ul[class*=Columns]"),
            iframe_urls=tuple(iframe_urls),
        )

    return VendorHints(vendor="unknown", iframe_urls=tuple(iframe_urls))
