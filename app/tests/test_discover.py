"""Link discovery: scoring, sibling detection, script-JSON harvesting."""
from bidboard.engine.discover import (
    _registered_domain,
    discover_bid_links,
    score_link,
)

BASE = "https://www.example-grain.com/cashbidssingle-2163"

HTML = """
<html><body>
<nav>
  <a href="/cashbids">Cash Bids</a>
  <a href="/about">About Us</a>
  <a href="/careers">Careers</a>
  <a href="/agronomy/news">Agronomy News</a>
  <a href="https://facebook.com/examplegrain">Facebook</a>
  <a href="/grain/markets">Grain Markets</a>
</nav>
<script>
var nav = {"items":[
  {"u":"/cashbidssingle-2162","n":"Northfield"},
  {"u":"/cashbidssingle-2164","n":"West Burlington"},
  {"u":"/photo-gallery","n":"Photos"}
]};
</script>
</body></html>
"""


def test_cash_bid_anchor_scores_high():
    links = discover_bid_links(HTML, BASE)
    urls = {l.url: l for l in links}
    cashbids = next(l for u, l in urls.items() if u.endswith("/cashbids"))
    assert cashbids.score >= 7  # strong token + anchor text


def test_sibling_pattern_found_in_script_json():
    links = discover_bid_links(HTML, BASE, known_bid_urls={BASE})
    sibling_urls = [l.url for l in links if "cashbidssingle-216" in l.url]
    assert "https://www.example-grain.com/cashbidssingle-2162" in sibling_urls
    assert "https://www.example-grain.com/cashbidssingle-2164" in sibling_urls
    sibling = next(l for l in links if l.url.endswith("-2164"))
    assert sibling.score >= 9  # strong token +4, sibling +5
    assert "sibling of a known bid page" in sibling.reasons


def test_penalty_and_offsite_links_excluded():
    links = discover_bid_links(HTML, BASE)
    urls = [l.url for l in links]
    assert not any("careers" in u for u in urls)
    assert not any("photo-gallery" in u for u in urls)
    assert not any("facebook.com" in u for u in urls)
    assert not any("agronomy" in u for u in urls)


def test_known_urls_not_resurfaced():
    links = discover_bid_links(
        HTML, BASE,
        known_bid_urls={"https://www.example-grain.com/cashbids"},
    )
    assert not any(l.url.endswith("/cashbids") for l in links)


def test_score_link_unit():
    score, reasons = score_link(
        "https://x.com/cashbids", "Today's Cash Bids", set()
    )
    assert score >= 7
    score2, _ = score_link("https://x.com/careers", "Careers", set())
    assert score2 < 0


# ---- registered-domain normalization -------------------------------------
#
# Same-site filtering compares ``_registered_domain`` of the page against
# ``_registered_domain`` of every candidate. The old implementation used
# ``str.lstrip("www.")``, which strips *characters* rather than the prefix, so
# any host whose first label begins with 'w' was read wrong (westfieldgrain.com
# -> estfieldgrain.com, wheat.com -> heat.com). The tests below pin the correct
# behaviour on both sides of the comparison.


def test_registered_domain_only_strips_a_real_www_prefix():
    assert _registered_domain("www.westfieldgrain.com") == "westfieldgrain.com"
    assert _registered_domain("westfieldgrain.com") == "westfieldgrain.com"
    assert _registered_domain("wheat.com") == "wheat.com"
    assert _registered_domain("w.wheat.com") == "wheat.com"
    assert _registered_domain("bids.westfieldgrain.com") == "westfieldgrain.com"
    # a port is dropped, and the two forms still agree
    assert _registered_domain("www.example.com:8080") == "example.com"
    assert _registered_domain("example.com:8080") == "example.com"


W_SUBDOMAIN_HTML = """
<html><body><nav>
  <a href="https://bids.westfieldgrain.com/cashbidssingle-2164">Northfield cash bids</a>
</nav></body></html>
"""


def test_same_site_link_on_a_sibling_subdomain_is_kept():
    """A bid page on ``bids.<host>`` is on the same site and must be found.

    Regression: with the lstrip bug, ``www.westfieldgrain.com`` and
    ``bids.westfieldgrain.com`` reduced to different strings, so this sibling
    page was discarded as off-site and never suggested.
    """
    links = discover_bid_links(
        W_SUBDOMAIN_HTML, "https://www.westfieldgrain.com/cashbidssingle-2163"
    )
    assert [l.url for l in links] == [
        "https://bids.westfieldgrain.com/cashbidssingle-2164"
    ]


LOOKALIKE_HTML = """
<html><body><nav>
  <a href="https://estfieldgrain.com/cashbids">Other elevator cash bids</a>
</nav></body></html>
"""


def test_offsite_host_that_merely_shares_the_stripped_string_is_rejected():
    """The bug also *accepted* an unrelated host whose name reduced to the same
    stripped string as the page's own host. Different registrable domain means
    a different site, so the link must be dropped."""
    links = discover_bid_links(
        LOOKALIKE_HTML, "https://westfieldgrain.com/cashbidssingle-2163"
    )
    assert links == []
