"""Companies, sites, name-resolution, probing, and per-company filters."""
from __future__ import annotations

import logging
from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, request

from .. import settings as settings_mod
from ..engine.config import EngineConfig
from ..engine.discover import discover_bid_links
from ..engine.fetcher import TieredFetcher
from ..engine.probe import normalize_url, probe_url
from ..engine.resolve import resolve_business_name

log = logging.getLogger("bidboard.companies")

companies_bp = Blueprint("companies", __name__, url_prefix="/api")


def _store():
    return current_app.config["STORE"]


def _settings() -> dict:
    return settings_mod.load_settings(current_app.config["SETTINGS_PATH"])


def _fetcher() -> TieredFetcher:
    return TieredFetcher(EngineConfig.from_settings(_settings()), _store())


def _company_view(company: dict) -> dict:
    store = _store()
    sites = store.list_sites(company_id=company["id"], enabled_only=False)
    site_views = []
    last_scan_at = None
    for site in sites:
        scan = store.latest_page_scan(site["id"])
        view = {
            "id": site["id"],
            "url": site["url"],
            "label": site["label"],
            "enabled": bool(site["enabled"]),
            "last_scan": None,
        }
        if scan:
            view["last_scan"] = {
                "fetched_at": scan["fetched_at"],
                "outcome": scan["outcome"],
                "rows": scan["rows_extracted"],
                "as_of_date": scan["as_of_date"],
            }
            if scan["fetched_at"] and (
                last_scan_at is None or scan["fetched_at"] > last_scan_at
            ):
                last_scan_at = scan["fetched_at"]
        site_views.append(view)
    return {
        "id": company["id"],
        "name": company["name"],
        "homepage_url": company["homepage_url"],
        "sites": site_views,
        "last_scan_at": last_scan_at,
        "open_flags": len(store.open_flags(company_id=company["id"])),
    }


# ---------------- listing & CRUD ----------------

@companies_bp.get("/companies")
def list_companies():
    return jsonify([_company_view(c) for c in _store().list_companies()])


@companies_bp.post("/companies")
def create_company():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Please give the company a name."}), 400
    homepage = body.get("homepage_url")
    sites = body.get("sites") or []
    store = _store()
    company_id = store.add_company(name, homepage)
    for site in sites:
        url = normalize_url(site.get("url", ""))
        if url:
            store.add_site(company_id, url, site.get("label"))
    log.info("company created: %s (%d sites)", name, len(sites))
    return jsonify(_company_view(store.get_company(company_id))), 201


@companies_bp.patch("/companies/<int:company_id>")
def update_company(company_id: int):
    body = request.get_json(silent=True) or {}
    store = _store()
    if store.get_company(company_id) is None:
        return jsonify({"error": "Company not found."}), 404
    name = (body.get("name") or "").strip()
    if name:
        store.rename_company(company_id, name)
    return jsonify(_company_view(store.get_company(company_id)))


@companies_bp.delete("/companies/<int:company_id>")
def delete_company(company_id: int):
    store = _store()
    if store.get_company(company_id) is None:
        return jsonify({"error": "Company not found."}), 404
    store.remove_company(company_id)
    return jsonify({"ok": True})


# ---------------- name resolution & probing ----------------

@companies_bp.post("/companies/search-business")
def search_business():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()

    # a pasted URL in the name box quietly becomes a probe
    if normalize_url(name) and ("." in name and " " not in name):
        probe = probe_url(_fetcher(), name)
        return jsonify({"mode": "url", "probe": probe, "candidates": [], "flags": []})

    result = resolve_business_name(name, _fetcher(), EngineConfig.from_settings(_settings()))
    return jsonify({
        "mode": "name",
        "candidates": [asdict(c) for c in result.candidates],
        "flags": [asdict(f) for f in result.flags],
    })


@companies_bp.post("/companies/probe-url")
def probe():
    body = request.get_json(silent=True) or {}
    return jsonify(probe_url(_fetcher(), body.get("url", "")))


# ---------------- sites ----------------

