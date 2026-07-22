"""Business name → website resolution. No AI: web search + heuristics,
and the USER always confirms the match.

DuckDuckGo's HTML endpoint first, Bing as fallback. Results are scored,
optionally verified (does the site visibly have a cash-bids page?), and
returned ranked with human-readable evidence for the confirm dialog.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from .config import EngineConfig
from .discover import discover_bid_links
from .fetcher import TieredFetcher
from .types import Flag, ResolveResult, UrlCandidate

log = logging.getLogger("bidboard.resolve")

BLOCKLIST_DOMAINS = (
    "facebook.com", "linkedin.com", "instagram.com", "x.com", "twitter.com",
    "yelp.com", "yellowpages.com", "mapquest.com", "dnb.com", "zoominfo.com",
    "bloomberg.com", "indeed.com", "glassdoor.com", "wikipedia.org",
    "barchart.com", "agweb.com", "agriculture.com", "farmbucks.com",
    "agsist.com", "dtnpf.com", "youtube.com", "apps.apple.com",
    "play.google.com", "duckduckgo.com", "bing.com", "microsoft.com",
    "buzzfile.com", "manta.com", "bizapedia.com", "opencorporates.com",
)

BID_WORDS_RE = re.compile(r"(?i)cash\s*bids?|grain\s*prices?|cash\s*grain")


def _registered_domain(netloc: str) -> str:
    host = netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _domain_tokens(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", domain.rsplit(".", 1)[0])


def _decode_ddg_href(href: str) -> str | None:
    """DDG html results wrap targets: //duckduckgo.com/l/?uddg=<enc>&..."""
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        return unquote(target) if target else None
    if parsed.scheme in ("http", "https"):
        return href
    return None


