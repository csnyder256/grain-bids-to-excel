"""Results: list past builds, open files/folder, rebuild from stored bids."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from .. import paths

log = logging.getLogger("bidboard.results")

results_bp = Blueprint("results", __name__, url_prefix="/api")


def _store():
    return current_app.config["STORE"]


def _open_path(path: Path) -> tuple[bool, str | None]:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True, None
    except Exception as e:
        log.warning("open failed: %s", e)
        return False, str(e)


def _reveal(path: Path) -> tuple[bool, str | None]:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            return _open_path(path.parent)
        return True, None
    except Exception as e:
        return _open_path(path.parent)


@results_bp.get("/results")
def list_results():
    builds = _store().list_builds(limit=50)
    runs = {r["id"]: r for r in _store().list_runs(limit=100)}
    out = []
    for b in builds:
        run = runs.get(b["scan_run_id"], {})
        out.append({
            "build_id": b["id"],
            "created_at": b["created_at"],
            "run_id": b["scan_run_id"],
            "run_status": run.get("status"),
            "files": b["files"],
        })
    return jsonify(out)


@results_bp.post("/results/open-folder")
def open_folder():
    paths.ensure_runtime_dirs()
    ok, err = _open_path(paths.SPREADSHEETS_DIR)
    if not ok:
        return jsonify({"error": err or "Couldn't open the folder."}), 500
    return jsonify({"ok": True})


@results_bp.post("/results/open-file")
def open_file():
    body = request.get_json(silent=True) or {}
    target = Path(body.get("path", ""))
    # confine to the Spreadsheets folder
    try:
        target.resolve().relative_to(paths.SPREADSHEETS_DIR.resolve())
    except (ValueError, OSError):
        return jsonify({"error": "That file is outside the Spreadsheets folder."}), 400
    if not target.exists():
        return jsonify({"error": "That file was moved or deleted."}), 404
    ok, err = _open_path(target)
    if not ok:
        return jsonify({"error": err or "Couldn't open the file."}), 500
    return jsonify({"ok": True})


@results_bp.post("/results/reveal-file")
def reveal_file():
    body = request.get_json(silent=True) or {}
    target = Path(body.get("path", ""))
    try:
        target.resolve().relative_to(paths.SPREADSHEETS_DIR.resolve())
    except (ValueError, OSError):
        return jsonify({"error": "That file is outside the Spreadsheets folder."}), 400
    ok, err = _reveal(target)
    if not ok:
        return jsonify({"error": err or "Couldn't open the folder."}), 500
    return jsonify({"ok": True})


@results_bp.post("/results/rebuild")
def rebuild():
    """Rebuild the latest run's workbook with current settings."""
    run_id = _store().latest_run_id()
    if run_id is None:
        return jsonify({"error": "There's nothing to rebuild yet - run a scan first."}), 400
    try:
        from ..report.builder import build_for_run

        info = build_for_run(_store(), current_app.config["SETTINGS_PATH"], run_id)
    except Exception as e:
        log.exception("rebuild failed")
        return jsonify({"error": "Rebuild ran into a problem."}), 500
    return jsonify({"ok": True, "workbook": info})
