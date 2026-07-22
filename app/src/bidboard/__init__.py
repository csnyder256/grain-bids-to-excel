"""BidBoard - cash bids from every elevator, in one spreadsheet."""
from __future__ import annotations

import logging
import threading
import uuid
from logging.handlers import RotatingFileHandler

from flask import Flask, render_template

from . import paths

__version__ = "1.0.0"

APP_NAME = "BidBoard"
TAGLINE = "Cash bids from every elevator, in one spreadsheet."


def _configure_logging() -> None:
    paths.ensure_runtime_dirs()
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    handler = RotatingFileHandler(
        paths.LOGS_DIR / "server-log.txt",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def create_app(db_path=None, settings_path=None) -> Flask:
    _configure_logging()

    app = Flask(__name__)
    app.config["INSTANCE_TOKEN"] = str(uuid.uuid4())
    app.config["SHUTDOWN_EVENT"] = threading.Event()
    app.config["JSON_SORT_KEYS"] = False
    app.config["SETTINGS_PATH"] = settings_path or paths.SETTINGS_PATH

    from .engine.store import Store

    store = Store(db_path or paths.DB_PATH)
    app.config["STORE"] = store

    # Report builder (M6). Wired lazily so the manager can call it after a
    # scan; falls back to None until the report package is importable.
    def _build_cb(run_id):
        try:
            from .report.builder import build_for_run

            return build_for_run(store, app.config["SETTINGS_PATH"], run_id)
        except Exception:  # pragma: no cover - defensive during bring-up
            logging.getLogger("bidboard").exception("build_cb failed")
            return None

    from .services.scan_manager import ScanManager
    from .services.scheduler import ScanScheduler

    app.config["SCAN_MANAGER"] = ScanManager(
        store, app.config["SETTINGS_PATH"], build_cb=_build_cb
    )

    from . import settings as settings_mod

    scheduler = ScanScheduler(app.config["SCAN_MANAGER"])
    scheduler.apply(settings_mod.load_settings(app.config["SETTINGS_PATH"]))
    app.config["SCHEDULER"] = scheduler

    from .web.companies_api import companies_bp
    from .web.pages import pages_bp
    from .web.results_api import results_bp
    from .web.scans_api import scans_bp
    from .web.settings_api import settings_bp
    from .web.system_api import system_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(companies_bp)
    app.register_blueprint(scans_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(settings_bp)

    @app.context_processor
    def _globals():
        return {
            "app_name": APP_NAME,
            "tagline": TAGLINE,
            "version": __version__,
        }

    @app.errorhandler(404)
    def _not_found(_err):
        return (
            render_template(
                "error.html",
                page_title="Page not found",
                error_heading="That page doesn't exist.",
                error_body=(
                    "The address may have been mistyped. Use the menu on the "
                    "left to get back to familiar ground."
                ),
            ),
            404,
        )

    @app.errorhandler(500)
    def _server_error(err):
        logging.getLogger("bidboard").exception("Unhandled error: %s", err)
        return (
            render_template(
                "error.html",
                page_title="Something went wrong",
                error_heading="Something unexpected happened.",
                error_body=(
                    "Nothing was lost. Try the page again; if this keeps "
                    "happening, close the app window and double-click RUN "
                    "to start fresh."
                ),
            ),
            500,
        )

    return app
