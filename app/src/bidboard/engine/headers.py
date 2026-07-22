"""Column-role assignment: synonym dictionary + fuzzy header matching +
value-shape inference for headerless grids."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .types import Flag

HEADER_SYNONYMS: dict[str, list[str]] = {
    "commodity": ["commodity", "commodities", "grain", "product", "crop"],
    "delivery_period": [
        "delivery", "delivery period", "delivery date", "delivery month",
        "delivery start", "delivery end", "del period", "del", "period",
        "shipment", "ship period", "time frame", "delivery window",
    ],
    "cash_price": [
        "cash bid", "cash bids", "cash price", "cash", "bid", "bids", "price",
        "local price", "net price", "posted price", "spot", "spot price",
        "flat price", "today's bid", "todays bid", "current bid", "your price",
        "cash bid price", "bid price",
    ],
    "basis": [
        "basis", "basis level", "basis price", "basis bid", "local basis",
    ],
    "futures_price": [
        "futures", "futures price", "future", "board", "board price",
        "settlement", "settle", "settlement price", "cbot", "cme",
        "futures quote", "market price", "futures settle", "futures close",
    ],
    "futures_month": [
        "futures month", "futures contract", "board month", "future month",
        "fut month", "futures symbol",
    ],
    "change": [
        "change", "chg", "chng", "+/-", "net change", "price change", "move",
        "chg.", "cash change", "basis change",
    ],
    "last_updated": [
        "last trade", "last updated", "as of", "updated", "date", "quote time",
        "last", "trade date", "quote date", "last update",
    ],
    "location": [
        "location", "elevator", "facility", "branch", "terminal",
        "delivery point", "station", "city", "site",
    ],
    "notes": ["notes", "comments", "remarks", "info"],
}

# terms too generic to trust on header text alone - must agree with shape
AMBIGUOUS = {"month", "date", "symbol", "price", "contract", "time", "name",
             "last", "close", "board"}

_ROLE_BY_ALIAS: list[tuple[str, str]] = [
    (alias, role) for role, aliases in HEADER_SYNONYMS.items() for alias in aliases
]

# ---- value-shape regexes ----
RE_PRICE = re.compile(r"^\$?\d{1,2}\.\d{2,4}$")
RE_PRICE_CENTS = re.compile(r"^\d{3,4}(\.\d{1,2})?$")
RE_BASIS = re.compile(r"^[+-]\s?\d{0,2}\.\d{2,4}$|^[+-]\s?\d{1,3}$")
RE_EIGHTHS = re.compile(r"^\d{3,4}-[0-7]s?$")
RE_CHANGE_8THS = re.compile(r"^[+-]\d+-[0-7]$")
RE_DATE_MDY = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")
RE_MONTHNAME = re.compile(
    r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
)
RE_FUT_MONTH = re.compile(
    r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*'?\d{2}\b"
    r"|\b[A-Z]{1,3}\s?[FGHJKMNQUVXZ]\d{1,2}\b"
)
RE_DATE_RANGE = re.compile(
    r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\s*[-–]"
)
RE_COMMODITY = re.compile(
    r"(?i)\b(corn|soybean|beans|wheat|oats|milo|sorghum|barley|canola|rye|sunflower)"
)


def match_header_role(header_text: str) -> tuple[str | None, float]:
    """Best (role, score 0-100) for a header cell via fuzzy synonym match."""
    if not header_text:
        return None, 0.0
    text = re.sub(r"[^a-z0-9 /+.-]", "", header_text.strip().lower()).strip()
    if not text:
        return None, 0.0

    best_role, best_score, runner = None, 0.0, 0.0
    for alias, role in _ROLE_BY_ALIAS:
        score = fuzz.token_set_ratio(text, alias)
        if score > best_score:
            runner = best_score
            best_role, best_score = role, score
        elif score > runner:
            runner = score

    if best_score >= 90 or (best_score >= 80 and best_score - runner >= 8):
        return best_role, best_score
    return None, best_score


def _shape_fraction(cells: list[str], pattern: re.Pattern) -> float:
    non_empty = [c for c in cells if c and c.strip()]
    if not non_empty:
        return 0.0
    hits = sum(1 for c in non_empty if pattern.match(c.strip()))
    return hits / len(non_empty)


def infer_column_role_by_shape(cells: list[str]) -> tuple[str | None, float]:
    """Role from value shapes when there's no usable header (>=70% match)."""
    non_empty = [c.strip() for c in cells if c and c.strip()]
    if not non_empty:
        return None, 0.0

    def frac(pat):
        return sum(1 for c in non_empty if pat.match(c)) / len(non_empty)

    # order matters: most specific shapes first
    if frac(RE_EIGHTHS) >= 0.6:
        return "futures_price", frac(RE_EIGHTHS)
    if frac(RE_CHANGE_8THS) >= 0.6:
        return "change", frac(RE_CHANGE_8THS)
    if frac(RE_FUT_MONTH) >= 0.5:
        return "futures_month", frac(RE_FUT_MONTH)
    if frac(RE_DATE_RANGE) >= 0.5:
        return "delivery_period", frac(RE_DATE_RANGE)
    if frac(RE_DATE_MDY) >= 0.6:
        return "last_updated", frac(RE_DATE_MDY)
    if frac(RE_COMMODITY) >= 0.6:
        return "commodity", frac(RE_COMMODITY)

    signed = frac(RE_BASIS)
    if signed >= 0.7:
        # basis vs change disambiguated later; default to basis
        return "basis", signed
    if frac(RE_PRICE) >= 0.7:
        return "cash_price", frac(RE_PRICE)
    if frac(RE_PRICE_CENTS) >= 0.7:
        return "cash_price", frac(RE_PRICE_CENTS)

    # month-name-only delivery (e.g. "December")
    monthy = sum(1 for c in non_empty if RE_MONTHNAME.search(c)) / len(non_empty)
    if monthy >= 0.6:
        return "delivery_period", monthy
    return None, 0.0
