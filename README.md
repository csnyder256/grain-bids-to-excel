# grain-bids-to-excel

**Scrapes grain elevator cash bids from a dozen mismatched websites and hands you one clean Excel workbook.**

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-CPython%203.12%20%2F%203.14-3776AB?style=flat-square&logo=python&logoColor=white)
![Tests](https://img.shields.io/badge/tests-97%20passing-brightgreen?style=flat-square)
![Flask](https://img.shields.io/badge/flask-3.x-000000?style=flat-square&logo=flask&logoColor=white)
![Playwright](https://img.shields.io/badge/playwright-chromium-2EAD33?style=flat-square&logo=playwright&logoColor=white)
![SQLite](https://img.shields.io/badge/storage-SQLite%20WAL-003B57?style=flat-square&logo=sqlite&logoColor=white)
![Excel](https://img.shields.io/badge/output-.xlsx%20via%20openpyxl-217346?style=flat-square&logo=microsoftexcel&logoColor=white)

## The problem

Grain elevators publish "cash bids" (what they will pay today for corn, soybeans, wheat and so on) on their public websites. Every site does it differently. Some are plain HTML tables. Some are third-party widgets that only render after JavaScript runs. Some are lists of `<div>`s pretending to be a table. Column names disagree. Prices show up as `4.23`, as `423-0s`, or as a basis you have to add to a futures price before the number means anything.

Someone whose job depended on that data was visiting each site by hand, reading the tables, and retyping the numbers into a spreadsheet. Every day. It took a long time and it produced typos.

This app does that instead. You add companies once, click "Run a scan", and get a formatted `.xlsx` workbook in a folder called `Spreadsheets`. It is not a platform. It is a small sharp tool built for one non-technical user, and it removed a daily chore.

## What it is

A local Flask app that serves only on `127.0.0.1`. The browser is just the window; nothing is exposed to the network. There is no multi-user support, no login, no cloud deployment, no export format other than Excel, and no LLM anywhere in the extraction path.

It can also run itself: a schedule block exists in settings (off by default, 7:30 every morning when enabled) and the scheduler enqueues through the same single job lane as a manual scan.

## The constraint that shaped everything

The user could not be asked to install Python, or a package manager, or a browser driver. So the deliverable is a folder you zip and email: double-click `SETUP` once, double-click `RUN` every time. Windows and macOS. That constraint drove most of the engineering below.

- `app/PACKAGING.md` documents how the shipping folder is assembled, including the one non-obvious edit to `python312._pth` that adds `Lib\site-packages` and `..\src` and uncomments `import site`. That is what lets the `bidboard` package import with no venv and no system Python.
- The same document carries the sanity check that matters most: before zipping, `app\python\python.exe -c "import flask"` must **fail** with `ModuleNotFoundError`. If it succeeds, a developer run has contaminated the clean embeddable interpreter and the recipient's SETUP will not install correctly.
- On macOS, `SETUP.command` picks the interpreter by `uname -m`, with separate arm64 and x86_64 branches pulling a python-build-standalone tarball and verifying its SHA.
- `read-me-first.html` and `read-me-first-mac.html` are the styled one-pagers the recipient actually reads, including the Windows SmartScreen and macOS Gatekeeper warnings (the mac page walks the right-click then Open step) and what to send back if setup fails.
- `app/src/selftest.py` runs after SETUP and verifies the install, because nobody is going to be there to debug it.

## Why it is technically interesting

**Container scoring, not per-site selectors.** Writing a CSS selector per elevator does not survive the first redesign, and there is no vendor standard. So `engine/extractor.py` discovers candidate containers, scores them, and only then tries to read them. `score_container` awards points across eight independent signals: a price-shaped column (+4), a basis-shaped column (+4), eighths notation (+3), a delivery column (+3), a futures-month column (+3), commodity words in surrounding context (+3), two or more recognizable header roles (+4), a date column (+1). `ACCEPT_SCORE = 8.0`, `LOW_SCORE = 4.0`. A container scoring between the two still produces rows, but they are marked `low_confidence` rather than silently trusted or silently dropped.

**Virtual grids.** Container discovery goes well beyond `<table>`. `_sibling_grids` groups sibling elements by (tag, class signature), takes the modal child count as the column count, and treats the group as a table. That is what lets it read a `ul.sevenColumnsBigFirst` widget or a repeated-div layout that no table parser would find. Column roles come from fuzzy-matching headers against a synonym dictionary (`engine/headers.py`, rapidfuzz); for headerless grids, roles are inferred from the shape of the values.

**Basis vs. change, settled by arithmetic identity.** The most domain-specific piece in the repo. Bid tables often carry two signed-decimal columns that look identical to a parser: `basis` and `day change`. `_disambiguate_basis_change` tests each candidate against the trade identity `basis == cash - futures` within 0.02, and accepts the column that agrees on at least 70% of rows, falling back to larger median absolute value when neither wins. You do not get that from generic scraping. You get it from knowing how a bid sheet works.

**Grain notation.** `COMMODITY_ALIASES` folds the wild variety of labels into 13 canonical symbols (CORN, WHITE_CORN, SOYBEANS, four wheat classes, OATS, SORGHUM, BARLEY, RYE, CANOLA, SUNFLOWERS). `_parse_eighths` decodes the trade's native notation: `415-0s` becomes `$4.15`, `+0-2` becomes `+$0.0025` (two eighths of a cent), signs handled. Two-digit years pivot at `_pivot_year` (`2000+yy` for `yy < 80`, else `1900+yy`), with a separate single-digit rule that snaps to the nearest plausible future decade.

**Tiered fetching, so a browser is a last resort.** Tier 1 is `requests` with realistic headers. `pipeline._needs_escalation` moves to Tier 2 (Playwright Chromium) on exactly three conditions: a vendor fingerprint says the page needs JS and nothing was extracted; or the HTML carries Cloudflare challenge markers; or the container score is below `ACCEPT_SCORE` **and** no rows came out **and** the page looks script-heavy. `engine/detect.py` fingerprints Barchart/AgriCharts, DTN, Bushel and Cloudflare and supplies selector hints, but those hints only accelerate: the generic extractor is always the fallback. Successful tier, vendor and column roles are memorized per site, so the next scan starts in the right place.

**Nothing raises to the UI.** Every failure becomes a `Flag(code, severity, message, suggestion)` with pre-written plain English aimed at a grain merchandiser, not a developer. DNS failure, HTTP 403, robots disallow, unreadable table, low-confidence extraction, arithmetic mismatch: all flags. `pipeline.scan_all` wraps each site in try/except and converts an outright crash into a `SCAN_INTERNAL_ERROR` flag that says the other sites were not affected. One bad site degrades the run to `partial` instead of aborting it.

Two more that are worth a line each. Three hashes give the app a memory: `row_identity_hash` (site, location, commodity, delivery, futures month, with a documented fallback to the raw delivery label so "Spot" and "Nearby" rows do not collide), `value_hash` (the four prices quantized), and `content_signature` (an order-independent hash of every row pair). Those power supersede-instead-of-duplicate, staleness detection against concrete thresholds (`aging_days` 2, `stale_days` 5, `identical_scans` 3), and overlap detection when two pages of the same company report the same bids. And the Excel writer is hardened for someone who will not debug it: atomic temp-then-replace, never overwrite (new timestamped names, collisions get a ` (2)` suffix), `PermissionError` on a file open in Excel falls back to an alternate name rather than losing the workbook, archive rotation into `Archive/YYYY-MM` skips locked files with a friendly warning, and workbooks carry zero formulas and zero external links, proven by a test that opens the saved `.xlsx` as a zip and asserts there is no `xl/externalLinks/` member. Charts default to `"none"`; bar and line charts come from `openpyxl.chart` when you turn them on.

## How it fits together

```
SETUP.bat / SETUP.command      one-time, no admin rights, no system Python
RUN.bat   / RUN.command        every launch
        |
        v
app/src/launcher.py            lock + /api/ping, port from [8383,8384,8385,8480,8481,0],
        |                      waitress on 127.0.0.1 with 8 threads in a daemon thread,
        |                      opens the browser after ping
        v
bidboard/web/       6 blueprints, 35 routes: 6 rendered pages and 29 JSON endpoints
        |           Jinja templates + vanilla CSS/JS, no build step, no node_modules
        v
bidboard/services/  ScanManager: one thread, one queue, 2000-event ring buffer
        |           scheduler.py (APScheduler) enqueues through the same single lane
        v
bidboard/engine/    fetcher  -> detect -> extractor -> headers -> normalize
        |           discover / resolve / probe / staleness / store / pipeline / types
        v
bidboard/report/    builder -> queries -> health -> layout -> charts -> naming -> files
        |
        v
Spreadsheets/*.xlsx           master / per-company / per-commodity workbooks
```

Progress events go into a 2000-entry in-memory ring; the UI polls `GET /api/scans/current?since=<cursor>` once a second. That was chosen over SSE to avoid connection management, and it means refreshing the page mid-scan loses nothing.

Data flow for one scan: `pipeline.scan_all` walks every enabled site, fetches it, fingerprints the vendor, extracts and scores candidate containers, normalizes values into `BidRow`s, stamps identity/value/content hashes and checks for repeats and overlap, persists raw plus normalized rows into SQLite, then `report.builder.build_for_run` reads the rows back through the active filters and writes the workbook. The master workbook opens on a cover sheet named `Summary`.

Layering rules the code actually holds to:

- `bidboard/paths.py` defines the entire on-disk contract in one file (`ROOT_DIR`, `DATA_DIR`, `SPREADSHEETS_DIR`, `LOCK_PATH`, `BROWSERS_DIR`). That is why "nothing installs outside the folder" is literally true.
- `engine/store.py` is the only module that touches SQLite. Eleven tables: `meta`, `companies`, `sites`, `robots_cache`, `scan_runs`, `page_scans`, `bids`, `flags`, `discovered_links`, `resolve_candidates`, `builds`. WAL mode, single-writer discipline, versioned forward-only migrations.
- Preferences live in JSON, records live in SQLite, and the two never mix. `settings.py` validates the whole settings document against a JSON schema on every write, `additionalProperties: false` throughout, plus per-company overrides resolved by `resolve(settings, company_id)`.
- `report/styles.py` is the single theme module: `grep -rn "Font(" app/src --include=*.py` returns hits in `styles.py` and nowhere else. Even the data-dependent status badges go through a `styles.badge(status)` factory rather than an inline `Font()` in a sheet writer.
- Every parsed field keeps its verbatim page text beside it in `engine/types.py`. A parse failure degrades a row to raw-plus-note rather than dropping it.
- `web/results_api.py` resolves any requested path and refuses anything outside `SPREADSHEETS_DIR` with "That file is outside the Spreadsheets folder." A real path-traversal guard, on a local server, because the endpoint shells out to open files.

Adding a company works two ways: paste a bid-page URL directly, or type a business name and let `engine/resolve.py` find candidate websites. That resolver is scrappier than it sounds. Search engines fingerprint Python's TLS handshake and serve empty deflection pages, so each engine gets tried through plain `requests`, then the system `curl` binary (a different TLS fingerprint), then Playwright Chromium if it is available. Bing's base64 `/ck/a` redirect URLs get unwrapped. A domain blocklist strips directory and aggregator noise, candidates are scored with human-readable evidence strings, and the user always confirms the match.

## Quickstart

### For the person it was built for

1. Unzip the folder anywhere.
2. Double-click `SETUP.bat` (Windows) or `SETUP.command` (macOS). Once. Needs internet, takes a few minutes, no admin rights.
3. Double-click `RUN.bat` or `RUN.command`. The browser opens by itself.

That is the entire user-facing procedure.

### For a developer

The public copy does not ship the bundled interpreter or browsers (see [What is not in this repository](#what-is-not-in-this-repository)), so run it against your own Python.

```powershell
py -3 -m pip install -r app/requirements.txt pytest
py -3 -m playwright install chromium          # only needed for Tier-2 JS pages

# run the tests
py -3 -m pytest app/tests

# run the app
py -3 app/src/launcher.py
# serves http://127.0.0.1:8383 (falls back to 8384/8385/8480/8481;
# the chosen port is written into app/data/server.lock as {"port": ..., "pid": ...})
```

Set the environment variable `BIDBOARD_NO_BROWSER=1` to suppress the browser launch for a headless run. In PowerShell that is `$env:BIDBOARD_NO_BROWSER = "1"` before the command. `launcher.py` puts `app/src` on `sys.path` itself, so there is no install step and no wrapper script. See `.env.example` for the only two environment variables the code reads.

Two gotchas worth knowing before you lose ten minutes:

- Jinja caches templates under Waitress. Template edits need a server restart; static CSS and JS are served fresh.
- If you run Playwright from an ad-hoc shell, `PLAYWRIGHT_BROWSERS_PATH` matters. The launchers point it at `app/browsers`; without it, Tier 2 looks somewhere else.

A second `RUN` double-click does not blow up. `launcher.py` reads `app/data/server.lock`, confirms the process with an `/api/ping` handshake, and just opens a browser tab at the already-running server.

## Project layout

```
SETUP.bat / RUN.bat                 Windows launchers (double-clickable, CRLF enforced via .gitattributes)
SETUP.command / RUN.command         macOS equivalents (LF enforced, or the shebang breaks)
read-me-first.html                  end-user documentation, Windows
read-me-first-mac.html              end-user documentation, macOS (covers the Gatekeeper right-click Open)
app/requirements.txt                10 direct runtime dependencies
app/PACKAGING.md                    how the shippable Windows folder is assembled
app/PACKAGING-MAC.md                same for macOS
app/data/SETTINGS-TEMPLATE.md       what the runtime state directory holds
app/data/settings.example.json      a complete, schema-valid settings document
app/src/launcher.py                 process entry point (166 lines)
app/src/selftest.py                 post-setup verification, run by SETUP
app/src/bidboard/
  paths.py                on-disk contract, one file
  settings.py             342 lines: JSON schema, defaults, per-company override resolution
  engine/     13 modules  store.py 675, extractor.py 498, fetcher.py 462, normalize.py 395
  report/      8 modules  layout.py 402, builder.py 262, health.py 184
  web/         6 blueprints, 35 routes
  services/    scan_manager.py, scheduler.py
  templates/   8 Jinja templates
  static/      2 CSS files, 5 JS files, Inter + Fraunces woff2 with OFL.txt
app/tests/    9 test modules + conftest.py, 1,155 lines, 97 tests
```

Module counts exclude `__init__.py`. Measured in this tree: 6,196 lines of application source across 38 Python files, 1,155 lines of tests, 2,151 lines of frontend. No build step, no `package.json`, no CDN.

## Testing

```
py -3 -m pytest app/tests
97 passed
```

Verified on CPython 3.14 with the pinned dependency floors from `app/requirements.txt`. **There is no CI.** No `.github` workflow exists. The tests run locally, and live scans against real sites were verified by hand, separately.

The distribution matters more than the total. `test_normalize.py` alone holds 40 of the 97 tests (price, eighths, basis, change, futures month, delivery window parsing), then `test_settings.py` 11, `test_store.py` 9, `test_staleness.py` 8, `test_extractor.py` 7, `test_report.py` 7, `test_discover.py` 5, `test_resolve.py` 5, `test_scheduler.py` 5. Coverage follows risk: parsing is where this app is wrong if it is wrong.

Two decisions worth calling out:

- **A golden fixture.** `app/tests/fixtures/sample_bushel_bids.html` is a hand-written synthetic page that imitates the *shape* of a server-rendered vendor widget: repeated `ul.sevenColumnsBigFirst` grids inside `div.cbCommodity`, precisely the layout that exercises the virtual-grid path. The test asserts `container_score >= 8`, no flags, exactly 15 rows, all `CORN`, and then pins the anchor row `July 1 - 15` field by field: cash `4.00`, basis `-0.15`, futures price `4.15` (parsed from `415-0s`), change `+0.0025` (parsed from `+0-2`), futures month `2026-09` (from "Sep 26"), delivery end `date(2026, 7, 15)`. The original fixture was a saved copy of a real company's page; it was replaced with invented markup and invented prices for the public repo, and the test still asserts the same behavior.
- **Product invariants pinned as tests.** Report tests round-trip generated workbooks through `openpyxl.load_workbook`, then open the `.xlsx` as a zip to assert no `xl/externalLinks/` members exist. `test_never_overwrites` and `test_company_override_applies` pin the other two rules the recipient would notice immediately if they broke.

Hard rule: no test hits the network.

## Ethics and scraping behavior

Worth stating plainly rather than burying.

`fetcher.check_robots` reads and honors robots.txt, with a persisted cache (`robots_cache_hours` 24). It is deliberately fail-open: an unreachable robots.txt counts as allowed, which is documented in its docstring. Fetching applies a per-domain jittered politeness delay (`politeness_min_s` 2.0, `politeness_max_s` 5.0) with bounded retries (`max_retries` 2, `retry_backoff_s` 5.0 then 15.0).

`advanced.respect_robots` defaults to **true**, both in `settings.example.json` and in the in-code defaults. The toggle is exposed under Advanced settings because some operators have a specific arrangement with a specific site. Turning it off is a decision the operator owns. Workbooks credit every source site, and the tool only reads public pricing pages that the publisher intends humans to read.

## What is not in this repository

The working copy was roughly 700 MB. The publishable source is well under a megabyte. Those figures are approximate. The difference was excluded on purpose, and some of it affects how you run this:

- **The bundled runtimes.** A vendored Windows-embeddable CPython 3.12 and a Playwright Chromium tree, together the large majority of those bytes. Redistributing them under MIT would misstate the license of most of the package, so they are not here. Install with pip and `playwright install chromium` instead.
- **Runtime state.** `app/data/` holds the live SQLite database, `settings.json`, logs and lock files. None of it ships. `settings.example.json` and `SETTINGS-TEMPLATE.md` are checked in instead; the app writes its own defaults on first launch if there is no settings file.
- **Real output.** The `Spreadsheets/` workbooks held real harvested pricing data attributed to a named third-party company. Not republishable.
- **The original test fixture.** A verbatim capture of a real elevator's page, replaced by synthetic markup as described above.
- **Build artifacts.** The packaged zip deliverable is not committed. Ship source, build the zip from `PACKAGING.md`.

The consequence: from a clean clone, use the developer path. `SETUP` expects the interpreter and browser download steps that are not in this tree, so the double-click experience is reconstructed by following `app/PACKAGING.md`.

There are no API keys, tokens, passwords or connection strings anywhere in this project, because it needs none. It reads public pages and writes local files.

## Honest notes

- Extraction is heuristic. A page scoring under `ACCEPT_SCORE` still returns rows, flagged `low_confidence`. That is a deliberate choice over silent success or silent failure, but it means output should be spot-checked when a new site is added.
- Two bids from the same site and commodity whose delivery window *and* futures month both fail to parse can, in principle, collide on the identity hash. `staleness.row_identity_hash` mitigates it by falling back to the raw delivery label, and there is a test for that case, but the general problem is a known residual.
- Search-engine name resolution is inherently fragile. Three fallback transports exist because search engines actively block scripted clients, and it will need maintenance whenever they change tactics. That is why the user always confirms a match instead of the app auto-accepting one.
- Single-user by design. No auth, no concurrent writers, no deployment story beyond `127.0.0.1`.
- No CI. Every "97 passing" claim here is a local run.

## Status

Working and shipped. It was used for its actual purpose. It is not under active development.

## License

MIT. See [LICENSE](LICENSE). Bundled webfonts (Inter, Fraunces) are SIL OFL 1.1 and ship with `OFL.txt` and the reserved-name notice alongside them in `app/src/bidboard/static/fonts/`.

Built by Cade (https://github.com/csnyder256)
