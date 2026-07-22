"""Read/update the settings document, and browser-install trigger."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from .. import settings as settings_mod

settings_bp = Blueprint("settings", __name__, url_prefix="/api")


def _path():
    return current_app.config["SETTINGS_PATH"]


@settings_bp.get("/settings")
def get_settings():
    return jsonify(settings_mod.load_settings(_path()))


@settings_bp.put("/settings")
def put_settings():
    patch = request.get_json(silent=True) or {}
    current = settings_mod.load_settings(_path())
    try:
        updated = settings_mod.save_settings(_path(), current, patch)
    except settings_mod.SettingsError as e:
        return jsonify({"error": str(e), "field": e.field}), 400
    if "schedule" in patch:
        scheduler = current_app.config.get("SCHEDULER")
        if scheduler:
            scheduler.apply(updated)
    return jsonify(updated)


@settings_bp.post("/system/install-browser")
def install_browser():
    started = current_app.config["SCAN_MANAGER"].enqueue_browser_install()
    if not started:
        return jsonify({"error": "Can't install while a scan is running."}), 409
    return jsonify({"ok": True}), 202


@settings_bp.get("/system/schedule-status")
def schedule_status():
    scheduler = current_app.config.get("SCHEDULER")
    nxt = scheduler.next_run_time() if scheduler else None
    return jsonify({
        "armed": nxt is not None,
        "next_run": nxt.strftime("%A %I:%M %p").replace(" 0", " ") if nxt else None,
    })


@settings_bp.get("/system/browser-status")
def browser_status():
    from ..engine.fetcher import TieredFetcher
    from ..engine.config import EngineConfig

    available = TieredFetcher(EngineConfig()).playwright_available()
    return jsonify({"installed": available})
