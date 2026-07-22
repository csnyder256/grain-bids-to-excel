"""Extractor golden test on a synthetic vendor-widget fixture, plus
synthetic grids covering tables, headerless grids, and ambiguity."""
from datetime import date
from pathlib import Path

import pytest

from bidboard.engine.detect import detect_vendor
from bidboard.engine.extractor import extract_bids

FIXTURES = Path(__file__).parent / "fixtures"

# Hand-written fixture imitating the SHAPE of a server-rendered Bushel
# cash-bids widget (repeated `ul.sevenColumnsBigFirst` grids). Company,
# location and every price are invented; see the comment in the file.
GOLDEN_URL = "https://prairieridgegrain.example/cashbidssingle-2163"


@pytest.fixture(scope="module")
def bushel_grid_html():
    return (FIXTURES / "sample_bushel_bids.html").read_text(encoding="utf-8")


def test_bushel_grid_golden(bushel_grid_html):
    res = extract_bids(bushel_grid_html, GOLDEN_URL, location="Westfield")
    assert res.as_of_date == date(2026, 7, 2)
    assert res.container_score >= 8
    assert len(res.rows) == 15
    assert all(r.commodity == "CORN" for r in res.rows)
    assert not res.flags  # clean extraction

    # the anchor row: July 1-15, 4.0000, -0.1500, 415-0s, +0-2, Sep 26
    july = next(r for r in res.rows if r.delivery_label == "July 1 - 15")
    assert july.cash_price == pytest.approx(4.00)
    assert july.basis == pytest.approx(-0.15)
    assert july.futures_price == pytest.approx(4.15)   # 415-0s eighths
    assert july.change == pytest.approx(0.0025)        # +0-2
    assert july.futures_month == "2026-09"             # Sep 26
    assert july.delivery_start == date(2026, 7, 1)
    assert july.delivery_end == date(2026, 7, 15)

    # arithmetic identity holds for every fully-parsed row
    for r in res.rows:
        if r.cash_price is not None and r.basis is not None and r.futures_price is not None:
            assert abs(r.cash_price - (r.futures_price + r.basis)) <= 0.03


def test_bushel_grid_detected_as_bushel(bushel_grid_html):
    hints = detect_vendor(bushel_grid_html, GOLDEN_URL)
    assert hints.vendor == "bushel_ssr"
    assert hints.needs_js is False


PLAIN_TABLE = """
<html><body><h2>Soybeans</h2>
<table>
<tr><th>Delivery</th><th>Cash Price</th><th>Basis</th></tr>
<tr><td>October 2026</td><td>10.25</td><td>-0.45</td></tr>
<tr><td>November 2026</td><td>10.40</td><td>-0.40</td></tr>
</table></body></html>
"""


def test_plain_table_with_headers():
    res = extract_bids(PLAIN_TABLE, "https://x.example/beans")
    assert len(res.rows) == 2
    assert all(r.commodity == "SOYBEANS" for r in res.rows)
    r = res.rows[0]
    assert r.cash_price == pytest.approx(10.25)
    assert r.basis == pytest.approx(-0.45)
    assert r.delivery_start == date(2026, 10, 1)


HEADERLESS_DIVGRID = """
<html><body><h3>Corn</h3>
<div class="grid">
  <div class="row"><span>July 2026</span><span>4.10</span><span>-0.13</span></div>
  <div class="row"><span>August 2026</span><span>4.14</span><span>-0.09</span></div>
  <div class="row"><span>September 2026</span><span>4.07</span><span>-0.16</span></div>
</div></body></html>
"""


def test_headerless_divgrid_shape_inference():
    res = extract_bids(HEADERLESS_DIVGRID, "https://x.example/corn")
    assert len(res.rows) == 3
    r = res.rows[0]
    assert r.commodity == "CORN"
    assert r.cash_price == pytest.approx(4.10)
    assert r.basis == pytest.approx(-0.13)


NO_BIDS = """
<html><body><nav><a href="/x">Home</a></nav>
<p>Welcome to our co-op. Call us for prices.</p></body></html>
"""


def test_no_bid_data_flag():
    res = extract_bids(NO_BIDS, "https://x.example/")
    assert not res.rows
    assert any(f.code == "NO_BID_DATA" for f in res.flags)


BASIS_CHANGE_AMBIGUOUS = """
<html><body><h2>Corn</h2>
<table>
<tr><th>Delivery</th><th>Cash</th><th>Futures</th><th>Basis</th><th>Change</th></tr>
<tr><td>Oct 2026</td><td>4.10</td><td>4.23</td><td>-0.13</td><td>+0.02</td></tr>
<tr><td>Nov 2026</td><td>4.14</td><td>4.23</td><td>-0.09</td><td>-0.01</td></tr>
</table></body></html>
"""


REPEATED_HEADER_BLOCKS = """
<html><body><h2>Corn</h2>
<table>
<tr><th>Delivery</th><th>Cash Price</th><th>Basis</th></tr>
<tr><td>October 2026</td><td>4.10</td><td>-0.25</td></tr>
<tr><td>Delivery</td><td>Cash Price</td><td>Basis</td></tr>
<tr><td>November 2026</td><td>4.20</td><td>-0.20</td></tr>
</table></body></html>
"""


def test_repeated_header_rows_not_extracted_as_data():
    """AgriCharts-style pages repeat a header row per location block - 
    those all-text rows must never become bid rows."""
    res = extract_bids(REPEATED_HEADER_BLOCKS, "https://x.example/corn")
    assert len(res.rows) == 2
    assert all(r.cash_price is not None for r in res.rows)
    assert not any(r.delivery_raw == "Delivery" for r in res.rows)


def test_basis_change_arithmetic_disambiguation():
    res = extract_bids(BASIS_CHANGE_AMBIGUOUS, "https://x.example/corn")
    assert len(res.rows) == 2
    r = res.rows[0]
    # basis is the column matching cash - futures = 4.10 - 4.23 = -0.13
    assert r.basis == pytest.approx(-0.13)
    assert r.change == pytest.approx(0.02)