@companies_bp.post("/companies/<int:company_id>/sites")
def add_site(company_id: int):
    body = request.get_json(silent=True) or {}
    url = normalize_url(body.get("url", ""))
    if url is None:
        return jsonify({"error": "That doesn't look like a web address."}), 400
    store = _store()
    if store.get_company(company_id) is None:
        return jsonify({"error": "Company not found."}), 404
    site_id = store.add_site(company_id, url, body.get("label"))
    return jsonify({"id": site_id, "url": url}), 201


@companies_bp.post("/sites/<int:site_id>/repoint")
def repoint_site(site_id: int):
    body = request.get_json(silent=True) or {}
    url = normalize_url(body.get("url", ""))
    if url is None:
        return jsonify({"error": "That doesn't look like a web address."}), 400
    store = _store()
    if store.get_site(site_id) is None:
        return jsonify({"error": "Location not found."}), 404
    store.repoint_site(site_id, url)
    return jsonify({"ok": True, "url": url})


@companies_bp.delete("/sites/<int:site_id>")
def remove_site(site_id: int):
    store = _store()
    if store.get_site(site_id) is None:
        return jsonify({"error": "Location not found."}), 404
    store.remove_site(site_id)
    return jsonify({"ok": True})


@companies_bp.post("/sites/<int:site_id>/discover")
def discover_pages(site_id: int):
    """Fetch the site's page fresh and list sibling bid-page candidates."""
    store = _store()
    site = store.get_site(site_id)
    if site is None:
        return jsonify({"error": "Location not found."}), 404
    known = {s["url"] for s in store.list_sites(company_id=site["company_id"], enabled_only=False)}
    fetched = _fetcher().fetch(site["url"])
    if not fetched.ok or not fetched.html:
        message = fetched.flags[0].message if fetched.flags else "We couldn't read the page."
        return jsonify({"error": message}), 502
    links = discover_bid_links(fetched.html, fetched.final_url, known)
    store.upsert_discovered_links(site_id, links)
    return jsonify({
        "candidates": [
            {"id": c["id"], "url": c["url"], "anchor_text": c["anchor_text"],
             "score": c["score"]}
            for c in store.candidate_links(site_id)
        ]
    })


@companies_bp.post("/links/<int:link_id>/accept")
def accept_link(link_id: int):
    store = _store()
    link = store.get_link(link_id)
    if link is None:
        return jsonify({"error": "Suggestion not found."}), 404
    origin = store.get_site(link["site_id"])
    site_id = store.add_site(origin["company_id"], link["url"])
    store.set_link_status(link_id, "accepted")
    return jsonify({"ok": True, "site_id": site_id})


@companies_bp.post("/links/<int:link_id>/dismiss")
def dismiss_link(link_id: int):
    store = _store()
    if store.get_link(link_id) is None:
        return jsonify({"error": "Suggestion not found."}), 404
    store.set_link_status(link_id, "dismissed")
    return jsonify({"ok": True})


# ---------------- per-company filters ("what are we looking for?") ------

@companies_bp.get("/companies/<int:company_id>/filters")
def get_filters(company_id: int):
    doc = _settings()
    override = doc.get("company_overrides", {}).get(str(company_id), {})
    # options the user can pick from = what scans actually saw (plus any
    # already-chosen values, so nothing chosen ever disappears)
    store = _store()
    rows = store.query_bids(company_ids=[company_id], latest_only=True)
    seen_commodities = sorted({r["commodity"] for r in rows if r["commodity"]})
    seen_locations = sorted({r["location_label"] for r in rows if r["location_label"]})
    return jsonify({
        "override": override,
        "options": {
            "commodities": sorted(set(seen_commodities) | set(override.get("commodities", []))),
            "locations": sorted(set(seen_locations) | set(override.get("locations", []))),
        },
        "effective": settings_mod.resolve(doc, company_id),
    })


@companies_bp.put("/companies/<int:company_id>/filters")
def put_filters(company_id: int):
    body = request.get_json(silent=True) or {}
    path = current_app.config["SETTINGS_PATH"]
    doc = settings_mod.load_settings(path)
    try:
        settings_mod.save_settings(
            path, doc, {"company_overrides": {str(company_id): body}}
        )
    except settings_mod.SettingsError as e:
        return jsonify({"error": str(e), "field": e.field}), 400
    return jsonify({"ok": True})
