"""System endpoints: instance identity (single-instance guard) and quit."""
from __future__ import annotations

import logging
import os
import threading

from flask import Blueprint, current_app, jsonify

log = logging.getLogger("bidboard.system")

system_bp = Blueprint("system", __name__, url_prefix="/api")


@system_bp.get("/ping")
def ping():
    return jsonify(
        {
            "app": "bidboard",
            "token": current_app.config["INSTANCE_TOKEN"],
        }
    )


@system_bp.post("/quit")
def quit_app():
    """Signal shutdown. The response goes out first; a short timer then
    lets the launcher clean up and exit the process."""
    log.info("Quit requested from the UI.")
    shutdown_event: threading.Event = current_app.config["SHUTDOWN_EVENT"]

    def _fire():
        shutdown_event.set()
        # Give the launcher's main thread a moment to clean up the lock
        # file; if it doesn't exit on its own, force the issue.
        threading.Timer(3.0, lambda: os._exit(0)).start()

    threading.Timer(0.5, _fire).start()
    return jsonify({"ok": True})
