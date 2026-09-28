"""Pipeline: flag collection, persistence and run-summary accounting.

Regression cover for the Tier-2 escalation paths. Two defects lived here:

  * a Tier-2 flag that was persisted but never returned made ScanRunSummary
    report a page as clean when it had in fact warned;
  * a failed render wrote its flags into ``extraction.flags`` while the
    no-rows branch was iterating that same list, so each flag was stored and
    returned more than once and the count drifted from the store.

The rule these tests pin down: every flag a page produces is collected once,
persisted once, and returned in the same order -- so ``ScanRunSummary.flags``
always equals what is in the store, for a successful render, a failed render
with no rows, and a failed render that kept initial partial rows.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bidboard.engine import pipeline as P
from bidboard.engine.config import EngineConfig
from bidboard.engine.store import Store
from bidboard.engine.types import FetchResult, Flag

THRESHOLDS = {"aging_days": 2, "stale_days": 5, "identical_scans": 3}

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_bushel_bids.html"

# A shell page with no table: no rows extract, so Tier-2 is attempted.
JS_SHELL = (
    "<html><body><div id='root'></div>"
    + "<script>x</script>" * 20
    + "</body></html>"
)

RENDER_TIMEOUT = Flag("RENDER_TIMEOUT", "warn", "The widget was slow.",
                      "It'll retry.")
PLAYWRIGHT_MISSING = Flag("PLAYWRIGHT_MISSING", "error",
                          "Needs the mini-browser.", "Install it in Settings.")
NO_BID_DATA = Flag("NO_BID_DATA", "warn", "No bids on the page.",
                   "Check the URL.")

# A page that yields usable rows from Tier-1 but still trips the escalation
# rule (the challenge marker): the "failed render keeps initial partial rows"
# path only exists when both are true at once.
FIXTURE_WITH_CHALLENGE = (
    FIXTURE.read_text().replace("<body>", "<body><div>just a moment</div>", 1)
)


def _result(url, *, tier=1, html=None, flags=()):
    """A FetchResult; ok is derived from whether html is present."""
    return FetchResult(
        ok=bool(html), url=url, final_url=url, tier_used=tier,
        http_status=200 if html else None, html=html,
        fetched_at=datetime.now(timezone.utc), duration_ms=5,
        flags=list(flags),
    )


class _Fetcher:
    """Tier-1 returns whatever the test says; Tier-2 likewise.

    Both are explicit so the render-success, render-failure and
    no-escalation paths can each be constructed without a network or browser.
    """

    def __init__(self, tier1: str | None = JS_SHELL, tier1_flags=(),
                 tier2: str | None = JS_SHELL, tier2_flags=()):
        self._tier1 = tier1
        self._tier1_flags = list(tier1_flags)
        self._tier2 = tier2
        self._tier2_flags = list(tier2_flags)

    def fetch(self, url, start_tier=1):
        return _result(url, tier=1, html=self._tier1, flags=self._tier1_flags)

    def fetch_tier2(self, url, hints=None):
        return _result(url, tier=2, html=self._tier2, flags=self._tier2_flags)


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


def _returned(outcome):
    return [f.code for f in outcome.flags]


def _persisted(store, run):
    return [f["code"] for f in store.open_flags(run_id=run)]


# ---------------------------------------------------------------- success path

def test_successful_render_flags_reach_the_caller(store, site):
    """A Tier-2 render that succeeds still carries flags; they were being
    dropped on the floor, so the outcome looked clean."""
    fetcher = _Fetcher(tier2=FIXTURE.read_text(), tier2_flags=[RENDER_TIMEOUT])

    run, outcome = _scan(store, site, fetcher)

    assert outcome.rows > 0, "fixture should still extract rows"
    assert _returned(outcome).count("RENDER_TIMEOUT") == 1
    assert _persisted(store, run).count("RENDER_TIMEOUT") == 1


def test_successful_render_counts_agree_with_the_store(store, site):
    """Persisted and returned must agree exactly, in the same order."""
    fetcher = _Fetcher(tier2=FIXTURE.read_text(), tier2_flags=[RENDER_TIMEOUT])

    run, outcome = _scan(store, site, fetcher)

    assert _returned(outcome) == _persisted(store, run)


# ------------------------------------------------------- failed render, no rows

def test_failed_render_flags_reach_the_caller(store, site):
    """The failure path (browser missing / render blew up) already persisted
    its flags but returned none of them."""
    fetcher = _Fetcher(tier2=None, tier2_flags=[PLAYWRIGHT_MISSING])

    run, outcome = _scan(store, site, fetcher)

    assert outcome.rows == 0 and outcome.outcome == "no_data"
    assert _returned(outcome) == ["PLAYWRIGHT_MISSING"]
    assert _persisted(store, run) == ["PLAYWRIGHT_MISSING"]


def test_failed_render_does_not_double_count_tier1_no_data(store, site):
    """Tier-1 read a JS shell and concluded "no bid table"; the render then
    failed. That stale verdict is superseded, not a second warning: the page
    must not be charged twice for one outcome.

    Regression for the reviewed head, which returned
    ['NO_BID_DATA', 'PLAYWRIGHT_MISSING', 'PLAYWRIGHT_MISSING'].
    """
    fetcher = _Fetcher(
        tier1=JS_SHELL, tier1_flags=[PLAYWRIGHT_MISSING],
        tier2=None, tier2_flags=[NO_BID_DATA, PLAYWRIGHT_MISSING],
    )

    run, outcome = _scan(store, site, fetcher)

    returned = _returned(outcome)
    assert returned == ["NO_BID_DATA", "PLAYWRIGHT_MISSING"]
    assert len(returned) == len(set(returned)), "no duplicates returned"
    assert returned == _persisted(store, run)


def test_failed_render_counts_agree_with_the_store(store, site):
    """The no-rows branch iterated the very list it was appending to, so the
    store filled with duplicates. Counts must be equal and each code once."""
    fetcher = _Fetcher(tier1_flags=[PLAYWRIGHT_MISSING], tier2=None,
                       tier2_flags=[NO_BID_DATA, PLAYWRIGHT_MISSING])

    run, outcome = _scan(store, site, fetcher)

    persisted = _persisted(store, run)
    assert len(persisted) == len(set(persisted)), "no duplicates stored"
    assert len(_returned(outcome)) == len(persisted)


# ------------------------------------- failed render, initial partial rows kept

def test_failed_render_keeps_partial_rows_and_counts_once(store, site):
    """Tier-1 extracted usable rows before the failed render. Those rows are
    kept, and the render's own flags are counted exactly once alongside them."""
    fetcher = _Fetcher(tier1=FIXTURE_WITH_CHALLENGE, tier2=None,
                       tier2_flags=[RENDER_TIMEOUT])

    run, outcome = _scan(store, site, fetcher)

    assert outcome.rows > 0, "partial Tier-1 rows are held on to"
    returned = _returned(outcome)
    assert returned.count("RENDER_TIMEOUT") == 1
    assert len(returned) == len(set(returned))
    assert returned == _persisted(store, run)


