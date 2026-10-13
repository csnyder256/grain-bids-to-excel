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


# ---- master workbook sheet granularity -------------------------------------
#
# ``master.sheet_per`` is a documented, schema-valid choice: "company" (the
# default) or "company_location". The second one used to change nothing but the
# columns: the workbook still held one sheet per company, so the bids of two
# location pages merged into a single sheet while the Location column - and the
# "Bids As Of" column beside it - were dropped. Nothing in the workbook said
# which elevator a merged row came from. The sheets are now actually split per
# location page.

@pytest.fixture
def two_location_store(tmp_path):
    """One company posting bids from two different location pages."""
    store = Store(str(tmp_path / "locations.db"))
    c1 = store.add_company("Prairie Ridge Grain")
    west = store.add_site(c1, "https://prairie.example/2163", "Westfield")
    north = store.add_site(c1, "https://prairie.example/2164", "Northfield")
    run = store.begin_run()
    ex_w = ExtractionResult(rows=[
        _row("CORN", "July 1 - 15", "2026-07-01", 4.10, -0.13, rh="w1"),
        _row("SOYBEANS", "October 2026", "2026-10-01", 10.25, -0.45, rh="w2"),
    ], as_of_date=date(2026, 7, 2))
    ps_w = store.record_page_scan(run, west, _fetch(), ex_w, "ok", content_signature="sw")
    store.save_bids(ps_w, west, c1, ex_w.rows)
    ex_n = ExtractionResult(rows=[
        _row("CORN", "July 1 - 15", "2026-07-01", 4.05, -0.18, rh="n1"),
    ], as_of_date=date(2026, 7, 2))
    ps_n = store.record_page_scan(run, north, _fetch(), ex_n, "ok", content_signature="sn")
    store.save_bids(ps_n, north, c1, ex_n.rows)
    store.finish_run(run, "complete")
    yield store, run
    store.close()


def _headers(ws):
    return [ws.cell(row=6, column=j).value for j in range(1, ws.max_column + 1)]


def _column_values(ws, header):
    """Values under one header, bounded by the table's own filter range so the
    merged footer line below the data is not mistaken for a row."""
    from openpyxl.utils import range_boundaries

    col = _headers(ws).index(header) + 1
    _, _, _, last = range_boundaries(ws.auto_filter.ref)
    return [ws.cell(row=r, column=col).value for r in range(7, last + 1)
            if ws.cell(row=r, column=col).value is not None]


def test_master_company_mode_is_unchanged(two_location_store, tmp_path):
    """The default still merges a company's locations into one sheet, and the
    Location column still says which one each row came from."""
    store, run = two_location_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    assert settings["output"]["master"]["sheet_per"] == "company"
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    wb = load_workbook(result.files[0].path)

    assert "Prairie Ridge Grain" in wb.sheetnames
    assert not any(n.startswith("Prairie Ridge Grain - ") for n in wb.sheetnames)
    ws = wb["Prairie Ridge Grain"]
    locations = _column_values(ws, "Location")
    assert len(locations) == 3          # both locations' bids, merged
    assert sorted(set(locations)) == ["Northfield", "Westfield"]


def test_master_company_location_mode_is_one_sheet_per_location(two_location_store, tmp_path):
    """``sheet_per: company_location`` splits the company's bids per location
    page instead of leaving them merged in one sheet."""
    store, run = two_location_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["output"]["master"]["sheet_per"] = "company_location"
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    wb = load_workbook(result.files[0].path)

    assert "Prairie Ridge Grain - Westfield" in wb.sheetnames
    # one sheet per location page, so the merged per-company sheet is gone
    split = [n for n in wb.sheetnames if n.startswith("Prairie Ridge Grain - ")]
    assert len(split) == 2, wb.sheetnames
    assert "Prairie Ridge Grain" not in wb.sheetnames

    assert _column_values(wb["Prairie Ridge Grain - Westfield"], "Cash Bid") == [4.10, 10.25]
    northfield = next(n for n in split if "Northfiel" in n)
    assert _column_values(wb[northfield], "Cash Bid") == [4.05]


def test_master_company_location_sheets_keep_location_and_as_of(two_location_store, tmp_path):
    """No column is lost: each per-location sheet still names its location and
    the page's as-of date (the old behaviour dropped both)."""
    store, run = two_location_store
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["output"]["master"]["sheet_per"] = "company_location"
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    wb = load_workbook(result.files[0].path)

    ws = wb["Prairie Ridge Grain - Westfield"]
    heads = _headers(ws)
    assert "Location" in heads
    assert "Bids As Of" in heads
    assert _column_values(ws, "Location") == ["Westfield", "Westfield"]


def test_master_company_location_keeps_same_labelled_pages_apart(tmp_path):
    """Two location pages that share a label are still two sheets: the split is
    by page, so neither location's bids are merged into the other."""
    store = Store(str(tmp_path / "same-label.db"))
    c1 = store.add_company("Prairie Ridge Grain")
    first = store.add_site(c1, "https://prairie.example/2163", "Main")
    second = store.add_site(c1, "https://prairie.example/2164", "Main")
    run = store.begin_run()
    for site, cash, rh in ((first, 4.10, "a1"), (second, 4.05, "b1")):
        ex = ExtractionResult(rows=[
            _row("CORN", "July 1 - 15", "2026-07-01", cash, -0.13, rh=rh),
        ], as_of_date=date(2026, 7, 2))
        ps = store.record_page_scan(run, site, _fetch(), ex, "ok", content_signature=f"s{rh}")
        store.save_bids(ps, site, c1, ex.rows)
    store.finish_run(run, "complete")

    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings["output"]["master"]["sheet_per"] = "company_location"
    result = builder.build_reports(store, settings, run, tmp_path / "out", now=NOW)
    wb = load_workbook(result.files[0].path)

    names = [n for n in wb.sheetnames if n.startswith("Prairie Ridge Grain")]
    assert len(names) == 2, names
    assert sorted(_column_values(wb[n], "Cash Bid")[0] for n in names) == [4.05, 4.10]
    store.close()
