"""ScanManager - one worker thread, one job queue.

Scans and the browser-install job flow through a single lane, so they never
overlap. The UI polls `snapshot(since=cursor)` once a second; the manager
answers from an in-memory event ring buffer, giving stream-like liveness
with none of SSE's connection management.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from ..engine.config import EngineConfig
from ..engine.pipeline import scan_all
from ..engine.types import ProgressEvent
from .. import settings as settings_mod

log = logging.getLogger("bidboard.scan_manager")

RING_SIZE = 2000


@dataclass
class _CompanyState:
    company_id: int
    name: str
    status: str = "waiting"      # waiting | scanning | done | attention
    stage: str = ""
    rows: int = 0
    flags: int = 0


@dataclass
class _RunState:
    run_id: int | None = None
    state: str = "idle"          # idle | running | finished | cancelled
    trigger: str = "manual"
    started_at: float = 0.0
    finished_at: float = 0.0
    total_companies: int = 0
    done_companies: int = 0
    total_rows: int = 0
    status: str = ""             # complete | partial (when finished)
    workbook: dict | None = None
    companies: dict[int, _CompanyState] = field(default_factory=dict)


class ScanManager:
    def __init__(self, store, settings_path, build_cb=None):
        self.store = store
        self.settings_path = settings_path
        self.build_cb = build_cb          # called after a scan to build reports
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._event_id = 0
        self._run = _RunState()
        self._cancel = threading.Event()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    # ---------------- public API ----------------

    def enqueue_scan(self, company_ids=None, trigger="manual") -> bool:
        """Returns False if a scan is already running/queued."""
        with self._lock:
            if self._run.state == "running" or not self._queue.empty():
                return False
        self._queue.put(("scan", {"company_ids": company_ids, "trigger": trigger}))
        return True

    def enqueue_browser_install(self) -> bool:
        with self._lock:
            if self._run.state == "running":
                return False
        self._queue.put(("install_browser", {}))
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def is_running(self) -> bool:
        with self._lock:
            return self._run.state == "running"

    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            run = self._run
            new_events = [e for e in self._events if e["id"] > since]
            return {
                "cursor": self._event_id,
                "run": {
                    "state": run.state,
                    "run_id": run.run_id,
                    "trigger": run.trigger,
                    "total_companies": run.total_companies,
                    "done_companies": run.done_companies,
                    "total_rows": run.total_rows,
                    "status": run.status,
                    "elapsed": int((run.finished_at or time.time()) - run.started_at)
                    if run.started_at else 0,
                    "workbook": run.workbook,
                    "companies": [
                        {"company_id": c.company_id, "name": c.name,
                         "status": c.status, "stage": c.stage, "rows": c.rows,
                         "flags": c.flags}
                        for c in run.companies.values()
                    ],
                },
                "events": new_events,
            }

    # ---------------- worker ----------------

    def _push_event(self, kind: str, message: str, level: str = "info",
                    company_id=None, data=None) -> None:
        with self._lock:
            self._event_id += 1
            self._events.append({
                "id": self._event_id, "kind": kind, "message": message,
                "level": level, "company_id": company_id, "data": data or {},
            })
            if len(self._events) > RING_SIZE:
                self._events = self._events[-RING_SIZE:]

    def _loop(self) -> None:
        while True:
            job, payload = self._queue.get()
            try:
                if job == "scan":
                    self._run_scan(payload)
                elif job == "install_browser":
                    self._install_browser()
            except Exception:
                log.exception("worker job %s failed", job)
            finally:
                self._queue.task_done()

    def _run_scan(self, payload: dict) -> None:
        settings = settings_mod.load_settings(self.settings_path)
        config = EngineConfig.from_settings(settings)
        thresholds = settings["output"]["stale_thresholds"]
        company_ids = payload.get("company_ids")

        companies = (
            [self.store.get_company(c) for c in company_ids]
            if company_ids else self.store.list_companies()
        )
        companies = [c for c in companies if c]

        self._cancel.clear()
        with self._lock:
            self._run = _RunState(
                state="running", trigger=payload.get("trigger", "manual"),
                started_at=time.time(), total_companies=len(companies),
                companies={c["id"]: _CompanyState(c["id"], c["name"])
                           for c in companies},
            )
        self._push_event("run", "Scan started.", "info")

        def progress(ev: ProgressEvent) -> None:
            with self._lock:
                cs = self._run.companies.get(ev.company_id)
                if cs:
                    if ev.stage == "fetch":
                        cs.status = "scanning"
                    cs.stage = ev.message
                    if ev.stage == "done":
                        cs.rows += ev.data.get("rows", 0)
                    if ev.level in ("warn", "error"):
                        cs.flags += 1
            self._push_event(ev.stage, ev.message, ev.level, ev.company_id, ev.data)

        summary = scan_all(
            self.store, config, thresholds,
            company_ids=[c["id"] for c in companies],
            trigger=payload.get("trigger", "manual"),
            progress=progress, cancel=self._cancel,
        )

        # mark company completion states from stored flags
        with self._lock:
            self._run.run_id = summary.run_id
            self._run.total_rows = summary.total_rows
            self._run.done_companies = len(companies)
            for cid, cs in self._run.companies.items():
                cs.status = "attention" if cs.flags else "done"

        workbook_info = None
        if not self._cancel.is_set() and self.build_cb and summary.total_rows:
            self._push_event("build", "Building your spreadsheet…", "info")
            try:
                workbook_info = self.build_cb(summary.run_id)
            except Exception:
                log.exception("report build failed")
                self._push_event("build", "The scan worked, but building the "
                                 "spreadsheet ran into a problem.", "warn")

        with self._lock:
            self._run.state = "cancelled" if self._cancel.is_set() else "finished"
            self._run.status = summary.status
            self._run.finished_at = time.time()
            self._run.workbook = workbook_info
        self._push_event(
            "run",
            "Scan cancelled." if self._cancel.is_set()
            else f"Done - {summary.total_rows} bids from {len(companies)} "
                 f"compan{'y' if len(companies) == 1 else 'ies'}.",
            "info", data={"workbook": workbook_info},
        )

    def _install_browser(self) -> None:
        import os
        import subprocess
        import sys

        from ..paths import BROWSERS_DIR

        self._push_event("install", "Downloading the mini-browser… this is a "
                         "one-time ~400 MB download.", "info")
        env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(BROWSERS_DIR))
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                env=env, capture_output=True, timeout=1800,
            )
            ok = proc.returncode == 0
        except Exception as e:
            log.exception("browser install failed")
            ok = False
        self._push_event(
            "install",
            "Mini-browser installed - sites that need it will now work."
            if ok else "The mini-browser download didn't finish. Check your "
                       "internet connection and try again from Settings.",
            "info" if ok else "warn",
        )
