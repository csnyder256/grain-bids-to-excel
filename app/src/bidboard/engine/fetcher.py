"""Tiered fetch pipeline.

Tier 1: `requests` with realistic headers - covers server-rendered sites
(server-rendered pages). Tier 2: Playwright Chromium - covers Barchart/DTN
widget sites; imported lazily so the engine never hard-requires it.

Politeness: per-domain jittered delays, robots.txt honored (warn flag on
disallow), bounded retries with backoff.
"""
from __future__ import annotations

import logging
import random
import threading
import time
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from .config import DEFAULT_HEADERS, USER_AGENT, EngineConfig
from .types import FetchResult, Flag, VendorHints

log = logging.getLogger("bidboard.fetcher")

_RETRYABLE_STATUS = {500, 502, 503, 504}


class PolitenessGate:
    """Enforces a jittered minimum gap between requests to one domain."""

    def __init__(self, min_s: float, max_s: float):
        self.min_s = min_s
        self.max_s = max_s
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str) -> None:
        gap = random.uniform(self.min_s, self.max_s)
        with self._lock:
            last = self._last.get(domain)
            now = time.monotonic()
            sleep_for = 0.0 if last is None else max(0.0, (last + gap) - now)
            self._last[domain] = now + sleep_for
        if sleep_for > 0:
            time.sleep(sleep_for)


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


