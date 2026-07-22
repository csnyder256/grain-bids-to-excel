"""Read-side shaping for the report builder: apply the effective (resolved)
settings' filters, sorting, grouping, and delivery horizon to stored bids."""
from __future__ import annotations

from datetime import date, timedelta


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def fetch_bid_rows(
    store, run_id: int, eff: dict, company_id: int | None = None
) -> list[dict]:
    """Rows for one company (or all), filtered/sorted per effective settings."""
    company_ids = [company_id] if company_id is not None else None
    commodities = eff.get("commodities") or None
    rows = store.query_bids(
        run_id=run_id, company_ids=company_ids,
        commodities=commodities, latest_only=True,
    )

    # location filter
    locations = set(eff.get("locations") or [])
    if locations:
        rows = [r for r in rows if (r.get("site_label") or "") in locations]

    # delivery horizon
    horizon = eff.get("horizon_months", 0)
    if horizon:
        cutoff = date.today() + timedelta(days=int(horizon) * 31)
        kept = []
        for r in rows:
            ds = _parse_date(r.get("delivery_start"))
            if ds is None or ds <= cutoff:
                kept.append(r)
        rows = kept

    return sort_rows(rows, eff)


def sort_rows(rows: list[dict], eff: dict) -> list[dict]:
    group_by = eff.get("group_by", "commodity")
    sort_by = eff.get("sort_by", "delivery_start")

    def group_key(r):
        if group_by == "commodity":
            return (r.get("commodity") or "zzz",)
        if group_by == "location":
            return (r.get("site_label") or "zzz",)
        if group_by == "company":
            return (r.get("company_name") or "zzz",)
        return ("",)

    def sort_key(r):
        if sort_by == "cash_desc":
            return (-(r.get("cash_price") or -999),)
        if sort_by == "commodity":
            return (r.get("commodity") or "zzz",)
        if sort_by == "location":
            return (r.get("site_label") or "zzz",)
        ds = _parse_date(r.get("delivery_start"))
        return (ds or date(2100, 1, 1),)

    return sorted(rows, key=lambda r: group_key(r) + sort_key(r))


def distinct_companies_in_run(store, run_id: int) -> list[dict]:
    rows = store.query_bids(run_id=run_id, latest_only=True)
    seen = {}
    for r in rows:
        seen.setdefault(r["company_id"], r["company_name"])
    return [{"id": cid, "name": name} for cid, name in seen.items()]


def distinct_commodities_in_run(store, run_id: int) -> list[str]:
    rows = store.query_bids(run_id=run_id, latest_only=True)
    return sorted({r["commodity"] for r in rows if r["commodity"]})