def _parse_ddg(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for result in soup.select("div.result, div.web-result"):
        a = result.select_one("a.result__a")
        if not a:
            continue
        url = _decode_ddg_href(a.get("href", ""))
        if not url:
            continue
        snippet_el = result.select_one("a.result__snippet, div.result__snippet")
        out.append({
            "url": url,
            "title": a.get_text(" ", strip=True),
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
        })
    return out


def _decode_bing_href(href: str) -> str | None:
    """Bing wraps results in click-tracking: /ck/a?...&u=a1<base64url>.
    Unwrap to the real target URL."""
    if not href.startswith("http"):
        return None
    parsed = urlparse(href)
    if "bing.com" not in parsed.netloc or not parsed.path.startswith("/ck/"):
        return href
    packed = parse_qs(parsed.query).get("u", [None])[0]
    if not packed or not packed.startswith("a1"):
        return None
    import base64

    payload = packed[2:]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded if decoded.startswith("http") else None


def _parse_bing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for result in soup.select("li.b_algo"):
        a = result.select_one("h2 a")
        if not a:
            continue
        url = _decode_bing_href(a.get("href", ""))
        if not url:
            continue
        caption = result.select_one("div.b_caption p, p")
        out.append({
            "url": url,
            "title": a.get_text(" ", strip=True),
            "snippet": caption.get_text(" ", strip=True) if caption else "",
        })
    return out


def _search(fetcher: TieredFetcher, name: str) -> tuple[list[dict], str | None]:
    """Returns (results, engine_used).

    Search engines fingerprint Python's TLS handshake and serve empty
    deflection pages, so each engine gets two shots: plain requests
    (cheap, sometimes works) then the system curl binary (different TLS
    fingerprint - passes today's checks)."""
    query = quote_plus(f'"{name}" grain cash bids')
    attempts = (
        (f"https://html.duckduckgo.com/html/?q={query}", _parse_ddg, "duckduckgo"),
        (f"https://www.bing.com/search?q={query}", _parse_bing, "bing"),
    )
    fetches = [fetcher.fetch_tier1, fetcher.fetch_via_curl]
    if fetcher.playwright_available():
        # real-browser fingerprint beats search-engine bot walls
        fetches.append(lambda url: fetcher.fetch_tier2(url))
    for url, parse, engine in attempts:
        for fetch in fetches:
            result = fetch(url)
            if result.ok and result.html:
                parsed = parse(result.html)
                if parsed:
                    return parsed, engine
    return [], None


def _score_result(result: dict, name: str) -> tuple[float, list[str]]:
    url, title, snippet = result["url"], result["title"], result["snippet"]
    domain = _registered_domain(urlparse(url).netloc)
    score = 0.0
    evidence: list[str] = []

    if any(domain == b or domain.endswith("." + b) for b in BLOCKLIST_DOMAINS):
        return -5.0, ["directory or social site, not the company's own"]

    name_lower = name.lower()
    domain_similarity = fuzz.token_set_ratio(_domain_tokens(domain), name_lower)
    if domain_similarity >= 60:
        score += 3
        evidence.append("web address matches the name")
        if domain_similarity >= 85:
            score += 1

    if fuzz.partial_ratio(name_lower, title.lower()) >= 85:
        score += 2
        evidence.append("page title names the company")

    if BID_WORDS_RE.search(title) or BID_WORDS_RE.search(snippet):
        score += 2
        evidence.append("mentions cash bids")

    path = urlparse(url).path.lower()
    if any(t in path for t in ("cashbid", "cash-bid", "bids", "grain")):
        score += 2
        evidence.append("links straight to a bids page")

    return score, evidence


def resolve_business_name(
    name: str, fetcher: TieredFetcher, config: EngineConfig
) -> ResolveResult:
    name = name.strip()
    if not name:
        return ResolveResult(flags=[Flag(
            "RESOLVE_FAILED", "error",
            "No company name was given.",
            "Type the company's name, or paste their website address.",
        )])

    results, engine = _search(fetcher, name)
    if not results:
        return ResolveResult(flags=[Flag(
            "RESOLVE_FAILED", "error",
            f"We couldn't search the web for '{name}' right now.",
            "Find the company's website in your own browser and paste the "
            "address here instead.",
        )])

    # score, dedupe by domain (keep best), rank
    best_per_domain: dict[str, UrlCandidate] = {}
    for result in results[:10]:
        score, evidence = _score_result(result, name)
        domain = _registered_domain(urlparse(result["url"]).netloc)
        candidate = UrlCandidate(
            url=result["url"],
            domain=domain,
            title=result["title"][:160],
            snippet=result["snippet"][:240],
            score=score,
            evidence=tuple(evidence),
        )
        prior = best_per_domain.get(domain)
        if prior is None or candidate.score > prior.score:
            best_per_domain[domain] = candidate

    ranked = sorted(best_per_domain.values(), key=lambda c: c.score, reverse=True)
    ranked = [c for c in ranked if c.score > 0][:5]

    # verification pass: does the top candidate's site visibly have a
    # cash-bids link? (+3, and remember that link as the suggested page)
    if config.resolve_verify and ranked:
        verified: list[UrlCandidate] = []
        for candidate in ranked[:3]:
            homepage = f"https://{candidate.domain}"
            fetched = fetcher.fetch_tier1(homepage)
            if fetched.ok and fetched.html:
                links = discover_bid_links(fetched.html, fetched.final_url)
                if links:
                    verified.append(UrlCandidate(
                        url=candidate.url,
                        domain=candidate.domain,
                        title=candidate.title,
                        snippet=candidate.snippet,
                        score=candidate.score + 3,
                        evidence=candidate.evidence
                        + ("site has a cash bids section",),
                        suggested_bid_page=links[0].url,
                    ))
                    continue
            verified.append(candidate)
        verified.extend(ranked[3:])
        ranked = sorted(verified, key=lambda c: c.score, reverse=True)

    flags: list[Flag] = []
    if not ranked or ranked[0].score < 3:
        flags.append(Flag(
            "RESOLVE_AMBIGUOUS", "warn",
            f"We found some possible websites for '{name}', but none looked "
            "like a clear match.",
            "Pick one below if it's right, or paste the address yourself.",
        ))
    log.info("resolve '%s' via %s: %d candidates", name, engine, len(ranked))
    return ResolveResult(candidates=ranked, flags=flags)
