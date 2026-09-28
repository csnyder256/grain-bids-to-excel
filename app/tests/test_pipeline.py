"""Pipeline: run-summary flag accounting.

Regression cover for the Tier-2 escalation paths. A flag that the pipeline
persists but does not return makes ScanRunSummary report a page as clean
when it in fact warned, so the counts must agree with the store.
"""
from datetime import datetime, timezone

import pytest

from bidboard.engine import pipeline as P
from bidboard.engine.config import EngineConfig
from bidboard.engine.store import Store
from bidboard.engine.types import ExtractionResult, FetchResult, Flag

THRESHOLDS = {"aging_days": 2, "stale_days": 5, "identical_scans": 3}

FIXTURE = (
    __import__("pathlib").Path(__file__).resolve().parent
    / "fixtures" / "sample_bushel_bids.html"
)

# A shell page with no table: vendor says it needs JS, so Tier-2 is attempted.
JS_SHELL = (
    "<html><body><div id='root'></div>"
    + "<script>x</script>" * 20
    + "</body></html>"
)


def _fetch(url, tier=1, status=200, html=JS_SHELL, flags=None):
    return FetchResult(
        ok=True, url=url, final_url=url, tier_used=tier, http_status=status,
        html=html, fetched_at=datetime.now(timezone.utc), duration_ms=5,
        flags=list(flags or []),
    )


class _Fetcher:
    """Tier-1 always returns a JS shell; Tier-2 returns whatever the test says."""

    def __init__(self, tier2: FetchResult):
        self.tier2 = tier2

    def fetch(self, url, start_tier=1):
        return _fetch(url)

    def fetch_tier2(self, url, hints=None):
        return self.tier2


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def site(store):
    cid = store.add_company("Co")
    sid = store.add_site(cid, "https://a.example/bids", "Westfield")
    return store.get_site(sid)


def _scan(store, site, fetcher):
    run = store.begin_run()
    outcome = P.scan_site(store, fetcher, site, run, EngineConfig(), THRESHOLDS)
    store.finish_run(run, "complete")
    return run, outcome


def test_tier2_success_flags_reach_the_caller(store, site):
    """A Tier-2 render that succeeds still carries flags; they were being
    dropped on the floor, so the outcome looked clean."""
    warn = Flag("RENDER_TIMEOUT", "warn", "The widget was slow.", "It'll retry.")
    html = FIXTURE.read_text()
    fetcher = _Fetcher(_fetch(site["url"], tier=2, html=html, flags=[warn]))

    run, outcome = _scan(store, site, fetcher)

    assert outcome.rows > 0, "fixture should still extract rows"
    assert [f.code for f in outcome.flags if f.code == "RENDER_TIMEOUT"] == ["RENDER_TIMEOUT"]
    assert [f["code"] for f in store.open_flags(run_id=run) if f["code"] == "RENDER_TIMEOUT"] == ["RENDER_TIMEOUT"]


def test_tier2_flags_are_counted_exactly_once(store, site):
    """Persisted and returned must agree; a duplicate would inflate the
    summary's flag count, a missing one would hide the warning."""
    warn = Flag("RENDER_TIMEOUT", "warn", "The widget was slow.", "It'll retry.")
    fetcher = _Fetcher(_fetch(site["url"], tier=2, html=FIXTURE.read_text(),
                              flags=[warn]))

    run, outcome = _scan(store, site, fetcher)

    returned = [f.code for f in outcome.flags]
    persisted = [f["code"] for f in store.open_flags(run_id=run)]
    assert returned.count("RENDER_TIMEOUT") == 1
    assert persisted.count("RENDER_TIMEOUT") == 1
    assert returned == persisted


def test_tier2_failure_flags_reach_the_caller(store, site):
    """The failure path (browser missing / render blew up) already persisted
    its flags but returned none of them."""
    err = Flag("PLAYWRIGHT_MISSING", "error", "Needs the mini-browser.",
               "Install it in Settings.")
    fetcher = _Fetcher(_fetch(site["url"], tier=2, html=None, flags=[err]))

    run, outcome = _scan(store, site, fetcher)

    assert outcome.rows == 0 and outcome.outcome == "no_data"
    assert "PLAYWRIGHT_MISSING" in [f.code for f in outcome.flags]
    assert "PLAYWRIGHT_MISSING" in [f["code"] for f in store.open_flags(run_id=run)]


def test_no_escalation_means_no_tier2_flags(store, site):
    """A Tier-1 page with a real table must not grow phantom Tier-2 flags."""
    fetcher = _Fetcher(_fetch(site["url"], tier=2, html="<html></html>"))
    fetcher.fetch = lambda url, start_tier=1: _fetch(
        url, html=FIXTURE.read_text()
    )

    run, outcome = _scan(store, site, fetcher)

    assert outcome.rows > 0
    assert "RENDER_TIMEOUT" not in [f.code for f in outcome.flags]
    assert [f.code for f in outcome.flags] == [f["code"] for f in store.open_flags(run_id=run)]


def test_scan_all_flag_count_matches_the_store(store, site):
    """ScanRunSummary.flags is what the UI prints; it must equal what was
    actually written for the run."""
    warn = Flag("RENDER_TIMEOUT", "warn", "The widget was slow.", "It'll retry.")

    class _All(_Fetcher):
        def fetch(self, url, start_tier=1):
            return _fetch(url)

        def fetch_tier2(self, url, hints=None):
            return _fetch(url, tier=2, html=FIXTURE.read_text(), flags=[warn])

    summary = P.scan_all(store, EngineConfig(), THRESHOLDS, progress=None,
                         fetcher=_All(_fetch(site["url"], tier=2)))

    assert summary.total_rows > 0
    assert summary.flags == len(store.open_flags(run_id=summary.run_id))
