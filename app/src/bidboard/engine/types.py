"""Shared engine dataclasses. Frozen where identity matters.

Two principles live here:
  * Raw + normalized, always - a parse failure degrades to raw-only, never
    drops data.
  * The engine never raises to the UI - everything the user must know
    becomes a Flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Flag:
    code: str                 # taxonomy code, e.g. "STALE_REPEAT"
    severity: str             # "info" | "warn" | "error"
    message: str              # human-readable, fully rendered
    suggestion: str = ""      # human-readable next step
    data: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    ok: bool
    url: str
    final_url: str            # after redirects
    tier_used: int            # 1 or 2
    http_status: int | None
    html: str | None
    fetched_at: datetime
    duration_ms: int
    flags: list[Flag] = field(default_factory=list)


@dataclass
class BidRow:
    """One normalized bid line. *_raw fields hold verbatim page text."""
    commodity_raw: str = ""
    commodity: str | None = None          # canonical, e.g. "CORN"
    location_label: str | None = None
    delivery_raw: str = ""
    delivery_start: date | None = None
    delivery_end: date | None = None
    delivery_label: str = ""
    cash_price_raw: str = ""
    cash_price: float | None = None       # always $/bu
    basis_raw: str = ""
    basis: float | None = None            # always $/bu, signed
    futures_raw: str = ""
    futures_price: float | None = None    # always $/bu
    futures_month_raw: str = ""
    futures_month: str | None = None      # "2026-09"
    change_raw: str = ""
    change: float | None = None
    last_update_raw: str = ""
    last_update: date | None = None
    row_hash: str = ""
    value_hash: str = ""
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    rows: list[BidRow] = field(default_factory=list)
    as_of_text: str | None = None
    as_of_date: date | None = None
    container_score: float = 0.0
    confidence: float = 0.0
    column_roles: dict[int, str] = field(default_factory=dict)
    flags: list[Flag] = field(default_factory=list)


@dataclass(frozen=True)
class ScoredLink:
    url: str
    anchor_text: str
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class UrlCandidate:
    url: str
    domain: str
    title: str
    snippet: str
    score: float
    evidence: tuple[str, ...] = ()
    suggested_bid_page: str | None = None


@dataclass
class ResolveResult:
    candidates: list[UrlCandidate] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)


@dataclass(frozen=True)
class VendorHints:
    vendor: str = "unknown"   # barchart | dtn | bushel_ssr | cloudflare | unknown
    needs_js: bool = False
    wait_selectors: tuple[str, ...] = ()
    container_selectors: tuple[str, ...] = ()
    iframe_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProgressEvent:
    """What the engine tells the UI while a scan runs."""
    company_id: int | None
    site_id: int | None
    stage: str                # fetch | extract | flag | done | ...
    message: str
    level: str = "info"       # info | warn | error
    data: dict = field(default_factory=dict)
