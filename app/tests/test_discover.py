"""Link discovery: scoring, sibling detection, script-JSON harvesting."""
from bidboard.engine.discover import discover_bid_links, score_link

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
