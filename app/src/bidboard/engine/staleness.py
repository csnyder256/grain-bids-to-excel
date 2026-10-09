"""Row hashing, staleness detection, and cross-page overlap.

Hashes let us (a) supersede a bid's old value with its new one instead of
duplicating, (b) detect a page that never changes, and (c) spot the same
bids appearing from two different pages.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from .types import BidRow, ExtractionResult, Flag


def _h(*parts) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def _delivery_identity(start, end, raw, label) -> str:
    """The delivery component of a bid's identity.

    Normalized dates when they parsed; otherwise the raw label, lowercased, so
    two unparsed bids ("Spot" vs "Nearby") never share an identity."""
    if start or end:
        return f"{start}|{end}"
    return (raw or label or "").strip().lower()


def business_identity(row: BidRow) -> tuple:
    """Site-independent bid identity.

    Everything :func:`row_identity_hash` uses *except* the site id. Two
    locations of one company that post the same bids share this key - which is
    exactly what overlap detection needs, and what ``row_identity_hash`` cannot
    provide because it bakes the site id into the hash.
    """
    return (
        row.location_label,
        row.commodity or row.commodity_raw,
        _delivery_identity(row.delivery_start, row.delivery_end,
                           row.delivery_raw, row.delivery_label),
        row.futures_month,
    )


def _stored_business_identity(record: dict) -> tuple:
    """Same key as :func:`business_identity`, built from a stored bid row.

    Stored dates are ISO strings (``_iso``), which render identically to the
    ``date`` objects :func:`_delivery_identity` sees on a live row, so the two
    paths agree.
    """
    return (
        record.get("location_label"),
        record.get("commodity") or record.get("commodity_raw"),
        _delivery_identity(record.get("delivery_start"), record.get("delivery_end"),
                           record.get("delivery_raw"), record.get("delivery_label")),
        record.get("futures_month"),
    )


def row_identity_hash(site_id: int, row: BidRow) -> str:
    # Site-scoped identity: a bid belongs to a site, so the site id is part of
    # the hash (two locations may post the same commodity and delivery and must
    # still supersede independently).
    return _h(site_id, *business_identity(row))


def value_hash(row: BidRow) -> str:
    def q(v):
        return f"{v:.4f}" if isinstance(v, (int, float)) else (v or "")
    return _h(q(row.cash_price), q(row.basis), q(row.futures_price), q(row.change))


def content_signature(rows: list[BidRow]) -> str:
    pairs = sorted(f"{r.row_hash}:{r.value_hash}" for r in rows)
    return _h(*pairs)


def stamp_hashes(site_id: int, rows: list[BidRow]) -> None:
    """Assign identity/value hashes. If two extracted rows on one page are
    still indistinguishable (same commodity, unparsed delivery, no futures
    month), disambiguate by order of appearance so neither is silently
    dropped by latest-only queries - supersede across scans then relies on
    the page keeping its row order, which is the best identity available."""
    seen: dict[str, int] = {}
    for r in rows:
        base = row_identity_hash(site_id, r)
        n = seen.get(base, 0)
        seen[base] = n + 1
        if n:
            r.notes.append("duplicate_identity_disambiguated")
            base = _h(base, n)
        r.row_hash = base
        r.value_hash = value_hash(r)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def check_staleness(
    store, site, extraction: ExtractionResult, thresholds: dict,
    signature: str, today: date | None = None,
) -> list[Flag]:
    """Emit STALE_* flags. `site` is a site dict; `store` gives history."""
    today = today or date.today()
    flags: list[Flag] = []
    label = site.get("label") or site["url"]
    aging = thresholds.get("aging_days", 2)
    stale = thresholds.get("stale_days", 5)
    identical_n = thresholds.get("identical_scans", 3)

    # STALE_ASOF
    if extraction.as_of_date:
        age = (today - extraction.as_of_date).days
        if age >= stale:
            pretty = extraction.as_of_date.strftime("%b %d").replace(" 0", " ")
            flags.append(Flag(
                "STALE_ASOF", "warn",
                f"{label} says its bids are from {pretty} - {age} days ago.",
                "The elevator may not have updated. If this persists, look "
                "for a newer page via 'Find bid pages'.",
                {"site_id": site["id"], "as_of": str(extraction.as_of_date),
                 "days": age},
            ))

    # STALE_REPEAT - identical signature across the last N scans over >=48h
    prior = store.recent_signatures(site["id"], limit=identical_n)
    matching = [p for p in prior if p["content_signature"] == signature]
    if len(matching) >= identical_n - 1 and matching:
        oldest = _parse_ts(matching[-1]["fetched_at"])
        span_ok = True
        if oldest:
            span_ok = (datetime.now(timezone.utc) - oldest) >= timedelta(hours=48)
        if span_ok:
            hint = ""
            candidates = store.candidate_links(site["id"], min_score=5.0)
            if candidates:
                hint = f"A sibling page {candidates[0]['url']} may be fresher."
            flags.append(Flag(
                "STALE_REPEAT", "warn",
                f"{label} has shown exactly the same numbers for the last "
                f"{len(matching) + 1} scans.",
                hint or "Markets usually move daily - this page may be frozen.",
                {"site_id": site["id"]},
            ))

    # STALE_DELIVERY - every delivery window already in the past
    if extraction.rows:
        ends = [r.delivery_end for r in extraction.rows if r.delivery_end]
        if ends and max(ends) < today:
            flags.append(Flag(
                "STALE_DELIVERY", "warn",
                f"Every delivery window on {label} is already in the past.",
                "This page is probably outdated - check the suggested sibling "
                "pages for a current one.",
                {"site_id": site["id"]},
            ))
    return flags


def check_overlap(store, company_id: int, site, rows: list[BidRow]) -> list[Flag]:
    """DUPLICATE_PAGE_OVERLAP when two sites of one company share >=80% of
    their bid identities.

    The comparison uses the *site-independent* identity (see
    :func:`business_identity`). ``row_hash`` cannot be used here: it includes
    the site id, so two locations of one company that post the very same bids
    hash differently and the overlap was never detected. Reading the other
    site's latest bids back as stored columns and rebuilding the same key
    fixes that without changing what a single site's rows hash to.
    """
    flags: list[Flag] = []
    my_keys = {business_identity(r) for r in rows}
    if not my_keys:
        return flags
    for other in store.list_sites(company_id=company_id, enabled_only=False):
        if other["id"] == site["id"]:
            continue
        other_keys = {
            _stored_business_identity(r)
            for r in store.latest_bid_rows(other["id"])
        }
        if not other_keys:
            continue
        overlap = len(my_keys & other_keys) / len(my_keys)
        if overlap >= 0.8:
            flags.append(Flag(
                "DUPLICATE_PAGE_OVERLAP", "warn",
                f"'{site.get('label') or site['url']}' and "
                f"'{other.get('label') or other['url']}' show the same bids "
                " - they may be the same page twice.",
                "You can remove one of them to avoid duplicate rows in your "
                "workbook.",
                {"site_id": site["id"], "other_site_id": other["id"]},
            ))
            break
    return flags
