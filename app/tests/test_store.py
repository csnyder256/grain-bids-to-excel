"""Store round-trip tests against an in-memory database."""
from datetime import date, datetime, timezone

import pytest

from bidboard.engine.store import Store
from bidboard.engine.types import BidRow, ExtractionResult, FetchResult, Flag, ScoredLink


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def _fetch(url="https://example.com/bids", tier=1, status=200):
    return FetchResult(
        ok=True,
        url=url,
        final_url=url,
        tier_used=tier,
        http_status=status,
        html="<html></html>",
        fetched_at=datetime.now(timezone.utc),
        duration_ms=123,
    )


def _row(commodity="CORN", start="2026-07-01", cash=4.10, rh="rh1", vh="vh1"):
    return BidRow(
        commodity_raw="Corn",
        commodity=commodity,
        location_label="Westfield",
        delivery_raw="July 1 - 15",
        delivery_start=date.fromisoformat(start),
        delivery_end=date.fromisoformat(start),
        delivery_label="July 1 - 15",
        cash_price_raw=f"{cash:.4f}",
        cash_price=cash,
        basis_raw="-0.1300",
        basis=-0.13,
        futures_raw="423-0s",
        futures_price=4.23,
        futures_month_raw="Sep 26 Corn",
        futures_month="2026-09",
        change_raw="+0-2",
        change=0.0025,
        last_update_raw="07/02/26",
        last_update=date(2026, 7, 2),
        row_hash=rh,
        value_hash=vh,
        confidence=0.95,
        notes=["year_inferred"],
    )


def test_schema_migrates_and_meta_roundtrip(store):
    assert store.get_meta("schema_version") == "1"
    store.set_meta("instance_uuid", "abc-123")
    assert store.get_meta("instance_uuid") == "abc-123"
    store.set_meta("instance_uuid", "def-456")
    assert store.get_meta("instance_uuid") == "def-456"


def test_company_site_crud(store):
    cid = store.add_company("Prairie Ridge Grain", "https://www.prairieridgegrain.example")
    sid = store.add_site(cid, "https://www.prairieridgegrain.example/cashbidssingle-2163", "Westfield")

    companies = store.list_companies()
    assert len(companies) == 1 and companies[0]["name"] == "Prairie Ridge Grain"

    sites = store.list_sites(company_id=cid)
    assert len(sites) == 1 and sites[0]["label"] == "Westfield"

    # duplicate URL upserts, not duplicates
    sid2 = store.add_site(cid, "https://www.prairieridgegrain.example/cashbidssingle-2163")
    assert sid2 == sid
    assert len(store.list_sites(company_id=cid)) == 1

    # archive hides the company and disables sites
    store.remove_company(cid)
    assert store.list_companies() == []
    assert store.list_companies(include_archived=True)[0]["archived"] == 1
    assert store.list_sites(company_id=cid, enabled_only=True) == []


def test_repoint_resets_site_memory(store):
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids")
    store.update_site_memory(sid, tier=2, vendor="barchart", column_roles={0: "cash_price"})
    assert store.get_site(sid)["preferred_tier"] == 2

    store.repoint_site(sid, "https://a.example/bids-new")
    site = store.get_site(sid)
    assert site["url"] == "https://a.example/bids-new"
    assert site["preferred_tier"] == 1
    assert site["vendor"] == "unknown"
    assert site["column_roles_json"] is None


