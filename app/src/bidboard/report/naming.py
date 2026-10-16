"""Sheet-name, filename and location naming."""
from __future__ import annotations

import re

_ILLEGAL_SHEET = re.compile(r"[\[\]:*?/\\]")
_ILLEGAL_FILE = re.compile(r'[<>:"/\\|?*]')


def sheet_name(raw: str, taken: set[str]) -> str:
    name = _ILLEGAL_SHEET.sub(" ", raw).strip()
    name = re.sub(r"\s+", " ", name)
    if len(name) > 31:
        name = name[:31].strip()
    if not name:
        name = "Sheet"
    base = name
    n = 2
    while name.lower() in {t.lower() for t in taken}:
        suffix = f" ({n})"
        name = base[: 31 - len(suffix)].strip() + suffix
        n += 1
    taken.add(name)
    return name


def safe_filename(name: str) -> str:
    name = _ILLEGAL_FILE.sub("-", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Cash Bids.xlsx"


def page_address(url: str | None) -> str:
    """``https://host/path`` -> ``host/path``.

    The workbook's way of naming a page it has no friendlier name for. The
    Data Health sheet's "Page" column and a location's fallback name both use
    it, so one page is spelled the same way everywhere in the file.
    """
    return (url or "").strip().split("//")[-1].rstrip("/")


def location_display(label: str | None, url: str | None) -> str:
    """What a location is called on a sheet: its label, else its page address.

    Every sheet that names a location - the bid sheets' Location column, the
    cover's "Freshness at a glance" and "Best cash bids", the Data Health
    sheet - reads ``sites.label``. Nothing in the app's own add-company flow
    ever sets that label (the UI posts bare page URLs), so the column came out
    blank on every row of a real workbook, leaving two elevators' bids
    indistinguishable. A location with no name is still one specific page, so
    fall back to that page's address rather than showing nothing.
    """
    return (label or "").strip() or page_address(url)
