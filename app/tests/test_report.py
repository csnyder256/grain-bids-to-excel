"""Report builder: structural round-trip, formats, zero-external-links."""
import zipfile
from datetime import date, datetime, timezone

import pytest
from openpyxl import load_workbook

from bidboard.engine.store import Store
from bidboard.engine.types import BidRow, ExtractionResult, FetchResult
from bidboard.report import builder, styles
from bidboard.settings import DEFAULT_SETTINGS
import copy

NOW = datetime(2026, 7, 4, 8, 30)


def _fetch():
    return FetchResult(True, "https://x.example/bids", "https://x.example/bids",
                       1, 200, "<html></html>", datetime.now(timezone.utc), 10)


def _row(commodity, delivery, start, cash, basis, futures=4.23, rh=None):
    r = BidRow(
        commodity=commodity, commodity_raw=commodity, location_label="Westfield",
        delivery_raw=delivery, delivery_label=delivery,
        delivery_start=date.fromisoformat(start),
        delivery_end=date.fromisoformat(start),
        futures_month="2026-09", cash_price=cash, basis=basis,
        futures_price=futures, change=0.0025,
    )
    from bidboard.engine import staleness as S
    r.row_hash = rh or S.row_identity_hash(1, r)
    r.value_hash = S.value_hash(r)
    return r


@pytest.fixture
def seeded_store(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    c1 = store.add_company("Prairie Ridge Grain")
    c2 = store.add_company("Prairie Grain Co-op")
    s1 = store.add_site(c1, "https://prairieridge.example/2163", "Westfield")
    s2 = store.add_site(c2, "https://prairie.example/bids", "Main")
    run = store.begin_run()
    ex1 = ExtractionResult(rows=[
        _row("CORN", "July 1 - 15", "2026-07-01", 4.10, -0.13, rh="a1"),
        _row("CORN", "August 1 - 15", "2026-08-01", 4.14, -0.09, rh="a2"),
        _row("SOYBEANS", "October 2026", "2026-10-01", 10.25, -0.45, rh="a3"),
    ], as_of_date=date(2026, 7, 2))
    ps1 = store.record_page_scan(run, s1, _fetch(), ex1, "ok", content_signature="sig1")
    store.save_bids(ps1, s1, c1, ex1.rows)
    ex2 = ExtractionResult(rows=[
        _row("CORN", "July 1 - 15", "2026-07-01", 4.08, -0.15, rh="b1"),
    ], as_of_date=date(2026, 7, 1))
    ps2 = store.record_page_scan(run, s2, _fetch(), ex2, "ok", content_signature="sig2")
    store.save_bids(ps2, s2, c2, ex2.rows)
    store.finish_run(run, "complete")
    yield store, run
    store.close()


def test_master_workbook_structure(seeded_store, tmp_path):
    store, run = seeded_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)

    assert len(result.files) == 1
    path = result.files[0].path
    wb = load_workbook(path)

    assert "Summary" in wb.sheetnames
    assert "Data Health" in wb.sheetnames
    assert "Prairie Ridge Grain" in wb.sheetnames
    assert "All Bids" in wb.sheetnames

    cover = wb["Summary"]
    assert cover["A1"].value == "Cash Bid Report"

    bidsheet = wb["Prairie Ridge Grain"]
    assert bidsheet["A1"].value == "Prairie Ridge Grain"
    assert bidsheet.freeze_panes == "A7"
    assert bidsheet.auto_filter.ref is not None
    assert bidsheet.sheet_view.showGridLines is False


def test_number_formats_and_values(seeded_store, tmp_path):
    store, run = seeded_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    wb = load_workbook(result.files[0].path)
    ws = wb["Prairie Ridge Grain"]

    header_row = 6
    headers = [ws.cell(row=header_row, column=j).value for j in range(1, 12)]
    assert "Cash Bid" in headers
    assert "Basis" in headers

    cash_col = headers.index("Cash Bid") + 1
    basis_col = headers.index("Basis") + 1
    cash_cell = ws.cell(row=header_row + 1, column=cash_col)
    basis_cell = ws.cell(row=header_row + 1, column=basis_col)
    assert cash_cell.number_format == styles.fmt_cash(4)
    assert basis_cell.number_format == styles.fmt_basis(4)
    # values present
    cash_values = [ws.cell(row=r, column=cash_col).value
                   for r in range(header_row + 1, header_row + 6)]
    assert 4.10 in cash_values


