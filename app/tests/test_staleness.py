"""Hashing, supersede semantics, and staleness/overlap flags."""
from datetime import date, datetime, timedelta, timezone

import pytest

from bidboard.engine import staleness as S
from bidboard.engine.store import Store
from bidboard.engine.types import BidRow, ExtractionResult, FetchResult


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def _row(commodity="CORN", start="2026-07-01", cash=4.10, basis=-0.13):
    return BidRow(
        commodity=commodity, commodity_raw=commodity, location_label="Westfield",
        delivery_start=date.fromisoformat(start),
        delivery_end=date.fromisoformat(start),
        futures_month="2026-09", cash_price=cash, basis=basis,
    )


def test_identity_stable_value_changes():
    r1 = _row(cash=4.10)
    r2 = _row(cash=4.20)
    assert S.row_identity_hash(1, r1) == S.row_identity_hash(1, r2)  # same bid
    assert S.value_hash(r1) != S.value_hash(r2)                       # different value


def test_content_signature_order_independent():
    a = [_row(start="2026-07-01"), _row(start="2026-08-01")]
    b = [_row(start="2026-08-01"), _row(start="2026-07-01")]
    S.stamp_hashes(1, a)
    S.stamp_hashes(1, b)
    assert S.content_signature(a) == S.content_signature(b)


def _fetch():
    return FetchResult(True, "u", "u", 1, 200, "<html></html>",
                       datetime.now(timezone.utc), 10)


def test_stale_asof_flag(store):
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")
    site = store.get_site(sid)
    rows = [_row()]
    S.stamp_hashes(sid, rows)
    ex = ExtractionResult(rows=rows, as_of_date=date(2026, 7, 1))
    flags = S.check_staleness(
        store, site, ex, {"aging_days": 2, "stale_days": 5, "identical_scans": 3},
        S.content_signature(rows), today=date(2026, 7, 10),
    )
    assert any(f.code == "STALE_ASOF" for f in flags)


def test_stale_delivery_all_past(store):
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")
    site = store.get_site(sid)
    rows = [_row(start="2026-01-01")]
    rows[0].delivery_end = date(2026, 1, 15)
    S.stamp_hashes(sid, rows)
    ex = ExtractionResult(rows=rows, as_of_date=date(2026, 7, 1))
    flags = S.check_staleness(
        store, site, ex, {"aging_days": 2, "stale_days": 5, "identical_scans": 3},
        S.content_signature(rows), today=date(2026, 7, 10),
    )
    assert any(f.code == "STALE_DELIVERY" for f in flags)


def _fetch_old(days_ago: int = 10):
    """A FetchResult dated well in the past so STALE_REPEAT's >=48h span gate
    is satisfied and the count/threshold logic is tested on its own."""
    return FetchResult(
        True, "u", "u", 1, 200, "<html></html>",
        datetime.now(timezone.utc) - timedelta(days=days_ago), 10,
    )


def _scan_identical(store, sid, cid, cash, fetch):
    """Record one page scan the way scan_site does - the scan is written
    *before* staleness is checked - and return its content signature."""
    rows = [_row(cash=cash)]
    S.stamp_hashes(sid, rows)
    sig = S.content_signature(rows)
    run = store.begin_run()
    ps = store.record_page_scan(
        run, sid, fetch,
        ExtractionResult(rows=rows, as_of_date=date(2026, 7, 1)),
        "ok", content_signature=sig,
    )
    store.save_bids(ps, sid, cid, rows)
    store.finish_run(run, "complete")
    return sig


def _repeat_flag(store, site, sig):
    flags = S.check_staleness(
        store, site,
        ExtractionResult(rows=[_row()], as_of_date=date(2026, 7, 1)),
        {"aging_days": 2, "stale_days": 5, "identical_scans": 3},
        sig, today=date(2026, 7, 10),
    )
    return next((f for f in flags if f.code == "STALE_REPEAT"), None)


def test_stale_repeat_fires_at_the_configured_scan_count(store):
    """``identical_scans`` = 3 means three identical scans, this one included.

    The current scan is already in ``recent_signatures`` when this runs, so the
    old ``>= identical_n - 1`` fired after only two identical scans - one scan
    early against the configured threshold."""
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")
    site = store.get_site(sid)

    seen = []
    for _ in range(3):
        sig = _scan_identical(store, sid, cid, 4.10, _fetch_old())
        seen.append(_repeat_flag(store, site, sig))

    assert seen[0] is None, "one identical scan is not a stuck page"
    assert seen[1] is None, "two identical scans must not trip a 3-scan rule"
    assert seen[2] is not None, "the third identical scan is the trigger"


