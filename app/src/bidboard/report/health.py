"""Data-health rules (freshness, repeats, overlap) and best-bid highlights
for the Summary and Data Health sheets. Plain-English messages only."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


@dataclass
class PageHealth:
    company: str
    location: str
    url: str
    as_of: date | None
    age_days: int | None
    status: str                    # FRESH | AGING | STALE | FAILED
    rows: int
    same_as_last: str              # "Yes ×3" | "No" | ""
    notices: str
    suggestion: str


@dataclass
class BestBid:
    commodity: str
    window: str                    # "Nearby" | "Deferred"
    cash_price: float
    basis: float | None
    delivery_label: str
    company: str
    location: str


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


FLAG_MESSAGES = {
    "LOW_CONFIDENCE_EXTRACTION": "We weren't fully sure we read this table right.",
    "PARTIAL_COLUMNS": "We couldn't identify the price column with confidence.",
    "LAYOUT_CHANGED": "This page's layout looks different from before.",
    "AMBIGUOUS_COLUMNS": "Two columns looked alike; we left them unlabeled.",
    "ARITHMETIC_MISMATCH": "The prices didn't add up the usual way.",
}


def compute_health(store, run_id: int, thresholds: dict, now: datetime) -> list[PageHealth]:
    aging = thresholds.get("aging_days", 2)
    stale = thresholds.get("stale_days", 5)
    today = now.date()

    out: list[PageHealth] = []
    # every page scanned in this run
    companies = store.list_companies(include_archived=True)
    by_id = {c["id"]: c["name"] for c in companies}

    run_flags = store.open_flags(run_id=run_id)
    flags_by_site: dict[int, list[dict]] = {}
    for f in run_flags:
        if f["site_id"]:
            flags_by_site.setdefault(f["site_id"], []).append(f)

    # find page_scans for this run via bids' sites, plus failed pages via flags
    seen_sites: set[int] = set()
    rows = store.query_bids(run_id=run_id, latest_only=False)
    site_meta: dict[int, dict] = {}
    for r in rows:
        sid = r["site_id"]
        site_meta.setdefault(sid, {
            "company": r["company_name"], "location": r.get("site_label") or "",
            "url": r.get("site_url") or "", "as_of": r.get("page_as_of"),
            "rows": 0,
        })
        site_meta[sid]["rows"] += 1

    for sid, meta in site_meta.items():
        seen_sites.add(sid)
        as_of = _parse_date(meta["as_of"])
        age = (today - as_of).days if as_of else None
        if age is None:
            status = "AGING"
        elif age >= stale:
            status = "STALE"
        elif age > aging:
            status = "AGING"
        else:
            status = "FRESH"

        # repeat streak
        sigs = store.recent_signatures(sid, limit=5)
        streak = 0
        if sigs:
            first = sigs[0]["content_signature"]
            for s in sigs:
                if s["content_signature"] == first:
                    streak += 1
                else:
                    break
        same = f"Yes ×{streak}" if streak >= 2 else "No"

        notices = []
        suggestion = ""
        if status == "STALE":
            notices.append(f"Bids haven't changed since {as_of:%b %d} - the elevator may not be updating this page." if as_of else "This page looks out of date.")
            suggestion = "Call the elevator, or re-point this page to a newer one."
        elif status == "AGING":
            notices.append(f"Bids are {age} days old - could just be a weekend or holiday." if age else "")
        for f in flags_by_site.get(sid, []):
            msg = FLAG_MESSAGES.get(f["code"])
            if msg:
                notices.append(msg)
                if not suggestion:
                    suggestion = "Open the source page and compare one row."

        out.append(PageHealth(
            company=meta["company"], location=meta["location"], url=meta["url"],
            as_of=as_of, age_days=age, status=status, rows=meta["rows"],
            same_as_last=same,
            notices=" ".join(n for n in notices if n) or "Up to date.",
            suggestion=suggestion,
        ))

    # failed pages (flags but no rows)
    for sid, flist in flags_by_site.items():
        if sid in seen_sites:
            continue
        site = store.get_site(sid)
        if not site:
            continue
        f = flist[0]
        out.append(PageHealth(
            company=by_id.get(site["company_id"], ""),
            location=site.get("label") or "",
            url=site["url"], as_of=None, age_days=None, status="FAILED",
            rows=0, same_as_last="",
            notices=f["message"],
            suggestion=f["suggestion"],
        ))

    out.sort(key=lambda h: (h.company, h.location))
    return out


def best_bids(rows: list[dict], now: datetime, nearby_days: int = 60) -> list[BestBid]:
    """Max cash bid per commodity × {Nearby, Deferred} across all rows."""
    cutoff = now.date().toordinal() + nearby_days
    buckets: dict[tuple, dict] = {}
    for r in rows:
        if r.get("cash_price") is None or not r.get("commodity"):
            continue
        ds = _parse_date(r.get("delivery_start"))
        window = "Nearby" if (ds and ds.toordinal() <= cutoff) else "Deferred"
        key = (r["commodity"], window)
        cur = buckets.get(key)
        if cur is None or r["cash_price"] > cur["cash_price"]:
            buckets[key] = r | {"_window": window}

    out = []
    for (commodity, window), r in sorted(buckets.items()):
        out.append(BestBid(
            commodity=commodity, window=window, cash_price=r["cash_price"],
            basis=r.get("basis"), delivery_label=r.get("delivery_label") or "",
            company=r.get("company_name") or "", location=r.get("site_label") or "",
        ))
    return out


def detect_overlaps(rows: list[dict]) -> list[dict]:
    """Same (company, location, commodity, delivery) from two source URLs."""
    seen: dict[tuple, dict] = {}
    overlaps = []
    for r in rows:
        key = (r["company_id"], r.get("site_label"), r.get("commodity"),
               r.get("delivery_start"), r.get("delivery_end"))
        if key in seen and seen[key].get("site_url") != r.get("site_url"):
            overlaps.append({"a": seen[key], "b": r})
        else:
            seen[key] = r
    return overlaps