def test_no_external_links_no_formulas(seeded_store, tmp_path):
    store, run = seeded_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["output"]["charts"] = "both"  # exercise chart path too
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    path = result.files[0].path

    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert not any(n.startswith("xl/externalLinks/") for n in names)

    wb = load_workbook(path)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    assert not cell.value.startswith("="), f"formula in {ws.title}!{cell.coordinate}"


def test_charts_created_when_enabled(seeded_store, tmp_path):
    store, run = seeded_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["output"]["charts"] = "bars"
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    wb = load_workbook(result.files[0].path)
    assert "_ChartData" in wb.sheetnames
    assert wb["_ChartData"].sheet_state == "hidden"


def test_per_company_mode_one_file_each(seeded_store, tmp_path):
    store, run = seeded_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["output"]["workbook_modes"] = ["per_company"]
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    assert len(result.files) == 2
    scopes = {f.scope for f in result.files}
    assert "Prairie Ridge Grain" in scopes
    assert "Prairie Grain Co-op" in scopes


def test_never_overwrites(seeded_store, tmp_path):
    store, run = seeded_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    out = tmp_path / "out"
    r1 = builder.build_reports(store, settings, run, out, now=NOW)
    r2 = builder.build_reports(store, settings, run, out, now=NOW)
    assert r1.files[0].path != r2.files[0].path  # second got a " (2)" name


def test_company_override_applies(seeded_store, tmp_path):
    store, run = seeded_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    # restrict Prairie Ridge (company id 1) to SOYBEANS only
    settings["company_overrides"] = {"1": {"commodities": ["SOYBEANS"]}}
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    wb = load_workbook(result.files[0].path)
    ws = wb["Prairie Ridge Grain"]
    # only soybean rows -> commodity column should show SOYBEANS, not CORN
    commodities = set()
    for r in range(7, 20):
        v = ws.cell(row=r, column=1).value
        if v:
            commodities.add(v)
    assert "CORN" not in commodities


def test_numeric_as_of_page_ages_in_data_health(tmp_path):
    """End to end: a page whose as-of date is written MM/DD/YYYY ages properly.

    The extractor reads the date out of the header phrase, so Data Health gets
    the real as-of date and reports the page STALE with its true age. While the
    numeric date was being dropped the page sat on AGING with no age at all and
    could never reach STALE, however long its bids went unchanged.
    """
    from bidboard.engine.extractor import extract_bids
    from bidboard.report import health as HEALTH

    page = (
        "<html><body><h2>Corn</h2>"
        '<p class="fcControlsSectionHeader">Cash Bids as of 06/20/2026</p>'
        "<table>"
        "<tr><th>Delivery</th><th>Cash Price</th><th>Basis</th></tr>"
        "<tr><td>July 2026</td><td>4.10</td><td>-0.25</td></tr>"
        "<tr><td>August 2026</td><td>4.20</td><td>-0.20</td></tr>"
        "</table></body></html>"
    )
    store = Store(str(tmp_path / "health.db"))
    try:
        cid = store.add_company("Prairie Ridge Grain")
        sid = store.add_site(cid, "https://prairie.example/bids", "Westfield")
        res = extract_bids(page, "https://prairie.example/bids", location="Westfield")
        assert res.as_of_date == date(2026, 6, 20)

        run = store.begin_run()
        ps = store.record_page_scan(run, sid, _fetch(), res, "ok",
                                    content_signature="sig")
        store.save_bids(ps, sid, cid, res.rows)
        store.finish_run(run, "complete")

        health = HEALTH.compute_health(
            store, run, {"aging_days": 2, "stale_days": 5},
            datetime(2026, 7, 4, 8, 30),
        )
        row = next(h for h in health if h.company == "Prairie Ridge Grain")
        assert row.as_of == date(2026, 6, 20)
        assert row.age_days == 14
        assert row.status == "STALE"
    finally:
        store.close()