def test_failed_render_partial_rows_do_not_grow_the_flag_list(store, site):
    """Re-scanning an unchanged failing page must not accumulate flags; the
    list is rebuilt per scan, never extended in place."""
    fetcher = _Fetcher(tier1=FIXTURE_WITH_CHALLENGE, tier2=None,
                       tier2_flags=[RENDER_TIMEOUT])

    first_run, first = _scan(store, site, fetcher)
    second_run, second = _scan(store, site, fetcher)

    assert len(first.flags) == len(second.flags)
    assert _persisted(store, first_run) == _persisted(store, second_run)


# -------------------------------------------------------------- no escalation

def test_no_escalation_means_no_tier2_flags(store, site):
    """A Tier-1 page with a real table must not grow phantom Tier-2 flags."""
    fetcher = _Fetcher(tier1=FIXTURE.read_text(), tier2="<html></html>")

    run, outcome = _scan(store, site, fetcher)

    assert outcome.rows > 0
    assert "RENDER_TIMEOUT" not in _returned(outcome)
    assert _returned(outcome) == _persisted(store, run)


# ----------------------------------------------------- run summary vs the store

def test_scan_all_flag_count_matches_the_store(store, site):
    """ScanRunSummary.flags is what the UI prints; it must equal what was
    actually written for the run."""
    fetcher = _Fetcher(tier2=FIXTURE.read_text(), tier2_flags=[RENDER_TIMEOUT])

    summary = P.scan_all(store, EngineConfig(), THRESHOLDS, progress=None,
                         fetcher=fetcher)

    assert summary.total_rows > 0
    assert summary.flags == len(store.open_flags(run_id=summary.run_id))


def test_scan_all_flag_count_matches_the_store_on_failure(store, site):
    """Same equality on the failed-render path, where duplicates used to
    inflate the summary against the store."""
    fetcher = _Fetcher(tier1_flags=[PLAYWRIGHT_MISSING], tier2=None,
                       tier2_flags=[NO_BID_DATA, PLAYWRIGHT_MISSING])

    summary = P.scan_all(store, EngineConfig(), THRESHOLDS, progress=None,
                         fetcher=fetcher)

    persisted = store.open_flags(run_id=summary.run_id)
    assert summary.flags == len(persisted)
    codes = [f["code"] for f in persisted]
    assert len(codes) == len(set(codes)), "no duplicates stored"
