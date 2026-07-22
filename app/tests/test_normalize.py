"""Parametrized normalization tests - the parsing edge cases."""
from datetime import date

import pytest

from bidboard.engine import normalize as N


@pytest.mark.parametrize("raw,expected", [
    ("4.1000", 4.10),
    ("$4.25", 4.25),
    ("423-0s", 4.23),        # eighths: 423 cents -> $4.23
    ("423-4", 4.235),        # 423 + 4/8 cents -> 423.5c -> $4.235
    ("1052.5", 10.525),      # cents (>50) -> dollars
    ("", None),
    ("N/A", None),
])
def test_parse_price(raw, expected):
    value, _ = N.parse_price(raw)
    if expected is None:
        assert value is None
    else:
        assert value == pytest.approx(expected)


@pytest.mark.parametrize("raw,expected", [
    ("-0.1300", -0.13),
    ("+0.05", 0.05),
    ("-13", -0.13),          # bare signed int -> cents
    ("+15", 0.15),
    ("0.00", 0.0),
])
def test_parse_basis(raw, expected):
    value, _ = N.parse_basis(raw)
    assert value == pytest.approx(expected)


def test_parse_basis_out_of_range():
    value, notes = N.parse_basis("-9.99")
    assert value is None
    assert "basis_out_of_range" in notes


@pytest.mark.parametrize("raw,expected", [
    ("+0-2", 0.0025),        # +2/8 cent -> $0.0025
    ("-0.05", -0.05),
    ("unch", 0.0),
    ("uc", 0.0),
    (" - ", 0.0),
])
def test_parse_change(raw, expected):
    assert N.parse_change(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw,expected", [
    ("Sep 26 Corn", "2026-09"),
    ("Sep 26", "2026-09"),
    ("December 2026", "2026-12"),
    ("ZCU26", "2026-09"),
    ("Dec '26", "2026-12"),
])
def test_parse_futures_month(raw, expected):
    assert N.parse_futures_month(raw, date(2026, 7, 2)) == expected


@pytest.mark.parametrize("commodity,alias", [
    ("CORN", "Corn"),
    ("CORN", "#2 Yellow Corn"),
    ("CORN", "yellow corn"),
    ("SOYBEANS", "Beans"),
    ("SOYBEANS", "Soybeans"),
    ("WHEAT_SRW", "SRW Wheat"),
    ("WHEAT_HRW", "Hard Red Winter Wheat"),
    ("SORGHUM", "Milo"),
    ("OATS", "#2 Oats"),
])
def test_canon_commodity(commodity, alias):
    assert N.canon_commodity(alias) == commodity


def test_canon_commodity_unknown():
    assert N.canon_commodity("Unobtanium") is None


def test_delivery_same_month_range():
    start, end, notes = N.parse_delivery("July 1 - 15", date(2026, 7, 2))
    assert start == date(2026, 7, 1)
    assert end == date(2026, 7, 15)
    assert "year_inferred" in notes


def test_delivery_month_year():
    start, end, _ = N.parse_delivery("October 2026", date(2026, 7, 2))
    assert start == date(2026, 10, 1)
    assert end == date(2026, 10, 31)


def test_delivery_cross_month():
    start, end, _ = N.parse_delivery("July 16 - August 15", date(2026, 7, 2))
    assert start == date(2026, 7, 16)
    assert end == date(2026, 8, 15)


def test_delivery_new_crop_uses_commodity_window():
    start, end, notes = N.parse_delivery("New Crop", date(2026, 7, 2), "CORN")
    assert start == date(2026, 10, 1)
    assert end == date(2026, 11, 30)
    assert "newcrop_window_assumed" in notes


def test_delivery_year_inference_new_year_boundary():
    # scraped Dec 30 2026, "January 1 - 15" should roll to 2027
    start, end, _ = N.parse_delivery("January 1 - 15", date(2026, 12, 30))
    assert start == date(2027, 1, 1)
    assert end == date(2027, 1, 15)


def test_delivery_unparsed_flagged():
    start, end, notes = N.parse_delivery("whenever", date(2026, 7, 2))
    assert start is None and end is None
    assert "delivery_unparsed" in notes


def test_parse_as_of_longform():
    assert N.parse_as_of("Cash bids for Thursday, July 02, 2026") == date(2026, 7, 2)
