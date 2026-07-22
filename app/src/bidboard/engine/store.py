"""SQLite layer - the single database module for the whole app.

WAL mode lets the web UI read while a scan writes; writes stay on the scan
thread (single-writer discipline). Forward-only migrations keyed by the
`schema_version` row in `meta`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .types import BidRow, ExtractionResult, FetchResult, Flag, ScoredLink

SCHEMA_VERSION = 1

_DDL_V1 = """
CREATE TABLE IF NOT EXISTS meta (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS companies (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  homepage_url  TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  archived      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sites (
  id                 INTEGER PRIMARY KEY,
  company_id         INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  url                TEXT NOT NULL UNIQUE,
  label              TEXT,
  enabled            INTEGER NOT NULL DEFAULT 1,
  preferred_tier     INTEGER NOT NULL DEFAULT 1,
  tier_success_count INTEGER NOT NULL DEFAULT 0,
  last_tier_probe_at TEXT,
  vendor             TEXT NOT NULL DEFAULT 'unknown',
  column_roles_json  TEXT,
  robots_state       TEXT NOT NULL DEFAULT 'unknown',
  robots_checked_at  TEXT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now')),
  notes              TEXT
);

CREATE TABLE IF NOT EXISTS robots_cache (
  domain      TEXT PRIMARY KEY,
  body        TEXT,
  fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
  id          INTEGER PRIMARY KEY,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  trigger     TEXT NOT NULL DEFAULT 'manual',
  status      TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS page_scans (
  id                    INTEGER PRIMARY KEY,
  run_id                INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
  site_id               INTEGER NOT NULL REFERENCES sites(id)     ON DELETE CASCADE,
  fetched_at            TEXT,
  tier_used             INTEGER,
  http_status           INTEGER,
  outcome               TEXT NOT NULL,
  as_of_text            TEXT,
  as_of_date            TEXT,
  rows_extracted        INTEGER NOT NULL DEFAULT 0,
  extraction_confidence REAL,
  content_signature     TEXT,
  duration_ms           INTEGER,
  debug_html_path       TEXT
);
CREATE INDEX IF NOT EXISTS idx_page_scans_site ON page_scans(site_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS bids (
  id                INTEGER PRIMARY KEY,
  page_scan_id      INTEGER NOT NULL REFERENCES page_scans(id) ON DELETE CASCADE,
  site_id           INTEGER NOT NULL,
  company_id        INTEGER NOT NULL,
  commodity_raw     TEXT, commodity TEXT,
  location_label    TEXT,
  delivery_raw      TEXT, delivery_start TEXT, delivery_end TEXT, delivery_label TEXT,
  cash_price_raw    TEXT, cash_price REAL,
  basis_raw         TEXT, basis REAL,
  futures_raw       TEXT, futures_price REAL,
  futures_month_raw TEXT, futures_month TEXT,
  change_raw        TEXT, change REAL,
  last_update_raw   TEXT, last_update TEXT,
  row_hash          TEXT NOT NULL,
  value_hash        TEXT NOT NULL,
  confidence        REAL NOT NULL DEFAULT 1.0,
  notes_json        TEXT
);
CREATE INDEX IF NOT EXISTS idx_bids_rowhash ON bids(row_hash, page_scan_id DESC);
CREATE INDEX IF NOT EXISTS idx_bids_scan    ON bids(page_scan_id);
CREATE INDEX IF NOT EXISTS idx_bids_filter  ON bids(company_id, commodity, delivery_start);

CREATE TABLE IF NOT EXISTS flags (
  id         INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  run_id     INTEGER REFERENCES scan_runs(id) ON DELETE CASCADE,
  site_id    INTEGER REFERENCES sites(id)     ON DELETE CASCADE,
  company_id INTEGER,
  code       TEXT NOT NULL,
  severity   TEXT NOT NULL,
  message    TEXT NOT NULL,
  suggestion TEXT NOT NULL DEFAULT '',
  data_json  TEXT,
  resolved   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_flags_open ON flags(resolved, created_at DESC);

CREATE TABLE IF NOT EXISTS discovered_links (
  id            INTEGER PRIMARY KEY,
  site_id       INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  url           TEXT NOT NULL,
  anchor_text   TEXT,
  score         REAL NOT NULL,
  reasons_json  TEXT,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
  status        TEXT NOT NULL DEFAULT 'candidate',
  UNIQUE(site_id, url)
);

CREATE TABLE IF NOT EXISTS resolve_candidates (
  id            INTEGER PRIMARY KEY,
  company_id    INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  url           TEXT NOT NULL,
  title         TEXT,
  snippet       TEXT,
  score         REAL NOT NULL,
  evidence_json TEXT,
  status        TEXT NOT NULL DEFAULT 'candidate',
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS builds (
  id                     INTEGER PRIMARY KEY,
  scan_run_id            INTEGER REFERENCES scan_runs(id) ON DELETE SET NULL,
  created_at             TEXT NOT NULL DEFAULT (datetime('now')),
  settings_snapshot_json TEXT NOT NULL,
  files_json             TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


class Store:
    """All reads and writes go through here. One instance per process;
    a lock serializes writes (waitress threads + the scan worker)."""

    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False, timeout=5.0
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    # ---------------- lifecycle ----------------

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(_DDL_V1)
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            # future: elif int(row["value"]) < SCHEMA_VERSION: forward migrations

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- meta ----------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, value, _now()),
            )

    # ---------------- companies & sites ----------------

    def add_company(self, name: str, homepage_url: str | None = None) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO companies(name, homepage_url) VALUES(?,?)",
                (name.strip(), homepage_url),
            )
            return cur.lastrowid

    def rename_company(self, company_id: int, name: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE companies SET name=? WHERE id=?", (name.strip(), company_id)
            )

    def remove_company(self, company_id: int) -> None:
        """Archive, don't delete - scan history stays browsable."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE companies SET archived=1 WHERE id=?", (company_id,)
            )
            self._conn.execute(
                "UPDATE sites SET enabled=0 WHERE company_id=?", (company_id,)
            )

    def get_company(self, company_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM companies WHERE id=?", (company_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_companies(self, include_archived: bool = False) -> list[dict]:
        q = "SELECT * FROM companies"
        if not include_archived:
            q += " WHERE archived=0"
        q += " ORDER BY name COLLATE NOCASE"
        return [dict(r) for r in self._conn.execute(q).fetchall()]

    def add_site(
        self, company_id: int, url: str, label: str | None = None
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO sites(company_id, url, label) VALUES(?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET enabled=1, company_id=excluded.company_id",
                (company_id, url.strip(), label),
            )
            if cur.lastrowid:
                return cur.lastrowid
            row = self._conn.execute(
                "SELECT id FROM sites WHERE url=?", (url.strip(),)
            ).fetchone()
            return row["id"]

    def repoint_site(self, site_id: int, new_url: str) -> None:
        """Re-point a location page at a fresher URL; extraction memory
        resets because the layout may differ."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sites SET url=?, preferred_tier=1, vendor='unknown', "
                "column_roles_json=NULL, robots_state='unknown', "
                "robots_checked_at=NULL WHERE id=?",
                (new_url.strip(), site_id),
            )

    def set_site_enabled(self, site_id: int, enabled: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sites SET enabled=? WHERE id=?", (int(enabled), site_id)
            )

    def remove_site(self, site_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sites WHERE id=?", (site_id,))

    def get_site(self, site_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sites WHERE id=?", (site_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_sites(
        self, company_id: int | None = None, enabled_only: bool = True
    ) -> list[dict]:
        q = "SELECT * FROM sites WHERE 1=1"
        args: list = []
        if company_id is not None:
            q += " AND company_id=?"
            args.append(company_id)
        if enabled_only:
            q += " AND enabled=1"
        q += " ORDER BY label COLLATE NOCASE, url"
        return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    # ---------------- site memory ----------------

    def update_site_memory(
        self,
        site_id: int,
        *,
        tier: int | None = None,
        vendor: str | None = None,
        column_roles: dict | None = None,
        tier_success: bool = False,
        probed_at: datetime | None = None,
    ) -> None:
        sets, args = [], []
        if tier is not None:
            sets.append("preferred_tier=?")
            args.append(tier)
        if vendor is not None:
            sets.append("vendor=?")
            args.append(vendor)
        if column_roles is not None:
            sets.append("column_roles_json=?")
            args.append(json.dumps(column_roles))
        if tier_success:
            sets.append("tier_success_count=tier_success_count+1")
        if probed_at is not None:
            sets.append("last_tier_probe_at=?")
            args.append(_iso(probed_at))
        if not sets:
            return
        args.append(site_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE sites SET {', '.join(sets)} WHERE id=?", args
            )

    def set_robots_state(self, site_id: int, state: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sites SET robots_state=?, robots_checked_at=? WHERE id=?",
                (state, _now(), site_id),
            )

    def get_robots_cache(self, domain: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM robots_cache WHERE domain=?", (domain,)
        ).fetchone()
        return dict(row) if row else None

    def set_robots_cache(self, domain: str, body: str | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO robots_cache(domain, body, fetched_at) VALUES(?,?,?) "
                "ON CONFLICT(domain) DO UPDATE SET body=excluded.body, "
                "fetched_at=excluded.fetched_at",
                (domain, body, _now()),
            )

    # ---------------- scan runs ----------------

    def begin_run(self, trigger: str = "manual") -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO scan_runs(trigger) VALUES(?)", (trigger,)
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE scan_runs SET status=?, finished_at=? WHERE id=?",
                (status, _now(), run_id),
            )

    def get_run(self, run_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM scan_runs WHERE id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        ]

    def latest_run_id(self) -> int | None:
        row = self._conn.execute(
            "SELECT id FROM scan_runs WHERE status IN ('complete','partial') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None

    # ---------------- page scans & bids ----------------

    def record_page_scan(
        self,
        run_id: int,
        site_id: int,
        fetch: FetchResult | None,
        extraction: ExtractionResult | None,
        outcome: str,
        content_signature: str | None = None,
        debug_html_path: str | None = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO page_scans(run_id, site_id, fetched_at, tier_used, "
                "http_status, outcome, as_of_text, as_of_date, rows_extracted, "
                "extraction_confidence, content_signature, duration_ms, "
                "debug_html_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    site_id,
                    _iso(fetch.fetched_at) if fetch else _now(),
                    fetch.tier_used if fetch else None,
                    fetch.http_status if fetch else None,
                    outcome,
                    extraction.as_of_text if extraction else None,
                    _iso(extraction.as_of_date) if extraction else None,
                    len(extraction.rows) if extraction else 0,
                    extraction.confidence if extraction else None,
                    content_signature,
                    fetch.duration_ms if fetch else None,
                    debug_html_path,
                ),
            )
            return cur.lastrowid

    def save_bids(
        self, page_scan_id: int, site_id: int, company_id: int, rows: list[BidRow]
    ) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO bids(page_scan_id, site_id, company_id, "
                "commodity_raw, commodity, location_label, delivery_raw, "
                "delivery_start, delivery_end, delivery_label, cash_price_raw, "
                "cash_price, basis_raw, basis, futures_raw, futures_price, "
                "futures_month_raw, futures_month, change_raw, change, "
                "last_update_raw, last_update, row_hash, value_hash, "
                "confidence, notes_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        page_scan_id, site_id, company_id,
                        r.commodity_raw, r.commodity, r.location_label,
                        r.delivery_raw, _iso(r.delivery_start), _iso(r.delivery_end),
                        r.delivery_label, r.cash_price_raw, r.cash_price,
                        r.basis_raw, r.basis, r.futures_raw, r.futures_price,
                        r.futures_month_raw, r.futures_month, r.change_raw,
                        r.change, r.last_update_raw, _iso(r.last_update),
                        r.row_hash, r.value_hash, r.confidence,
                        json.dumps(r.notes) if r.notes else None,
                    )
                    for r in rows
                ],
            )

    def query_bids(
        self,
        *,
        run_id: int | None = None,
        company_ids: list[int] | None = None,
        commodities: list[str] | None = None,
        latest_only: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        """Read-side workhorse for the report builder and UI.

        latest_only: keep only the newest value per row_hash (bid identity),
        so re-scans supersede rather than duplicate.
        """
        q = (
            "SELECT b.*, c.name AS company_name, s.label AS site_label, "
            "s.url AS site_url, p.as_of_date AS page_as_of, p.run_id AS run_id "
            "FROM bids b "
            "JOIN page_scans p ON p.id = b.page_scan_id "
            "JOIN companies c ON c.id = b.company_id "
            "JOIN sites s ON s.id = b.site_id WHERE 1=1"
        )
        args: list = []
        if run_id is not None:
            q += " AND p.run_id=?"
            args.append(run_id)
        if company_ids:
            q += f" AND b.company_id IN ({','.join('?' * len(company_ids))})"
            args.extend(company_ids)
        if commodities:
            q += f" AND b.commodity IN ({','.join('?' * len(commodities))})"
            args.extend(commodities)
        if latest_only:
            q += (
                " AND b.id = (SELECT b2.id FROM bids b2 WHERE b2.row_hash = "
                "b.row_hash ORDER BY b2.page_scan_id DESC, b2.id DESC LIMIT 1)"
            )
        q += " ORDER BY c.name, b.commodity, b.delivery_start"
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def bid_history(self, row_hash: str, limit: int = 30) -> list[dict]:
        """Value history for one bid identity - powers trend charts."""
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT b.*, p.as_of_date AS page_as_of, p.fetched_at "
                "FROM bids b JOIN page_scans p ON p.id=b.page_scan_id "
                "WHERE b.row_hash=? ORDER BY b.page_scan_id DESC LIMIT ?",
                (row_hash, limit),
            ).fetchall()
        ]

    def recent_signatures(self, site_id: int, limit: int = 5) -> list[dict]:
        """Newest-first content signatures for staleness checks."""
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT id, content_signature, fetched_at FROM page_scans "
                "WHERE site_id=? AND content_signature IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (site_id, limit),
            ).fetchall()
        ]

    def latest_page_scan(self, site_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM page_scans WHERE site_id=? ORDER BY id DESC LIMIT 1",
            (site_id,),
        ).fetchone()
        return dict(row) if row else None

    def latest_row_hashes(self, site_id: int) -> set[str]:
        """row_hashes from the most recent scan of a site (overlap checks)."""
        row = self._conn.execute(
            "SELECT id FROM page_scans WHERE site_id=? AND rows_extracted > 0 "
            "ORDER BY id DESC LIMIT 1",
            (site_id,),
        ).fetchone()
        if not row:
            return set()
        return {
            r["row_hash"]
            for r in self._conn.execute(
                "SELECT row_hash FROM bids WHERE page_scan_id=?", (row["id"],)
            ).fetchall()
        }

    # ---------------- flags ----------------

    def add_flag(
        self,
        flag: Flag,
        *,
        run_id: int | None = None,
        site_id: int | None = None,
        company_id: int | None = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO flags(run_id, site_id, company_id, code, severity, "
                "message, suggestion, data_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id, site_id, company_id, flag.code, flag.severity,
                    flag.message, flag.suggestion,
                    json.dumps(flag.data) if flag.data else None,
                ),
            )
            return cur.lastrowid

    def open_flags(
        self,
        *,
        company_id: int | None = None,
        run_id: int | None = None,
    ) -> list[dict]:
        q = "SELECT * FROM flags WHERE resolved=0"
        args: list = []
        if company_id is not None:
            q += " AND company_id=?"
            args.append(company_id)
        if run_id is not None:
            q += " AND run_id=?"
            args.append(run_id)
        q += " ORDER BY created_at DESC"
        return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def resolve_flag(self, flag_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE flags SET resolved=1 WHERE id=?", (flag_id,)
            )

    # ---------------- discovered links ----------------

    def upsert_discovered_links(
        self, site_id: int, links: list[ScoredLink]
    ) -> None:
        with self._lock, self._conn:
            for link in links:
                self._conn.execute(
                    "INSERT INTO discovered_links(site_id, url, anchor_text, "
                    "score, reasons_json) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(site_id, url) DO UPDATE SET "
                    "score=excluded.score, anchor_text=excluded.anchor_text, "
                    "reasons_json=excluded.reasons_json, last_seen_at=?",
                    (
                        site_id, link.url, link.anchor_text, link.score,
                        json.dumps(list(link.reasons)), _now(),
                    ),
                )

    def candidate_links(
        self, site_id: int, min_score: float = 3.0
    ) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM discovered_links WHERE site_id=? AND "
                "status='candidate' AND score>=? ORDER BY score DESC",
                (site_id, min_score),
            ).fetchall()
        ]

    def set_link_status(self, link_id: int, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE discovered_links SET status=? WHERE id=?",
                (status, link_id),
            )

    def get_link(self, link_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM discovered_links WHERE id=?", (link_id,)
        ).fetchone()
        return dict(row) if row else None

    # ---------------- builds audit ----------------

    def record_build(
        self, scan_run_id: int | None, settings_snapshot: dict, files: list[dict]
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO builds(scan_run_id, settings_snapshot_json, "
                "files_json) VALUES(?,?,?)",
                (scan_run_id, json.dumps(settings_snapshot), json.dumps(files)),
            )
            return cur.lastrowid

    def list_builds(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM builds ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["files"] = json.loads(d.pop("files_json"))
            d["settings_snapshot"] = json.loads(d.pop("settings_snapshot_json"))
            out.append(d)
        return out