def test_stale_repeat_message_counts_scans_that_actually_happened(store):
    """The warning must not name a scan that never occurred.

    With two scans on record the old code printed 'last 3 scans'; with three it
    printed 'last 4'."""
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")
    site = store.get_site(sid)

    for i in range(3):
        sig = _scan_identical(store, sid, cid, 4.10, _fetch_old())
        flag = _repeat_flag(store, site, sig)
        if flag is not None:
            assert f"last {i + 1} scans" in flag.message
            assert f"last {i + 2} scans" not in flag.message
            break
    else:  # pragma: no cover - guarded by the test above
        pytest.fail("STALE_REPEAT never fired")


def test_stale_repeat_still_needs_the_48h_span(store):
    """Three identical scans inside 48 hours are a weekend, not a frozen page."""
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")
    site = store.get_site(sid)

    for _ in range(3):
        sig = _scan_identical(store, sid, cid, 4.10, _fetch())  # fresh timestamp
        assert _repeat_flag(store, site, sig) is None


def test_supersede_not_duplicate(store):
    """Two scans of the same bid → query_bids(latest_only) returns one row."""
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")

    for cash in (4.10, 4.20):
        run = store.begin_run()
        rows = [_row(cash=cash)]
        S.stamp_hashes(sid, rows)
        ps = store.record_page_scan(run, sid, _fetch(),
                                    ExtractionResult(rows=rows, as_of_date=date(2026, 7, 2)),
                                    "ok", content_signature=S.content_signature(rows))
        store.save_bids(ps, sid, cid, rows)
        store.finish_run(run, "complete")

    latest = store.query_bids(latest_only=True)
    assert len(latest) == 1
    assert latest[0]["cash_price"] == 4.20


def test_unparsed_delivery_rows_keep_distinct_identities(store):
    """Refute fix: 'Spot' + 'Nearby' style rows (no parseable dates, no
    futures month) must NOT collide and vanish from latest_only queries."""
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")

    def spotrow(label, cash):
        r = _row(cash=cash)
        r.delivery_start = None
        r.delivery_end = None
        r.futures_month = None
        r.delivery_raw = label
        r.delivery_label = label
        return r

    rows = [spotrow("Spot", 4.10), spotrow("Nearby", 3.90)]
    S.stamp_hashes(sid, rows)
    assert rows[0].row_hash != rows[1].row_hash

    run = store.begin_run()
    ps = store.record_page_scan(run, sid, _fetch(), ExtractionResult(rows=rows), "ok")
    store.save_bids(ps, sid, cid, rows)
    store.finish_run(run, "complete")

    latest = store.query_bids(latest_only=True)
    assert len(latest) == 2  # neither silently dropped
    assert {r["cash_price"] for r in latest} == {4.10, 3.90}

    # identity is stable across scans -> supersede still works
    rows2 = [spotrow("Spot", 4.15), spotrow("Nearby", 3.95)]
    S.stamp_hashes(sid, rows2)
    assert rows2[0].row_hash == rows[0].row_hash
    run2 = store.begin_run()
    ps2 = store.record_page_scan(run2, sid, _fetch(), ExtractionResult(rows=rows2), "ok")
    store.save_bids(ps2, sid, cid, rows2)
    store.finish_run(run2, "complete")
    latest = store.query_bids(latest_only=True)
    assert len(latest) == 2
    assert {r["cash_price"] for r in latest} == {4.15, 3.95}


def test_truly_indistinguishable_rows_disambiguated_by_order(store):
    """Even rows with EMPTY labels get ordinal-suffixed hashes + a note."""
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")
    rows = []
    for cash in (4.30, 4.40):
        r = _row(cash=cash)
        r.delivery_start = r.delivery_end = None
        r.futures_month = None
        r.delivery_raw = r.delivery_label = ""
        rows.append(r)
    S.stamp_hashes(sid, rows)
    assert rows[0].row_hash != rows[1].row_hash
    assert "duplicate_identity_disambiguated" in rows[1].notes


def test_overlap_detection(store):
    cid = store.add_company("Co")
    sid_a = store.add_site(cid, "https://a.example/bids", "Page A")
    sid_b = store.add_site(cid, "https://a.example/bids2", "Page B")

    rows_a = [_row(start="2026-07-01"), _row(start="2026-08-01")]
    S.stamp_hashes(sid_a, rows_a)
    run = store.begin_run()
    ps = store.record_page_scan(run, sid_a, _fetch(),
                                ExtractionResult(rows=rows_a), "ok")
    store.save_bids(ps, sid_a, cid, rows_a)
    store.finish_run(run, "complete")

    # site B has the SAME identities (same site_id hashing would differ, so
    # build B's rows hashed under B but with identical business keys ->
    # here we assert the >=80% overlap path using B's own hashes)
    rows_b = [_row(start="2026-07-01"), _row(start="2026-08-01")]
    S.stamp_hashes(sid_a, rows_b)  # hash under A's id to simulate same page
    site_b = store.get_site(sid_b)
    flags = S.check_overlap(store, cid, site_b, rows_b)
    assert any(f.code == "DUPLICATE_PAGE_OVERLAP" for f in flags)