class TieredFetcher:
    def __init__(self, config: EngineConfig, store=None):
        self.config = config
        self.store = store  # optional: robots cache persistence
        self.gate = PolitenessGate(config.politeness_min_s, config.politeness_max_s)
        self._sessions: dict[str, requests.Session] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._pw = None  # lazy Playwright handle (M5 wires rendering)

    # ---------------- robots ----------------

    def check_robots(self, url: str) -> tuple[bool, Flag | None]:
        """(allowed, flag). Fail-open: unreachable robots.txt = allowed."""
        if not self.config.respect_robots:
            return True, None
        domain = _domain(url)
        parser = self._robots.get(domain)
        if parser is None:
            body = self._load_robots_body(domain, urlparse(url).scheme or "https")
            parser = urllib.robotparser.RobotFileParser()
            if body is None:
                parser.allow_all = True
            else:
                parser.parse(body.splitlines())
            self._robots[domain] = parser
        allowed = parser.can_fetch(USER_AGENT, url) and parser.can_fetch("*", url)
        if allowed:
            return True, None
        return False, Flag(
            code="ROBOTS_BLOCKED",
            severity="warn",
            message=(
                f"{domain} asks automated tools not to read this page, "
                "so we skipped it."
            ),
            suggestion=(
                "You can read it manually in your browser, or change the "
                "'respect site rules' setting (not recommended)."
            ),
            data={"url": url},
        )

    def _load_robots_body(self, domain: str, scheme: str) -> str | None:
        if self.store is not None:
            cached = self.store.get_robots_cache(domain)
            if cached is not None:
                age_ok = True
                try:
                    fetched = datetime.strptime(
                        cached["fetched_at"], "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                    age_h = (
                        datetime.now(timezone.utc) - fetched
                    ).total_seconds() / 3600
                    age_ok = age_h < self.config.robots_cache_hours
                except (ValueError, KeyError):
                    pass
                if age_ok:
                    return cached["body"]
        body: str | None = None
        try:
            self.gate.wait(domain)
            resp = requests.get(
                f"{scheme}://{domain}/robots.txt",
                headers=DEFAULT_HEADERS,
                timeout=(self.config.connect_timeout_s, 10),
            )
            if resp.status_code == 200:
                body = resp.text[:100_000]
        except requests.RequestException:
            body = None
        if self.store is not None:
            self.store.set_robots_cache(domain, body)
        return body

    # ---------------- tier 1 ----------------

    def _session(self, domain: str) -> requests.Session:
        s = self._sessions.get(domain)
        if s is None:
            s = requests.Session()
            s.headers.update(DEFAULT_HEADERS)
            self._sessions[domain] = s
        return s

    def fetch(
        self,
        url: str,
        *,
        start_tier: int = 1,
        hints: VendorHints | None = None,
        check_robots: bool = True,
    ) -> FetchResult:
        if check_robots:
            allowed, robots_flag = self.check_robots(url)
            if not allowed:
                return FetchResult(
                    ok=False, url=url, final_url=url, tier_used=start_tier,
                    http_status=None, html=None,
                    fetched_at=datetime.now(timezone.utc), duration_ms=0,
                    flags=[robots_flag],
                )
        if start_tier >= 2:
            return self.fetch_tier2(url, hints=hints)
        return self.fetch_tier1(url)

    def fetch_tier1(self, url: str) -> FetchResult:
        domain = _domain(url)
        started = time.monotonic()
        flags: list[Flag] = []
        status: int | None = None
        html: str | None = None
        final_url = url
        ok = False

        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            self.gate.wait(domain)
            try:
                resp = self._session(domain).get(
                    url,
                    timeout=(self.config.connect_timeout_s, self.config.read_timeout_s),
                    allow_redirects=True,
                )
                status = resp.status_code
                final_url = resp.url
                if status == 200:
                    resp.encoding = resp.apparent_encoding or resp.encoding
                    html = resp.text
                    ok = True
                    break
                if status == 429:
                    retry_after = min(
                        float(resp.headers.get("Retry-After", 30) or 30), 120.0
                    )
                    if attempt < attempts - 1:
                        time.sleep(retry_after)
                        continue
                    flags.append(self._flag_for_status(url, status))
                    break
                if status in _RETRYABLE_STATUS and attempt < attempts - 1:
                    time.sleep(
                        self.config.retry_backoff_s[
                            min(attempt, len(self.config.retry_backoff_s) - 1)
                        ]
                        + random.uniform(0, 2)
                    )
                    continue
                # keep the body for 403 bot-wall size heuristics downstream
                html = resp.text if resp.text else None
                flags.append(self._flag_for_status(url, status))
                break
            except requests.exceptions.ConnectTimeout:
                err_flag = Flag(
                    "TIMEOUT", "error",
                    f"{domain} took too long to respond.",
                    "The site may be slow or down. It will be retried on the next scan.",
                    {"url": url},
                )
            except requests.exceptions.ReadTimeout:
                err_flag = Flag(
                    "TIMEOUT", "error",
                    f"{domain} took too long to respond.",
                    "The site may be slow or down. It will be retried on the next scan.",
                    {"url": url},
                )
            except requests.exceptions.SSLError:
                err_flag = Flag(
                    "NETWORK_FAIL", "error",
                    f"We couldn't make a secure connection to {domain}.",
                    "Double-check the web address; if it looks right, try again later.",
                    {"url": url},
                )
            except requests.exceptions.ConnectionError as e:
                name_fail = "getaddrinfo" in str(e) or "NameResolution" in str(e)
                if name_fail:
                    err_flag = Flag(
                        "DNS_FAIL", "error",
                        f"We couldn't find the website {domain} - the address "
                        "may be wrong or the site may be gone.",
                        "Double-check the web address on the company's page.",
                        {"url": url},
                    )
                else:
                    err_flag = Flag(
                        "NETWORK_FAIL", "error",
                        f"We couldn't connect to {domain} after "
                        f"{attempts} tries.",
                        "This is often temporary - try again in a few minutes.",
                        {"url": url},
                    )
            except requests.RequestException as e:
                err_flag = Flag(
                    "NETWORK_FAIL", "error",
                    f"Something went wrong talking to {domain}.",
                    "Try again in a few minutes.",
                    {"url": url, "detail": str(e)[:200]},
                )
            if attempt < attempts - 1:
                time.sleep(
                    self.config.retry_backoff_s[
                        min(attempt, len(self.config.retry_backoff_s) - 1)
                    ]
                    + random.uniform(0, 2)
                )
                continue
            flags.append(err_flag)
            break

        return FetchResult(
            ok=ok,
            url=url,
            final_url=final_url,
            tier_used=1,
            http_status=status,
            html=html,
            fetched_at=datetime.now(timezone.utc),
            duration_ms=int((time.monotonic() - started) * 1000),
            flags=flags,
        )

    def fetch_via_curl(self, url: str) -> FetchResult:
        """Fallback fetch through the system curl binary (present on
        Windows 10 1803+). curl's TLS fingerprint passes bot checks that
        reject Python's - used for search engines, never for bid pages."""
        import shutil
        import subprocess

        domain = _domain(url)
        started = time.monotonic()
        curl = shutil.which("curl")
        if curl is None:
            return FetchResult(
                ok=False, url=url, final_url=url, tier_used=1,
                http_status=None, html=None,
                fetched_at=datetime.now(timezone.utc), duration_ms=0,
                flags=[Flag("NETWORK_FAIL", "error",
                            "A helper program (curl) is missing on this PC.",
                            "Paste the company's website address instead.")],
            )
        self.gate.wait(domain)
        try:
            proc = subprocess.run(
                [
                    curl, "-sL", "-m", "30",
                    "-A", USER_AGENT,
                    "-H", "Accept-Language: en-US,en;q=0.9",
                    "-w", "\n%{http_code}",
                    url,
                ],
                capture_output=True, timeout=40,
            )
            raw = proc.stdout.decode("utf-8", errors="replace")
            body, _, status_line = raw.rpartition("\n")
            status = int(status_line) if status_line.strip().isdigit() else None
            ok = proc.returncode == 0 and status == 200 and bool(body)
            return FetchResult(
                ok=ok, url=url, final_url=url, tier_used=1,
                http_status=status, html=body if ok else None,
                fetched_at=datetime.now(timezone.utc),
                duration_ms=int((time.monotonic() - started) * 1000),
                flags=[] if ok else [self._flag_for_status(url, status or 0)],
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return FetchResult(
                ok=False, url=url, final_url=url, tier_used=1,
                http_status=None, html=None,
                fetched_at=datetime.now(timezone.utc),
                duration_ms=int((time.monotonic() - started) * 1000),
                flags=[Flag("TIMEOUT", "error",
                            f"{domain} took too long to respond.",
                            "Try again in a moment.",
                            {"detail": str(e)[:120]})],
            )

    @staticmethod
    def _flag_for_status(url: str, status: int) -> Flag:
        domain = _domain(url)
        if status in (404, 410):
            return Flag(
                "HTTP_404", "error",
                f"The bid page at {url} no longer exists.",
                "The company may have moved it. Use 'Find bid pages' to look "
                "for a replacement on their site.",
                {"url": url, "status": status},
            )
        if status in (401, 403, 406):
            return Flag(
                "HTTP_403_BOTBLOCK", "error",
                f"{domain} is blocking automated readers.",
                "Open the page in your own browser to confirm it works, then "
                "try again later. Some sites block repeat visitors for a while.",
                {"url": url, "status": status},
            )
        if status >= 500:
            return Flag(
                "HTTP_5XX", "warn",
                f"{domain} reported a server problem (their side, not yours).",
                "It will be retried on the next scan.",
                {"url": url, "status": status},
            )
        if status == 429:
            return Flag(
                "HTTP_429", "warn",
                f"{domain} asked us to slow down.",
                "It will be retried on the next scan with longer pauses.",
                {"url": url, "status": status},
            )
        return Flag(
            "NETWORK_FAIL", "error",
            f"{domain} answered in an unexpected way (code {status}).",
            "Try again later; if it persists, check the address.",
            {"url": url, "status": status},
        )

    # ---------------- tier 2 ----------------

    def playwright_available(self) -> bool:
        try:
            import importlib

            importlib.import_module("playwright.sync_api")
            return True
        except ImportError:
            return False

    def fetch_tier2(
        self, url: str, hints: VendorHints | None = None
    ) -> FetchResult:
        """Rendered fetch via headless Chromium. Lazy import: a missing
        Playwright degrades to a PLAYWRIGHT_MISSING flag, never a crash."""
        domain = _domain(url)
        started = time.monotonic()
        if not self.playwright_available():
            return FetchResult(
                ok=False, url=url, final_url=url, tier_used=2,
                http_status=None, html=None,
                fetched_at=datetime.now(timezone.utc),
                duration_ms=0,
                flags=[Flag(
                    "PLAYWRIGHT_MISSING", "error",
                    "This site needs the built-in mini-browser to be read.",
                    "Go to Settings and click 'Install browser engine' "
                    "(one-time download).",
                    {"url": url},
                )],
            )
        self.gate.wait(domain)
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        user_agent=USER_AGENT,
                        viewport={"width": 1366, "height": 900},
                    )
                    page = context.new_page()
                    resp = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.config.render_timeout_s * 1000,
                    )
                    waited = False
                    for selector in (hints.wait_selectors if hints else ()):
                        try:
                            page.wait_for_selector(selector, timeout=15_000)
                            waited = True
                            break
                        except Exception:
                            continue
                    if not waited:
                        try:
                            page.wait_for_load_state("networkidle", timeout=10_000)
                        except Exception:
                            pass
                    page.wait_for_timeout(2_000)  # settle
                    html = page.content()
                    status = resp.status if resp else None
                    final_url = page.url
                finally:
                    browser.close()
            return FetchResult(
                ok=bool(html),
                url=url,
                final_url=final_url,
                tier_used=2,
                http_status=status,
                html=html,
                fetched_at=datetime.now(timezone.utc),
                duration_ms=int((time.monotonic() - started) * 1000),
                flags=[],
            )
        except Exception as e:
            log.warning("Tier-2 render failed for %s: %s", url, e)
            return FetchResult(
                ok=False, url=url, final_url=url, tier_used=2,
                http_status=None, html=None,
                fetched_at=datetime.now(timezone.utc),
                duration_ms=int((time.monotonic() - started) * 1000),
                flags=[Flag(
                    "RENDER_TIMEOUT", "warn",
                    f"The bid widget on {url} didn't finish loading in time.",
                    "It will be retried next scan; if it persists, the site's "
                    "widget may have changed.",
                    {"url": url, "detail": str(e)[:200]},
                )],
            )
