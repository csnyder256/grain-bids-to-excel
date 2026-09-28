"""Scan orchestration: tie fetcher → detect → extractor → staleness →
store together. One bad site never aborts a run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from . import staleness as S
from .config import EngineConfig
from .detect import detect_vendor
from .discover import discover_bid_links
from .extractor import ACCEPT_SCORE, LOW_SCORE, extract_bids
from .fetcher import TieredFetcher
from .types import Flag, ProgressEvent

log = logging.getLogger("bidboard.pipeline")

Progress = Callable[[ProgressEvent], None]


@dataclass
class PageScanOutcome:
    site_id: int
    outcome: str            # ok | no_data | fetch_failed | robots_blocked | low_confidence
    rows: int
    flags: list[Flag]


@dataclass
class ScanRunSummary:
    run_id: int
    status: str             # complete | partial | failed
    pages: int
    total_rows: int
    flags: int


def _emit(progress: Progress | None, event: ProgressEvent) -> None:
    if progress:
        try:
            progress(event)
        except Exception:
            log.exception("progress callback failed")


def _persist_flags(store, flags, *, run_id, site_id, company_id,
                   progress=None) -> list[Flag]:
    """The single place a page's flags are written.

    Takes the finished list of every flag this page produced, writes each one
    to the store exactly once, emits the loud ones to progress, and returns
    that same list. Persistence and the returned ``PageScanOutcome.flags`` are
    therefore the same objects in the same order; the run summary can never
    disagree with what was stored.

    ``flags`` must be a snapshot (a fresh list), never a live list that some
    caller is still iterating or extending.
    """
    for f in flags:
        store.add_flag(f, run_id=run_id, site_id=site_id, company_id=company_id)
        if f.severity in ("warn", "error"):
            _emit(progress, ProgressEvent(company_id, site_id, "flag",
                                          f.message, f.severity))
    return list(flags)


def _needs_escalation(html: str, vendor, extraction) -> bool:
    """Decide whether Tier-1 output warrants a Tier-2 render."""
    if vendor.needs_js and not extraction.rows:
        return True
    low = html.lower()
    if any(m in low for m in ("just a moment", "cf-chl", "challenge-platform")):
        return True
    if extraction.container_score < ACCEPT_SCORE and not extraction.rows:
        visible = len(html)
        if "<noscript" in low or html.count("<script") > 15 and visible < 60_000:
            return True
    return False


def scan_site(
    store,
    fetcher: TieredFetcher,
    site: dict,
    run_id: int,
    config: EngineConfig,
    thresholds: dict,
    progress: Progress | None = None,
) -> PageScanOutcome:
    site_id = site["id"]
    company_id = site["company_id"]
    label = site.get("label") or site["url"]
    _emit(progress, ProgressEvent(company_id, site_id, "fetch",
                                  f"Reading {label}…"))

    start_tier = site.get("preferred_tier", 1)
    fetched = fetcher.fetch(site["url"], start_tier=start_tier)

    # robots / hard fetch failure
    if not fetched.ok and fetched.flags:
        code = fetched.flags[0].code
        outcome = "robots_blocked" if code == "ROBOTS_BLOCKED" else "fetch_failed"
        for f in fetched.flags:
            store.add_flag(f, run_id=run_id, site_id=site_id, company_id=company_id)
        store.record_page_scan(run_id, site_id, fetched, None, outcome)
        _emit(progress, ProgressEvent(company_id, site_id, "flag",
                                      fetched.flags[0].message, "error"))
        return PageScanOutcome(site_id, outcome, 0, fetched.flags)

    vendor = detect_vendor(fetched.html or "", site["url"])
    _emit(progress, ProgressEvent(company_id, site_id, "extract",
                                  f"Reading the bid table for {label}…"))
    extraction = extract_bids(
        fetched.html or "", fetched.final_url, vendor, location=site.get("label")
    )

    # escalate to Tier-2 if needed and we started at Tier-1
    used = fetched
    # Flags the Tier-2 attempt produced, kept separate from the extraction.
    # They are folded into the page's single flag list below; they are never
    # written into ``extraction.flags``, because that list is a live object the
    # no-rows branch iterates (extending it there duplicates accounting).
    tier2_flags: list[Flag] = []
    render_failed = False
    if fetched.tier_used == 1 and _needs_escalation(fetched.html or "", vendor, extraction):
        _emit(progress, ProgressEvent(company_id, site_id, "fetch",
                                      f"{label} needs the mini-browser - rendering…"))
        rendered = fetcher.fetch_tier2(site["url"], hints=vendor)
        # Whatever the render produced, its flags describe this page's result
        # and must reach both the store and the caller.
        tier2_flags = list(rendered.flags)
        if rendered.ok and rendered.html:
            # A successful render replaces the Tier-1 reading outright.
            used = rendered
            extraction = extract_bids(rendered.html, rendered.final_url, vendor,
                                      location=site.get("label"))
        else:
            render_failed = True

    # A failed render means Tier-1's reading of an un-rendered shell is
    # superseded, not corroborating: its "no bid table here" conclusion was
    # drawn from content the browser was supposed to supply. Reporting both it
    # and the render failure would charge one page outcome twice, so the stale
    # Tier-1 verdict is dropped and the render's own flags stand alone.
    extraction_flags = [
        f for f in extraction.flags
        if not (render_failed and f.code == "NO_BID_DATA")
    ]

    # persist vendor + tier memory on success
    if extraction.rows:
        S.stamp_hashes(site_id, extraction.rows)
        signature = S.content_signature(extraction.rows)
        store.update_site_memory(
            site_id, tier=used.tier_used, vendor=vendor.vendor,
            column_roles={str(k): v for k, v in extraction.column_roles.items()},
            tier_success=True,
        )
        outcome = "ok" if extraction.container_score >= ACCEPT_SCORE else "low_confidence"
        page_scan_id = store.record_page_scan(
            run_id, site_id, used, extraction, outcome, content_signature=signature
        )
        store.save_bids(page_scan_id, site_id, company_id, extraction.rows)

        # staleness + overlap
        stale_flags = S.check_staleness(store, site, extraction, thresholds, signature)
        overlap_flags = S.check_overlap(store, company_id, site, extraction.rows)
        # One snapshot of every flag this page produced, persisted and returned
        # by the same call so the run summary always matches the store.
        reported = _persist_flags(
            store, extraction_flags + stale_flags + overlap_flags + tier2_flags,
            run_id=run_id, site_id=site_id, company_id=company_id,
            progress=progress,
        )

        # discovery: remember sibling bid pages for later suggestion
        try:
            known = {s["url"] for s in store.list_sites(company_id=company_id, enabled_only=False)}
            links = discover_bid_links(used.html or "", used.final_url, known)
            if links:
                store.upsert_discovered_links(site_id, links)
        except Exception:
            log.exception("discovery failed for %s", site["url"])

        _emit(progress, ProgressEvent(
            company_id, site_id, "done",
            f"{label}: {len(extraction.rows)} bids", "info",
            {"rows": len(extraction.rows)}))
        return PageScanOutcome(site_id, outcome, len(extraction.rows), reported)

    # no rows: a failed render keeps whatever Tier-1 extracted, plus the
    # Tier-2 flags. Snapshot with ``+`` (a fresh list) so persistence cannot
    # mutate the list it is iterating.
    outcome = "no_data"
    reported = _persist_flags(
        store, extraction_flags + tier2_flags,
        run_id=run_id, site_id=site_id, company_id=company_id,
        progress=progress,
    )
    store.record_page_scan(run_id, site_id, used, extraction, outcome)
    msg = f"No bid rows extracted; {len(reported)} validation flag(s) recorded."
    _emit(progress, ProgressEvent(company_id, site_id, "done", msg, "warn"))
    return PageScanOutcome(site_id, outcome, 0, reported)


def scan_all(
    store,
    config: EngineConfig,
    thresholds: dict,
    *,
    company_ids: list[int] | None = None,
    trigger: str = "manual",
    progress: Progress | None = None,
    cancel=None,
    fetcher=None,
) -> ScanRunSummary:
    run_id = store.begin_run(trigger)
    # Injectable so the orchestration can be exercised without network or a
    # browser; production always builds the tiered fetcher.
    fetcher = fetcher or TieredFetcher(config, store)

    companies = company_ids or [c["id"] for c in store.list_companies()]
    sites: list[dict] = []
    for cid in companies:
        sites.extend(store.list_sites(company_id=cid, enabled_only=True))

    total_rows = 0
    flag_count = 0
    had_error = False
    completed = 0

    for site in sites:
        if cancel is not None and cancel.is_set():
            store.finish_run(run_id, "partial")
            return ScanRunSummary(run_id, "partial", completed, total_rows, flag_count)
        try:
            outcome = scan_site(store, fetcher, site, run_id, config, thresholds, progress)
            total_rows += outcome.rows
            flag_count += len(outcome.flags)
            if outcome.outcome in ("fetch_failed", "no_data"):
                had_error = True
        except Exception as e:
            log.exception("scan_site crashed for %s", site["url"])
            had_error = True
            store.add_flag(Flag(
                "SCAN_INTERNAL_ERROR", "error",
                f"Something unexpected went wrong while scanning "
                f"{site.get('label') or site['url']}. Your other sites were "
                "not affected.",
                "Try scanning again. If it repeats, use 'Copy error details' "
                "when reporting the problem.",
                {"site_id": site["id"], "detail": str(e)[:300]},
            ), run_id=run_id, site_id=site["id"], company_id=site["company_id"])
        completed += 1

    status = "partial" if had_error else "complete"
    store.finish_run(run_id, status)
    return ScanRunSummary(run_id, status, completed, total_rows, flag_count)
