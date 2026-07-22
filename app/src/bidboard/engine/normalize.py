"""Value normalization - turn verbatim page text into canonical numbers.

Every function returns (value, notes) or a value plus never raises; an
unparseable input yields None so the row keeps its raw text and gets a note.
All prices normalize to dollars per bushel.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# ---- commodity canonicalization ----

COMMODITY_ALIASES: dict[str, list[str]] = {
    "CORN": ["corn", "yellow corn", "#2 yellow corn", "no 2 yellow corn",
             "#2 corn", "no 2 corn", "yc", "corn 2 yellow", "us #2 corn",
             "#2 yellow", "yellow"],
    "WHITE_CORN": ["white corn", "#2 white corn"],
    "SOYBEANS": ["soybeans", "soybean", "beans", "#1 soybeans", "no 1 soybeans",
                 "#2 soybeans", "yellow soybeans", "sb", "syb", "soy"],
    "WHEAT_SRW": ["srw wheat", "soft red winter wheat", "soft red wheat",
                  "sr winter wheat", "#2 srw", "chicago wheat", "srw"],
    "WHEAT_HRW": ["hrw wheat", "hard red winter wheat", "hard red wheat",
                  "#1 hrw", "kc wheat", "kansas city wheat", "hrw"],
    "WHEAT_HRS": ["hrs wheat", "hard red spring wheat", "spring wheat",
                  "dns wheat", "dark northern spring", "14% spring wheat",
                  "mpls wheat", "hrs"],
    "WHEAT": ["wheat", "#2 wheat", "winter wheat"],
    "OATS": ["oats", "#2 oats", "white oats"],
    "SORGHUM": ["sorghum", "milo", "grain sorghum", "#2 milo", "#2 sorghum"],
    "BARLEY": ["barley", "feed barley", "malting barley"],
    "RYE": ["rye"],
    "CANOLA": ["canola", "rapeseed"],
    "SUNFLOWERS": ["sunflowers", "sunflower", "sunflower seeds", "nusun",
                   "oil sunflowers"],
}

# built once: alias -> canonical, longest-alias-first for greedy matching
_ALIAS_TO_CANON: list[tuple[str, str]] = sorted(
    ((alias, canon) for canon, aliases in COMMODITY_ALIASES.items()
     for alias in aliases),
    key=lambda pair: len(pair[0]),
    reverse=True,
)

_GRADE_STRIP = re.compile(r"(?i)^\s*(us\s+)?(#|no\.?\s*)\d\s*")

FUTURES_ROOTS = {
    "ZC": "CORN", "C": "CORN", "ZS": "SOYBEANS", "S": "SOYBEANS",
    "ZW": "WHEAT_SRW", "W": "WHEAT_SRW", "KE": "WHEAT_HRW", "KW": "WHEAT_HRW",
    "MWE": "WHEAT_HRS", "MW": "WHEAT_HRS", "ZO": "OATS", "O": "OATS",
}

MONTH_CODES = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
               "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_MONTH_LAST_DAY = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
                   7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def canon_commodity(raw: str) -> str | None:
    if not raw:
        return None
    text = raw.strip().lower()
    text = _GRADE_STRIP.sub("", text).strip()
    # exact / substring alias match (greedy longest first)
    for alias, canon in _ALIAS_TO_CANON:
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return canon
    # fuzzy fallback
    try:
        from rapidfuzz import fuzz, process

        choice = process.extractOne(
            text,
            [a for a, _ in _ALIAS_TO_CANON],
            scorer=fuzz.partial_ratio,
            score_cutoff=90,
        )
        if choice:
            alias = choice[0]
            return dict(_ALIAS_TO_CANON)[alias]
    except Exception:
        pass
    return None


# ---- price parsing ----

_EIGHTHS_RE = re.compile(r"^([+-]?)(\d{1,4})-([0-7])(s)?$")
_NUM_RE = re.compile(r"[-+]?\d*\.?\d+")


def _parse_eighths(text: str) -> float | None:
    """'423-0s' -> 4.23 ($/bu); '+0-2' -> +0.0025. Whole part is cents,
    fraction is eighths of a cent; result converted to dollars."""
    m = _EIGHTHS_RE.match(text.strip())
    if not m:
        return None
    sign = -1.0 if m.group(1) == "-" else 1.0
    whole = int(m.group(2))
    eighths = int(m.group(3))
    cents = whole + eighths / 8.0
    return sign * cents / 100.0


def parse_price(raw: str, context: str = "cash") -> tuple[float | None, list[str]]:
    """Cash/futures price -> $/bu. Handles $, commas, eighths, and
    cents-vs-dollars disambiguation."""
    notes: list[str] = []
    if raw is None:
        return None, notes
    text = raw.strip().replace("$", "").replace(",", "").strip()
    if not text or text.lower() in {"n/a", "na", "-", " - ", "closed", "call"}:
        return None, notes

    eighths = _parse_eighths(text)
    if eighths is not None:
        return round(eighths, 6), notes

    m = _NUM_RE.search(text)
    if not m:
        return None, notes
    try:
        value = float(m.group())
    except ValueError:
        return None, notes

    # grain cash bids never legitimately exceed ~$50/bu; larger = cents
    if abs(value) > 50:
        notes.append("unit_assumed_cents")
        value = value / 100.0
    return round(value, 6), notes


def parse_basis(raw: str) -> tuple[float | None, list[str]]:
    notes: list[str] = []
    if raw is None:
        return None, notes
    text = raw.strip().replace("$", "").replace(",", "").strip()
    if not text or text.lower() in {"n/a", "na", "-", " - "}:
        return None, notes

    eighths = _parse_eighths(text)
    if eighths is not None:
        return round(eighths, 6), notes

    m = _NUM_RE.search(text)
    if not m:
        return None, notes
    try:
        value = float(m.group())
    except ValueError:
        return None, notes
    # bare signed integer with no decimal point = cents (e.g. -13 -> -0.13)
    if "." not in m.group() and abs(value) >= 3:
        notes.append("unit_assumed_cents")
        value = value / 100.0
    if abs(value) > 5.0:
        notes.append("basis_out_of_range")
        return None, notes
    return round(value, 6), notes


def parse_change(raw: str) -> float | None:
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None  # absent is not the same as unchanged
    if text in {"unch", "uc", "nc", " - ", "-", "0", "0.00"}:
        return 0.0
    eighths = _parse_eighths(raw.strip())
    if eighths is not None:
        return round(eighths, 6)
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return round(float(m.group()), 6)
    except ValueError:
        return None


# ---- futures month ----

_FUT_NAME_RE = re.compile(
    r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*'?(\d{2,4})\b"
)
_FUT_SYMBOL_RE = re.compile(r"(?i)^@?([A-Z]{1,3})\s?([FGHJKMNQUVXZ])(\d{1,2})$")


def _pivot_year(yy: int) -> int:
    return 2000 + yy if yy < 80 else 1900 + yy


def parse_futures_month(raw: str, ref_date: date | None = None) -> str | None:
    """Return 'YYYY-MM' from 'Sep 26', 'Sep 26 Corn', 'ZCU26', '@C U6'."""
    if not raw:
        return None
    text = raw.strip()

    m = _FUT_NAME_RE.search(text)
    if m:
        month = MONTH_NAMES.get(m.group(1).lower())
        yy = int(m.group(2))
        year = yy if yy > 100 else _pivot_year(yy)
        if month:
            return f"{year:04d}-{month:02d}"

    compact = text.replace(" ", "").upper()
    m = _FUT_SYMBOL_RE.match(compact)
    if m:
        month = MONTH_CODES.get(m.group(2))
        yy = int(m.group(3))
        ref = ref_date or date.today()
        if yy < 10:  # single digit -> nearest plausible future year
            year = (ref.year // 10) * 10 + yy
            if year < ref.year - 1:
                year += 10
        else:
            year = _pivot_year(yy)
        if month:
            return f"{year:04d}-{month:02d}"
    return None


# ---- delivery period ----

_RANGE_SAME_MONTH_RE = re.compile(
    r"(?i)([a-z]+)\.?\s+(\d{1,2})\s*[-–]\s*(\d{1,2})(?:\s*,?\s*(\d{4}))?"
)
_RANGE_CROSS_MONTH_RE = re.compile(
    r"(?i)([a-z]+)\.?\s+(\d{1,2})\s*[-–]\s*([a-z]+)\.?\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?"
)
_MONTH_YEAR_RE = re.compile(r"(?i)^([a-z]+)\.?\s*(\d{4})$")
_MONTH_SLASH_RE = re.compile(r"(?i)^([a-z]+)\s*/\s*([a-z]+)$")
_LITERAL_RANGE_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{2,4})"
)

NEW_CROP_WINDOWS = {  # (start_month, start_day, end_month, end_day)
    "CORN": (10, 1, 11, 30), "WHITE_CORN": (10, 1, 11, 30),
    "SOYBEANS": (10, 1, 11, 30),
    "WHEAT": (6, 15, 7, 31), "WHEAT_SRW": (6, 15, 7, 31),
    "WHEAT_HRW": (6, 15, 7, 31), "WHEAT_HRS": (8, 1, 9, 15),
}


def _infer_year(month: int, day: int, ref_date: date) -> int:
    """Pick the year placing this date >= ref_date - 45 days, else next."""
    candidate = ref_date.year
    try:
        d = date(candidate, month, min(day, _MONTH_LAST_DAY[month]))
    except ValueError:
        d = date(candidate, month, 1)
    if d < ref_date - timedelta(days=45):
        candidate += 1
    return candidate


def _parse_mdy(text: str) -> date | None:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text.strip())
    if not m:
        return None
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = yy if yy > 100 else _pivot_year(yy)
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def parse_delivery(
    raw: str, ref_date: date | None = None, commodity: str | None = None
) -> tuple[date | None, date | None, list[str]]:
    """Return (start, end, notes). Preserves nothing but the dates; the
    caller keeps the raw label."""
    notes: list[str] = []
    if not raw:
        return None, None, notes
    ref = ref_date or date.today()
    text = raw.strip()

    # literal MM/DD/YY - MM/DD/YY
    m = _LITERAL_RANGE_RE.search(text)
    if m:
        return _parse_mdy(m.group(1)), _parse_mdy(m.group(2)), notes

    # cross-month range: "July 16 - August 15"
    m = _RANGE_CROSS_MONTH_RE.search(text)
    if m:
        m1 = MONTH_NAMES.get(m.group(1).lower())
        m2 = MONTH_NAMES.get(m.group(3).lower())
        if m1 and m2:
            d1, d2 = int(m.group(2)), int(m.group(4))
            if m.group(5):
                y1 = y2 = int(m.group(5))
            else:
                y1 = _infer_year(m1, d1, ref)
                y2 = y1 + 1 if m2 < m1 else y1
                notes.append("year_inferred")
            return (_safe_date(y1, m1, d1), _safe_date(y2, m2, d2), notes)

    # same-month range: "July 1 - 15"
    m = _RANGE_SAME_MONTH_RE.search(text)
    if m and m.group(1).lower() in MONTH_NAMES:
        month = MONTH_NAMES[m.group(1).lower()]
        d1, d2 = int(m.group(2)), int(m.group(3))
        if m.group(4):
            year = int(m.group(4))
        else:
            year = _infer_year(month, d1, ref)
            notes.append("year_inferred")
        return (_safe_date(year, month, d1), _safe_date(year, month, d2), notes)

    # "October 2026" / "Oct 2026"
    m = _MONTH_YEAR_RE.match(text)
    if m and m.group(1).lower() in MONTH_NAMES:
        month = MONTH_NAMES[m.group(1).lower()]
        year = int(m.group(2))
        return (_safe_date(year, month, 1),
                _safe_date(year, month, _MONTH_LAST_DAY[month]), notes)

    # "Oct/Nov"
    m = _MONTH_SLASH_RE.match(text)
    if m and m.group(1).lower() in MONTH_NAMES and m.group(2).lower() in MONTH_NAMES:
        m1 = MONTH_NAMES[m.group(1).lower()]
        m2 = MONTH_NAMES[m.group(2).lower()]
        y1 = _infer_year(m1, 1, ref)
        y2 = y1 + 1 if m2 < m1 else y1
        notes.append("year_inferred")
        return (_safe_date(y1, m1, 1),
                _safe_date(y2, m2, _MONTH_LAST_DAY[m2]), notes)

    # bare month name "December"
    key = text.lower().strip().rstrip(".")
    if key in MONTH_NAMES:
        month = MONTH_NAMES[key]
        year = _infer_year(month, 1, ref)
        notes.append("year_inferred")
        return (_safe_date(year, month, 1),
                _safe_date(year, month, _MONTH_LAST_DAY[month]), notes)

    # new crop / harvest
    low = text.lower()
    if "new crop" in low or "harvest" in low or "newcrop" in low:
        canon = commodity or "CORN"
        window = NEW_CROP_WINDOWS.get(canon, (10, 1, 11, 30))
        sm, sd, em, ed = window
        year = _infer_year(sm, sd, ref)
        notes.append("newcrop_window_assumed")
        return (_safe_date(year, sm, sd), _safe_date(year, em, ed), notes)

    notes.append("delivery_unparsed")
    return None, None, notes


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, min(day, _MONTH_LAST_DAY.get(month, 28)))
    except ValueError:
        return None


def parse_last_update(raw: str) -> date | None:
    if not raw:
        return None
    d = _parse_mdy(raw)
    if d:
        return d
    # long form "Thursday, July 02, 2026"
    m = re.search(r"(?i)([a-z]+)\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m and m.group(1).lower() in MONTH_NAMES:
        return _safe_date(int(m.group(3)), MONTH_NAMES[m.group(1).lower()], int(m.group(2)))
    return None


def parse_as_of(text: str) -> date | None:
    """Extract an 'as of' date from a page header string."""
    if not text:
        return None
    m = re.search(
        r"(?i)([a-z]+)\s+(\d{1,2}),?\s+(\d{4})", text
    )
    if m and m.group(1).lower() in MONTH_NAMES:
        return _safe_date(int(m.group(3)), MONTH_NAMES[m.group(1).lower()], int(m.group(2)))
    return _parse_mdy(text)
