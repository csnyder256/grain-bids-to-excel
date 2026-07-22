"""Resolver scoring and search-result parsing (offline, synthetic HTML)."""
import base64

from bidboard.engine.resolve import (
    _decode_bing_href,
    _parse_bing,
    _parse_ddg,
    _score_result,
)
from bidboard.engine.probe import normalize_url

DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.prairieridgegrain.example%2Fcashbids&rut=x">
    Cash Bids - Prairie Ridge Grain</a>
  <a class="result__snippet">View current cash bids for corn at all Prairie Ridge locations.</a>
</div>
<div class="result">
  <a class="result__a" href="https://www.facebook.com/prairieridge">Prairie Ridge Grain - Facebook</a>
  <a class="result__snippet">Prairie Ridge Grain is on Facebook.</a>
</div>
</body></html>
"""

def _bing_ck(target: str) -> str:
    packed = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc123&u=a1{packed}"


BING_HTML = f"""
<html><body>
<li class="b_algo">
  <h2><a href="{_bing_ck('https://www.prairieridgegrain.example/')}">Prairie Ridge Grain - Home</a></h2>
  <div class="b_caption"><p>Prairie Ridge Grain, LLC is a grain processing company. Cash bids updated daily.</p></div>
</li>
</body></html>
"""


def test_parse_ddg_decodes_redirect_urls():
    results = _parse_ddg(DDG_HTML)
    assert results[0]["url"] == "https://www.prairieridgegrain.example/cashbids"
    assert "Cash Bids" in results[0]["title"]
    assert len(results) == 2


def test_decode_bing_redirect():
    target = "https://www.prairieridgegrain.example/cashbidssingle-2163"
    assert _decode_bing_href(_bing_ck(target)) == target
    # a plain (non-redirect) bing href passes through
    assert _decode_bing_href("https://example.com/x") == "https://example.com/x"


def test_parse_bing_unwraps_redirects():
    results = _parse_bing(BING_HTML)
    assert results[0]["url"] == "https://www.prairieridgegrain.example/"
    assert "cash bids" in results[0]["snippet"].lower()


def test_scoring_prefers_company_domain_over_social():
    results = _parse_ddg(DDG_HTML)
    company_score, company_evidence = _score_result(results[0], "Prairie Ridge Grain")
    facebook_score, _ = _score_result(results[1], "Prairie Ridge Grain")
    assert company_score >= 7   # domain match + title + bid words + bids path
    assert facebook_score < 0   # blocklisted
    assert any("matches the name" in e for e in company_evidence)


def test_normalize_url():
    assert normalize_url("www.example.com") == "https://www.example.com"
    assert normalize_url("https://example.com/x") == "https://example.com/x"
    assert normalize_url("not a url") is None
    assert normalize_url("") is None
