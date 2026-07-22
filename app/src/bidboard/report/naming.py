"""Sheet-name and filename sanitizing."""
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
