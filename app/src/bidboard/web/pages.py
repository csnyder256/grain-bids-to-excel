"""Server-rendered pages. Pages render the right structural state
(empty vs populated); live data inside populated states is fetched by the
page's JS from the JSON APIs."""
from __future__ import annotations

from flask import Blueprint, current_app, render_template

pages_bp = Blueprint("pages", __name__)


def _store():
    return current_app.config["STORE"]


@pages_bp.get("/")
def home():
    companies = _store().list_companies()
    guidance = (
        "Welcome! Three easy steps and your first spreadsheet is minutes away."
        if not companies
        else "You're all set up. Run a scan whenever you want fresh numbers."
    )
    return render_template(
        "home.html",
        page_title="Home",
        active_nav="home",
        guidance=guidance,
        companies=companies,
        last_run=_store().latest_run_id(),
    )


@pages_bp.get("/companies")
def companies():
    companies = _store().list_companies()
    guidance = (
        "These are the grain companies BidBoard watches for you. Add one to get started."
        if not companies
        else "Click a company card to choose what to look for, or add another company."
    )
    return render_template(
        "companies.html",
        page_title="Companies",
        active_nav="companies",
        guidance=guidance,
        companies=companies,
    )


@pages_bp.get("/scan")
def scan():
    companies = _store().list_companies()
    site_count = len(_store().list_sites()) if companies else 0
    guidance = (
        "First, add a company so there's something to scan."
        if not companies
        else "One click reads every company's bid pages and builds your spreadsheet."
    )
    return render_template(
        "scan.html",
        page_title="Run a Scan",
        active_nav="scan",
        guidance=guidance,
        companies=companies,
        site_count=site_count,
        scan_state="idle",
    )


@pages_bp.get("/results")
def results():
    runs = _store().list_runs(limit=25)
    return render_template(
        "results.html",
        page_title="Results",
        active_nav="results",
        guidance="Every spreadsheet BidBoard builds shows up here, newest first.",
        runs=runs,
    )


@pages_bp.get("/settings")
def settings():
    return render_template(
        "settings.html",
        page_title="Settings",
        active_nav="settings",
        guidance="Shape your spreadsheets exactly how you like them. Sensible choices are already made for you.",
    )


@pages_bp.get("/goodbye")
def goodbye():
    return render_template("goodbye.html")
