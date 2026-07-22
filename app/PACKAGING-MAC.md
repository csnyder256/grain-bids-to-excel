# Assembling the shippable BidBoard-Mac folder

The Mac deliverable is a sibling of the Windows one (`app/PACKAGING.md`), not
a replacement - the same git working tree builds both, but the two zips
never share a Python interpreter and building one never touches the other.
The folder the recipient receives IS the deliverable (zipped). Git tracks
the source; the large binaries below are git-excluded and assembled on disk
(or fetched by the recipient's own Mac at setup time).

## What ships (top level the recipient sees)
- `SETUP.command`, `RUN.command`, `read-me-first-mac.html`
- `Spreadsheets/` (created on first run)
- `app/` (the machinery - the README tells the user to ignore it)

Note: no `app/python/` in this zip. That folder is the Windows embeddable
interpreter and belongs only in `BidBoard.zip` (see `PACKAGING.md`) - it is
never assembled into, or shipped inside, `BidBoard-Mac.zip`.

## Architecture decision: fetch at setup time, not pre-bundled
`SETUP.command` downloads the correct-architecture CPython build the first
time it runs, based on `uname -m` (`arm64` -> Apple Silicon, `x86_64` ->
Intel). It is **not** pre-bundled in the zip. Reasons:
1. Mirrors how Windows already handles its own ~400 MB Playwright Chromium
   download - fetching at setup time, not shipping it, is already this
   project's pattern for large platform binaries.
2. macOS needs two different architectures live today (Apple Silicon and
   Intel), and python-build-standalone does not publish a universal2
   `install_only` build (confirmed against upstream release assets).
   Pre-bundling both would roughly double the zip for every recipient, most
   of whom only need one; pre-bundling only one would silently break on the
   other Mac type.
3. Keeps `BidBoard-Mac.zip` small and keeps the repo free of committing a
   second architecture's binaries - the same spirit as `app/python/` being
   git-excluded and assembled on disk for Windows.

## Bundled binary (fetched by the recipient's Mac; git-excluded)
- `app/python-mac/` - CPython, from
  https://github.com/astral-sh/python-build-standalone releases, the
  `install_only` flavor for `aarch64-apple-darwin` or `x86_64-apple-darwin`
  (chosen by `uname -m`), extracted as-is. Pinned release tag and SHA256
  checksums (for both architectures) live directly in `SETUP.command`, so a
  corrupted or tampered download fails loudly instead of silently.
  - Currently pinned: release `20260623`, CPython `3.12.13+20260623`.
  - `app/python-mac/bin/python3.12` (the real binary, not the `python3`
    convenience symlink) is what `SETUP.command`/`RUN.command` run.
- `app/browsers/` - created by SETUP if the user installs the mini-browser
  (Playwright Chromium). Not shipped pre-populated. Same folder name and
  `PLAYWRIGHT_BROWSERS_PATH` convention as Windows.
- `app/data/` - created at first run (SQLite DB, settings.json, logs,
  markers). Same as Windows.

## What SETUP.command does on the recipient's machine (needs internet, no admin, no Homebrew, no Xcode Command Line Tools)
1. Verifies the zip was extracted (bundled files like `app/get-pip.py` are
   present next to SETUP).
2. Checks internet via `curl` (no Python exists yet at this point).
3. Detects the Mac's architecture via `uname -m` and downloads the matching
   python-build-standalone `install_only` tarball, verifies its SHA256
   against the value pinned in the script, and extracts it into
   `app/python-mac/`. Skipped if it's already there (idempotent).
4. Bootstraps pip into that interpreter via bundled `app/get-pip.py`, then
   `pip install -r app/requirements.txt` into
   `app/python-mac/lib/python3.12/site-packages`.
5. Optionally downloads Playwright Chromium into `app/browsers/`.
6. Runs `app/src/selftest.py`; on success writes `app/data/.setup-complete`.

Everything stays inside the folder; nothing touches `/usr/local`,
Homebrew, or any system-wide Python. Re-running SETUP is safe (idempotent - 
it skips the download step if `app/python-mac/bin/python3.12` already exists).

## Gatekeeper (first run only)
macOS quarantines anything downloaded from the internet. The first time the
recipient double-clicks `SETUP.command` (or `RUN.command`), Gatekeeper will
refuse to open it with an "unidentified developer" warning. They must
**right-click (or Control-click) the file and choose "Open"** once - this
shows a dialog with an actual Open button, which a plain double-click does
not. After that one-time step, double-clicking works normally forever.
`read-me-first-mac.html` explains this in the same plain-English tone as
`read-me-first.html`.

## To build the zip
Copy the working tree EXCLUDING `.git/`, `.gitattributes`,
`app/data/`, `app/browsers/`, `app/python/` (Windows-only - never include
it), `app/python-mac/` (fetched by the recipient's own Mac, never shipped),
`Spreadsheets/` contents, all `__pycache__/`, `SETUP.bat`, `RUN.bat`,
`read-me-first.html` (the Windows-only files/README - the Mac zip ships
`SETUP.command`/`RUN.command`/`read-me-first-mac.html` instead), and root
the developer-facing agent onboarding file (not part of the non-technical
recipient's experience - the same reasoning applies to the Windows zip, just
not yet formalized in `PACKAGING.md`).

Sanity check before zipping: the assembled folder must contain
`SETUP.command` and `RUN.command` with their executable bits set and LF line
endings, and must NOT contain an `app/python-mac/` directory (that would mean
a dev-machine SETUP run leaked into the deliverable).

Name the zip so extraction yields one `BidBoard-Mac/` folder.
