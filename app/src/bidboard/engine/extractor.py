"""Generic bid-table extractor.

Handles <table>, <ul>/<li> grids (Bushel-style widgets), and div grids by
discovering candidate containers, scoring them for "bid-ness", assigning
column roles (fuzzy headers + value-shape inference), and building
normalized BidRows. Never raises; ambiguity becomes a flag.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from bs4 import BeautifulSoup, Tag

from . import headers as H
from . import normalize as N
from .types import BidRow, ExtractionResult, Flag, VendorHints

ACCEPT_SCORE = 8.0
LOW_SCORE = 4.0

_AS_OF_RE = re.compile(
    r"(?i)(?:cash\s+bids?|prices?|quotes?)\s+(?:as\s+of|for)\s+(.{4,40}?\d{4})"
)


# ============================================================
# Phase A - candidate container discovery
# ============================================================

def _table_grid(table: Tag) -> tuple[list[str] | None, list[list[str]]]:
    """(header_or_None, data_rows) from a <table>."""
    rows = []
    header = None
    trs = table.find_all("tr")
    for i, tr in enumerate(trs):
        cells = tr.find_all(["th", "td"])
        texts = []
        for c in cells:
            span = int(c.get("colspan", 1) or 1)
            text = c.get_text(" ", strip=True)
            texts.extend([text] * span)
        if not texts:
            continue
        is_header = all(c.name == "th" for c in cells) or (
            i == 0 and header is None
            and sum(1 for t in texts if _NUMERIC_ANY.match(t.strip())) < len(texts) * 0.3
        )
        if is_header and header is None:
            header = texts
        else:
            rows.append(texts)
    return header, rows


_NUMERIC_ANY = re.compile(r"^[-+$]?\d")


def _sibling_grids(soup: BeautifulSoup) -> list[tuple[list[str] | None, list[list[str]], Tag]]:
    """Groups of sibling elements sharing tag+class → virtual grids.
    Covers Bushel-style repeated ``ul.sevenColumnsBigFirst`` rows and div grids.
    Returns (header, rows, anchor_element)."""
    out = []
    seen_ids: set[int] = set()

    for parent in soup.find_all(True):
        children = [c for c in parent.find_all(recursive=False) if isinstance(c, Tag)]
        if len(children) < 3:
            continue
        # group by (tag, class signature)
        groups: dict[tuple, list[Tag]] = {}
        for child in children:
            key = (child.name, tuple(child.get("class", [])))
            groups.setdefault(key, []).append(child)
        for key, members in groups.items():
            if len(members) < 2:
                continue
            counts = [
                len([g for g in m.find_all(recursive=False) if isinstance(g, Tag)])
                for m in members
            ]
            counts = [c for c in counts if c]
            if not counts:
                continue
            mode = Counter(counts).most_common(1)[0][0]
            if mode < 3 or mode > 15:
                continue
            rows = []
            for m in members:
                cells = [
                    g.get_text(" ", strip=True)
                    for g in m.find_all(recursive=False) if isinstance(g, Tag)
                ]
                if len(cells) == mode:
                    rows.append(cells)
            if len(rows) < 2:
                continue
            if id(members[0]) in seen_ids:
                continue
            seen_ids.add(id(members[0]))
            # header = first row if it looks non-numeric
            header = None
            if rows and sum(
                1 for t in rows[0] if _NUMERIC_ANY.match(t.strip())
            ) < mode * 0.3:
                header, rows = rows[0], rows[1:]
            if rows:
                out.append((header, rows, members[0]))
    return out


# ============================================================
# Phase B - container scoring
# ============================================================

def _column(rows: list[list[str]], idx: int) -> list[str]:
    return [r[idx] for r in rows if idx < len(r)]


def _frac(cells: list[str], pattern: re.Pattern) -> float:
    non_empty = [c for c in cells if c and c.strip()]
    if not non_empty:
        return 0.0
    return sum(1 for c in non_empty if pattern.match(c.strip())) / len(non_empty)


def score_container(
    header: list[str] | None, rows: list[list[str]], context_text: str
) -> float:
    if not rows:
        return -99
    ncols = max(len(r) for r in rows)
    if ncols < 2:
        return -99

    cols = [_column(rows, i) for i in range(ncols)]
    score = 0.0

    if any(
        _frac(c, H.RE_PRICE) >= 0.6 or _frac(c, H.RE_PRICE_CENTS) >= 0.6
        for c in cols
    ):
        score += 4  # S1 price column
    if any(_frac(c, H.RE_BASIS) >= 0.6 for c in cols):
        score += 4  # S2 basis column
    if any(any(H.RE_EIGHTHS.match(x.strip()) for x in c) for c in cols):
        score += 3  # S3 eighths futures
    if any(_frac(c, H.RE_DATE_RANGE) >= 0.5 or _frac(c, H.RE_MONTHNAME) >= 0.5
           for c in cols):
        score += 3  # S4 delivery
    if any(_frac(c, H.RE_FUT_MONTH) >= 0.5 for c in cols):
        score += 3  # S5 futures month
    if H.RE_COMMODITY.search(context_text):
        score += 3  # S6 commodity context
    if header:
        roles = {H.match_header_role(h)[0] for h in header}
        roles.discard(None)
        if len(roles) >= 2:
            score += 4  # S7 header roles
    if any(_frac(c, H.RE_DATE_MDY) >= 0.6 for c in cols):
        score += 1  # S8 date column
    return score


# ============================================================
# Phase C - column-role assignment
# ============================================================

def assign_roles(
    header: list[str] | None, rows: list[list[str]]
) -> tuple[dict[int, str], list[Flag]]:
    ncols = max(len(r) for r in rows)
    cols = [_column(rows, i) for i in range(ncols)]
    roles: dict[int, str] = {}
    flags: list[Flag] = []
    taken: set[str] = set()

    # step 1: header fuzzy match (skip ambiguous unless shape agrees)
    if header:
        for i, h in enumerate(header):
            if i >= ncols:
                break
            role, score = H.match_header_role(h)
            if role and role not in taken:
                htext = re.sub(r"[^a-z ]", "", (h or "").lower())
                if any(a in htext.split() for a in H.AMBIGUOUS):
                    shape_role, shape_conf = H.infer_column_role_by_shape(cols[i])
                    if shape_role != role and shape_conf >= 0.5:
                        continue
                roles[i] = role
                taken.add(role)

    # step 2: value-shape inference for unassigned columns
    for i in range(ncols):
        if i in roles:
            continue
        role, conf = H.infer_column_role_by_shape(cols[i])
        if role and role not in taken:
            roles[i] = role
            taken.add(role)

    # basis vs change disambiguation
    roles, flags = _disambiguate_basis_change(cols, roles, flags)
    return roles, flags


def _disambiguate_basis_change(
    cols: list[list[str]], roles: dict[int, str], flags: list[Flag]
) -> tuple[dict[int, str], list[Flag]]:
    signed_idx = [
        i for i, c in enumerate(cols)
        if _frac(c, H.RE_BASIS) >= 0.7 or _frac(c, H.RE_CHANGE_8THS) >= 0.6
    ]
    have_basis = "basis" in roles.values()
    have_change = "change" in roles.values()

    if len(signed_idx) < 2 or (have_basis and have_change):
        return roles, flags

    cash_idx = next((i for i, r in roles.items() if r == "cash_price"), None)
    fut_idx = next((i for i, r in roles.items() if r == "futures_price"), None)

    # arithmetic identity: basis == cash - futures (±0.02) on >=70% rows
    if cash_idx is not None and fut_idx is not None:
        best = None
        for idx in signed_idx:
            agree = 0
            total = 0
            for r_cash, r_fut, r_sig in zip(cols[cash_idx], cols[fut_idx], cols[idx]):
                cash, _ = N.parse_price(r_cash)
                fut, _ = N.parse_price(r_fut, "futures")
                sig, _ = N.parse_basis(r_sig)
                if cash is None or fut is None or sig is None:
                    continue
                total += 1
                if abs((cash - fut) - sig) <= 0.02:
                    agree += 1
            if total and agree / total >= 0.7:
                best = idx
                break
        if best is not None:
            roles = {i: r for i, r in roles.items() if r not in ("basis", "change")}
            roles[best] = "basis"
            other = [i for i in signed_idx if i != best]
            if other:
                roles[other[0]] = "change"
            return roles, flags

    # fallback: larger median abs = basis
    def median_abs(cells):
        vals = []
        for c in cells:
            v, _ = N.parse_basis(c)
            if v is not None:
                vals.append(abs(v))
        vals.sort()
        return vals[len(vals) // 2] if vals else 0

    ranked = sorted(signed_idx, key=lambda i: median_abs(cols[i]), reverse=True)
    roles = {i: r for i, r in roles.items() if r not in ("basis", "change")}
    roles[ranked[0]] = "basis"
    roles[ranked[1]] = "change"
    return roles, flags


# ============================================================
# Row building
# ============================================================

def _build_rows(
    rows: list[list[str]],
    roles: dict[int, str],
    commodity_ctx: str | None,
    location: str | None,
    ref_date: date | None,
) -> list[BidRow]:
    out = []
    role_idx = {r: i for i, r in roles.items()}
    for raw_row in rows:
        def cell(role):
            i = role_idx.get(role)
            return raw_row[i].strip() if i is not None and i < len(raw_row) else ""

        commodity_raw = cell("commodity") or (commodity_ctx or "")
        commodity = N.canon_commodity(commodity_raw)
        notes: list[str] = []

        cash, cn = N.parse_price(cell("cash_price"), "cash")
        basis, bn = N.parse_basis(cell("basis"))
        fut, fn = N.parse_price(cell("futures_price"), "futures")
        change = N.parse_change(cell("change"))
        fut_month = N.parse_futures_month(cell("futures_month"), ref_date)
        delivery_raw = cell("delivery_period")
        d_start, d_end, dn = N.parse_delivery(delivery_raw, ref_date, commodity)
        last_up = N.parse_last_update(cell("last_updated"))
        notes += cn + bn + fn + dn

        # skip rows with no numeric signal at all - real bid rows always
        # carry at least one number; this also drops the repeated header
        # rows that widget pages (AgriCharts) emit per location block
        if cash is None and basis is None and fut is None and change is None:
            continue

        confidence = 1.0
        if cash is None and basis is None:
            confidence *= 0.4
        confidence -= 0.1 * len({n for n in notes if n.endswith(("_inferred", "_assumed", "_cents"))})

        # arithmetic bonus
        if cash is not None and basis is not None and fut is not None:
            if abs(cash - (fut + basis)) <= 0.03:
                confidence = min(1.0, confidence + 0.1)

        row = BidRow(
            commodity_raw=commodity_raw,
            commodity=commodity,
            location_label=location,
            delivery_raw=delivery_raw,
            delivery_start=d_start,
            delivery_end=d_end,
            delivery_label=delivery_raw,
            cash_price_raw=cell("cash_price"),
            cash_price=cash,
            basis_raw=cell("basis"),
            basis=basis,
            futures_raw=cell("futures_price"),
            futures_price=fut,
            futures_month_raw=cell("futures_month"),
            futures_month=fut_month,
            change_raw=cell("change"),
            change=change,
            last_update_raw=cell("last_updated"),
            last_update=last_up,
            confidence=max(0.1, round(confidence, 3)),
            notes=notes,
        )
        if commodity is None and commodity_raw:
            row.notes.append("commodity_unrecognized")
        out.append(row)
    return out


def _commodity_context(anchor: Tag) -> str:
    """Nearest preceding heading / ancestor text for commodity naming."""
    node = anchor
    for _ in range(4):
        if node is None:
            break
        # preceding heading sibling
        prev = node.find_previous(["h1", "h2", "h3", "h4", "h5"])
        if prev:
            text = prev.get_text(" ", strip=True)
            if H.RE_COMMODITY.search(text):
                return text
        node = node.parent
    # ancestor with cbCommodity-like class
    node = anchor
    for _ in range(4):
        if node is None or not isinstance(node, Tag):
            break
        classes = " ".join(node.get("class", []))
        if "commodity" in classes.lower():
            h = node.find(["h1", "h2", "h3", "h4", "h5"])
            if h:
                return h.get_text(" ", strip=True)
        node = node.parent
    return ""


# ============================================================
# Public entry
# ============================================================

def extract_bids(
    html: str,
    url: str,
    hints: VendorHints | None = None,
    as_of_hint: date | None = None,
    location: str | None = None,
) -> ExtractionResult:
    soup = BeautifulSoup(html, "lxml")

    # as-of date
    as_of_text, as_of_date = None, as_of_hint
    header_el = soup.select_one("p.fcControlsSectionHeader")
    page_text = soup.get_text(" ", strip=True)
    if header_el:
        as_of_text = header_el.get_text(" ", strip=True)
    else:
        m = _AS_OF_RE.search(page_text)
        if m:
            as_of_text = m.group(0)
    if as_of_text:
        parsed = N.parse_as_of(as_of_text)
        if parsed:
            as_of_date = parsed
    ref_date = as_of_date or date.today()

    # gather candidates: tables + sibling grids
    candidates: list[tuple[list[str] | None, list[list[str]], Tag]] = []
    for table in soup.find_all("table"):
        header, rows = _table_grid(table)
        if rows:
            candidates.append((header, rows, table))
    candidates.extend(_sibling_grids(soup))

    result = ExtractionResult(as_of_text=as_of_text, as_of_date=as_of_date)
    if not candidates:
        result.flags.append(_no_data_flag(url))
        return result

    accepted: list[tuple[float, list[str] | None, list[list[str]], Tag]] = []
    best_score = -99.0
    for header, rows, anchor in candidates:
        ctx = _commodity_context(anchor) + " " + (
            anchor.parent.get_text(" ", strip=True)[:400] if anchor.parent else ""
        )
        score = score_container(header, rows, ctx)
        best_score = max(best_score, score)
        if score >= LOW_SCORE:
            accepted.append((score, header, rows, anchor))

    if not accepted:
        result.container_score = best_score
        result.flags.append(_no_data_flag(url))
        return result

    # drop containers nested inside another accepted one
    anchors = [a for _, _, _, a in accepted]
    filtered = []
    for score, header, rows, anchor in accepted:
        if any(other is not anchor and other in anchor.parents for other in anchors):
            continue
        filtered.append((score, header, rows, anchor))

    all_rows: list[BidRow] = []
    low_conf = False
    top_roles: dict[int, str] = {}
    for score, header, rows, anchor in filtered:
        roles, rflags = assign_roles(header, rows)
        result.flags.extend(rflags)
        if not any(r in roles.values() for r in ("cash_price", "basis")):
            continue
        commodity_ctx_text = _commodity_context(anchor)
        commodity_ctx = None
        cm = H.RE_COMMODITY.search(commodity_ctx_text)
        if cm:
            commodity_ctx = commodity_ctx_text
        built = _build_rows(rows, roles, commodity_ctx, location, ref_date)
        if score < ACCEPT_SCORE:
            low_conf = True
            for r in built:
                r.confidence = min(r.confidence, 0.5)
        all_rows.extend(built)
        if not top_roles:
            top_roles = roles

    result.rows = all_rows
    result.container_score = best_score
    result.column_roles = top_roles
    result.confidence = (
        min(1.0, sum(r.confidence for r in all_rows) / len(all_rows))
        if all_rows else 0.0
    )
    if not all_rows:
        result.flags.append(_partial_columns_flag(url))
    elif low_conf:
        result.flags.append(Flag(
            "LOW_CONFIDENCE_EXTRACTION", "warn",
            f"We found something that might be a bid table on {url}, but "
            "we're not fully sure we read it right.",
            "Please compare a row or two against the live page.",
            {"url": url},
        ))
    return result


def _no_data_flag(url: str) -> Flag:
    return Flag(
        "NO_BID_DATA", "error",
        f"We read {url} but couldn't find any cash-bid table on it.",
        "Check the address points at the actual bids page (not the "
        "homepage). Other pages on this site may be the real bid pages - "
        "see suggestions.",
        {"url": url},
    )


def _partial_columns_flag(url: str) -> Flag:
    return Flag(
        "PARTIAL_COLUMNS", "warn",
        f"We read a table on {url} but couldn't identify the price column "
        "with confidence.",
        "Values were saved as text. Check the export; if the columns are "
        "mislabeled, report this site.",
        {"url": url},
    )
