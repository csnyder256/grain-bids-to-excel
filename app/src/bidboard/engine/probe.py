"""Light-weight URL probe for the add-company flow: fetch a page, say in
one sentence what we found, and list its likely bid pages. The full
extractor (scan pipeline) is the real reader - this is the preview."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .discover import discover_bid_links
from .fetcher import TieredFetcher
from .types import Flag

COMMODITY_WORDS = {
    "corn": "Corn",
    "soybean": "Soybeans",
    "beans": "Soybeans",
    "wheat": "Wheat",
    "oats": "Oats",
    "milo": "Milo",
    "sorghum": "Sorghum",
    "barley": "Barley",
    "canola": "Canola",
    "rye": "Rye",
    "sunflower": "Sunflowers",
}

PRICE_RE = re.compile(r"\b\d{1,2}\.\d{2,4}\b")
BASIS_RE = re.compile(r"[+-]\s?\d{0,2}\.\d{2,4}\b")


def normalize_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return raw


def probe_url(fetcher: TieredFetcher, url: str) -> dict:
    """Returns a dict the UI renders directly:
    { ok, url, title, looks_like_bid_page, commodities, bid_links,
      summary, flags }"""
    normalized = normalize_url(url)
    if normalized is None:
        return {
            "ok": False,
            "url": url,
            "summary": "That doesn't look like a web address.",
            "flags": [{
                "code": "BAD_URL", "severity": "error",
                "message": "That doesn't look like a web address.",
                "suggestion": "Try something like www.companyname.com",
            }],
        }

    fetched = fetcher.fetch(normalized)
    if not fetched.ok or not fetched.html:
        flag = fetched.flags[0] if fetched.flags else Flag(
            "NETWORK_FAIL", "error", "We couldn't read that page.", "Try again."
        )
        return {
            "ok": False,
            "url": normalized,
            "summary": flag.message,
            "flags": [flag.__dict__],
        }

    soup = BeautifulSoup(fetched.html, "lxml")
    title = (soup.title.get_text(strip=True) if soup.title else "")[:120]

    text = soup.get_text(" ", strip=True).lower()
    commodities = sorted({
        label for word, label in COMMODITY_WORDS.items() if word in text
    })

    price_hits = len(PRICE_RE.findall(text[:200_000]))
    basis_hits = len(BASIS_RE.findall(text[:200_000]))
    path = urlparse(fetched.final_url).path.lower()
    path_says_bids = any(t in path for t in ("cashbid", "cash-bid", "bid"))
    looks_like_bid_page = bool(
        commodities and (price_hits >= 3 or basis_hits >= 2)
    ) or (path_says_bids and price_hits >= 3)

    bid_links = discover_bid_links(
        fetched.html, fetched.final_url, {fetched.final_url}
    )

    if looks_like_bid_page:
        summary = "Found a cash bids page"
        if commodities:
            summary += " - " + ", ".join(commodities[:4])
        extra = [l for l in bid_links if l.score >= 5]
        if extra:
            summary += f" (and {len(extra)} more location page{'s' if len(extra) != 1 else ''} on this site)"
        summary += "."
    elif bid_links:
        summary = (
            f"This looks like the company's site - we spotted "
            f"{len(bid_links)} page{'s' if len(bid_links) != 1 else ''} that "
            "may hold their cash bids."
        )
    else:
        summary = (
            "The page loaded, but we didn't spot a cash bids table yet - "
            "you can still add it and point us at the right page."
        )

    return {
        "ok": True,
        "url": fetched.final_url,
        "title": title,
        "looks_like_bid_page": looks_like_bid_page,
        "commodities": commodities,
        "bid_links": [
            {"url": l.url, "text": l.anchor_text, "score": l.score,
             "reasons": list(l.reasons)}
            for l in bid_links[:12]
        ],
        "summary": summary,
        "flags": [],
    }
