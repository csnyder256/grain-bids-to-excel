"""Same-domain bid-page discovery.

Sources: anchor tags PLUS url-shaped strings inside inline <script> JSON - 
the latter is what surfaces nav-menu siblings on multi-location elevator
(/cashbidssingle-2162 ... -2167 live in a JS nav structure, not in <a> tags).

Links are scored, thresholded, and SUGGESTED to the user - never
auto-scanned.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .types import ScoredLink

STRONG_TOKENS = ("cashbid", "cash-bid", "cash_bid", "cashbids")
MID_TOKENS = ("bids", "bid")
WEAK_TOKENS = ("grain", "markets", "market", "prices", "quotes", "locations", "elevator")
PENALTY_TOKENS = (
    "news", "weather", "agronomy", "careers", "career", "about", "contact",
    "login", "admin", "blog", "feed", "energy", "fuel", "privacy", "terms",
    "photo", "gallery", "event", "history", "safety", "employment",
)

SCRIPT_URL_RE = re.compile(r"[\"'](/[A-Za-z0-9_\-/]{3,60})[\"']")
ACCEPT_THRESHOLD = 3.0


def _registered_domain(netloc: str) -> str:
    parts = netloc.lower().lstrip("www.").split(":")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else netloc.lower()


def _numeric_sibling(path: str, known_paths: set[str]) -> bool:
    """True if `path` matches a known bid URL except for a numeric suffix
    or numeric path segment (the /cashbidssingle-2163 -> -2164 pattern)."""
    stem = re.sub(r"\d+", "#", path)
    for known in known_paths:
        if not known:
            continue
        if re.sub(r"\d+", "#", known) == stem and known != path:
            return True
    return False


def score_link(
    url: str, anchor_text: str, known_bid_paths: set[str]
) -> tuple[float, list[str]]:
    path = urlparse(url).path.lower()
    text = (anchor_text or "").lower()
    haystack = path + " " + text
    score = 0.0
    reasons: list[str] = []

    if any(t in haystack for t in STRONG_TOKENS):
        score += 4
        reasons.append("mentions cash bids")
    elif any(re.search(rf"\b{t}\b", haystack) for t in MID_TOKENS):
        score += 2
        reasons.append("mentions bids")

    weak_hits = sum(1 for t in WEAK_TOKENS if t in haystack)
    if weak_hits:
        add = min(weak_hits, 2)
        score += add
        reasons.append("grain/market wording")

    if "cash bid" in text:
        score += 3
        reasons.append("link text says cash bids")

    if _numeric_sibling(path, known_bid_paths):
        score += 5
        reasons.append("sibling of a known bid page")

    penalties = sum(1 for t in PENALTY_TOKENS if t in haystack)
    if penalties:
        score -= 3 * penalties
        reasons.append("looks unrelated")

    return score, reasons


def discover_bid_links(
    html: str, base_url: str, known_bid_urls: set[str] | None = None
) -> list[ScoredLink]:
    known_bid_urls = known_bid_urls or set()
    known_paths = {urlparse(u).path.lower() for u in known_bid_urls}
    base_domain = _registered_domain(urlparse(base_url).netloc)

    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, tuple[str, float, list[str]]] = {}

    def consider(raw_url: str, anchor_text: str) -> None:
        raw_url = (raw_url or "").strip()
        if not raw_url or raw_url.startswith(("mailto:", "tel:", "javascript:", "#")):
            return
        absolute = urljoin(base_url, raw_url)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            return
        if _registered_domain(parsed.netloc) != base_domain:
            return
        clean = absolute.split("#")[0].rstrip("/")
        if not clean or clean == base_url.rstrip("/"):
            return
        if clean.lower() in {u.lower().rstrip("/") for u in known_bid_urls}:
            return
        score, reasons = score_link(clean, anchor_text, known_paths)
        prior = seen.get(clean)
        if prior is None or score > prior[1]:
            seen[clean] = (anchor_text, score, reasons)

    for a in soup.find_all("a", href=True):
        consider(a["href"], a.get_text(" ", strip=True))

    for script in soup.find_all("script"):
        content = script.string or script.get_text() or ""
        for match in SCRIPT_URL_RE.findall(content):
            consider(match, "")

    links = [
        ScoredLink(url=url, anchor_text=text, score=score, reasons=tuple(reasons))
        for url, (text, score, reasons) in seen.items()
        if score >= ACCEPT_THRESHOLD
    ]
    links.sort(key=lambda l: l.score, reverse=True)
    return links
