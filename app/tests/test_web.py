"""Web endpoints: the re-point flow answers a URL clash in plain English.

Regression: pointing a location at a page another location already watches hit
``sites.url``'s UNIQUE constraint, which escaped as an unhandled 500 instead of
the sentence the operator needs. The endpoint now answers 409 with a message
that names the location holding the page.
"""
import pytest
from flask import Flask

from bidboard.engine.store import Store
from bidboard.web.companies_api import companies_bp


@pytest.fixture
def web(tmp_path):
    """The companies blueprint over a real store, without the scan worker or
    scheduler ``create_app`` would also start."""
    store = Store(":memory:")
    app = Flask(__name__)
    app.config["STORE"] = store
    app.config["SETTINGS_PATH"] = str(tmp_path / "settings.json")
    app.register_blueprint(companies_bp)
    yield app.test_client(), store
    store.close()


def test_repoint_onto_a_watched_page_is_a_friendly_conflict(web):
    client, store = web
    cid = store.add_company("Prairie Ridge Grain")
    first = store.add_site(cid, "https://a.example/bids", "Westfield")
    second = store.add_site(cid, "https://b.example/bids", "Northfield")

    resp = client.post(f"/api/sites/{second}/repoint",
                       json={"url": "https://a.example/bids"})

    assert resp.status_code == 409
    error = resp.get_json()["error"]
    assert "Westfield" in error                 # names the location that owns it
    assert "Prairie Ridge Grain" in error
    assert store.get_site(second)["url"] == "https://b.example/bids"
    assert store.get_site(first)["url"] == "https://a.example/bids"


def test_repoint_to_a_fresh_page_still_succeeds(web):
    client, store = web
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")

    resp = client.post(f"/api/sites/{sid}/repoint",
                       json={"url": "https://a.example/new-bids"})

    assert resp.status_code == 200
    assert store.get_site(sid)["url"] == "https://a.example/new-bids"


def test_repoint_to_a_bad_address_is_rejected(web):
    client, store = web
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")

    resp = client.post(f"/api/sites/{sid}/repoint", json={"url": "not a url"})

    assert resp.status_code == 400
    assert store.get_site(sid)["url"] == "https://a.example/bids"
