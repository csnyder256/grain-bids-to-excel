"""Scan control + live progress polling."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

scans_bp = Blueprint("scans", __name__, url_prefix="/api")


def _manager():
    return current_app.config["SCAN_MANAGER"]


@scans_bp.post("/scans")
def start_scan():
    body = request.get_json(silent=True) or {}
    company_ids = body.get("company_ids")
    started = _manager().enqueue_scan(company_ids=company_ids)
    if not started:
        return jsonify({"error": "A scan is already running."}), 409
    return jsonify({"ok": True}), 202


@scans_bp.get("/scans/current")
def current_scan():
    since = request.args.get("since", 0, type=int)
    return jsonify(_manager().snapshot(since=since))


@scans_bp.post("/scans/cancel")
def cancel_scan():
    _manager().cancel()
    return jsonify({"ok": True})