def test_scan_run_bids_roundtrip_and_latest_only(store):
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids")

    # scan 1: cash 4.10
    run1 = store.begin_run()
    ex1 = ExtractionResult(rows=[_row(cash=4.10, vh="v1")], as_of_date=date(2026, 7, 2), confidence=0.9)
    ps1 = store.record_page_scan(run1, sid, _fetch(), ex1, "ok", content_signature="sig1")
    store.save_bids(ps1, sid, cid, ex1.rows)
    store.finish_run(run1, "complete")

    # scan 2: same bid identity, new value 4.15
    run2 = store.begin_run()
    ex2 = ExtractionResult(rows=[_row(cash=4.15, vh="v2")], as_of_date=date(2026, 7, 3), confidence=0.9)
    ps2 = store.record_page_scan(run2, sid, _fetch(), ex2, "ok", content_signature="sig2")
    store.save_bids(ps2, sid, cid, ex2.rows)
    store.finish_run(run2, "complete")

    latest = store.query_bids(latest_only=True)
    assert len(latest) == 1
    assert latest[0]["cash_price"] == 4.15
    assert latest[0]["company_name"] == "Co"

    everything = store.query_bids(latest_only=False)
    assert len(everything) == 2

    history = store.bid_history("rh1")
    assert [h["cash_price"] for h in history] == [4.15, 4.10]

    sigs = store.recent_signatures(sid)
    assert [s["content_signature"] for s in sigs] == ["sig2", "sig1"]

    assert store.latest_run_id() == run2
    assert store.latest_row_hashes(sid) == {"rh1"}


def test_query_bids_filters(store):
    cid_a = store.add_company("Alpha")
    cid_b = store.add_company("Beta")
    sid_a = store.add_site(cid_a, "https://a.example/bids")
    sid_b = store.add_site(cid_b, "https://b.example/bids")
    run = store.begin_run()
    ps_a = store.record_page_scan(run, sid_a, _fetch(), None, "ok")
    ps_b = store.record_page_scan(run, sid_b, _fetch(), None, "ok")
    store.save_bids(ps_a, sid_a, cid_a, [_row(commodity="CORN", rh="a1")])
    store.save_bids(ps_b, sid_b, cid_b, [_row(commodity="SOYBEANS", rh="b1")])
    store.finish_run(run, "complete")

    corn = store.query_bids(commodities=["CORN"])
    assert len(corn) == 1 and corn[0]["company_name"] == "Alpha"

    beta = store.query_bids(company_ids=[cid_b])
    assert len(beta) == 1 and beta[0]["commodity"] == "SOYBEANS"

    by_run = store.query_bids(run_id=run)
    assert len(by_run) == 2


def test_flags_lifecycle(store):
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids")
    run = store.begin_run()
    fid = store.add_flag(
        Flag("STALE_ASOF", "warn", "Bids look old.", "Check the page.", {"days": 4}),
        run_id=run, site_id=sid, company_id=cid,
    )
    open_flags = store.open_flags()
    assert len(open_flags) == 1
    assert open_flags[0]["code"] == "STALE_ASOF"
    assert open_flags[0]["severity"] == "warn"

    store.resolve_flag(fid)
    assert store.open_flags() == []


def test_discovered_links_upsert_and_status(store):
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids")
    links = [
        ScoredLink("https://a.example/cashbidssingle-2164", "W Burlington", 9.0, ("sibling",)),
        ScoredLink("https://a.example/grain", "Grain", 3.0, ("token",)),
    ]
    store.upsert_discovered_links(sid, links)
    store.upsert_discovered_links(sid, links)  # idempotent
    cands = store.candidate_links(sid)
    assert len(cands) == 2
    assert cands[0]["score"] == 9.0  # sorted desc

    store.set_link_status(cands[1]["id"], "dismissed")
    assert len(store.candidate_links(sid)) == 1


def test_builds_audit(store):
    run = store.begin_run()
    bid = store.record_build(run, {"output": {"charts": "none"}}, [{"path": "x.xlsx", "rows": 10}])
    builds = store.list_builds()
    assert builds[0]["id"] == bid
    assert builds[0]["files"][0]["path"] == "x.xlsx"
    assert builds[0]["settings_snapshot"]["output"]["charts"] == "none"


def test_robots_cache_roundtrip(store):
    assert store.get_robots_cache("a.example") is None
    store.set_robots_cache("a.example", "User-agent: *\nAllow: /")
    cached = store.get_robots_cache("a.example")
    assert "Allow" in cached["body"]
