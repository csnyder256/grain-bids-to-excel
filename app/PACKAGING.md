# Assembling the shippable BidBoard folder

The folder the recipient receives IS the deliverable (zipped). Git tracks the
source; the large binaries below are git-excluded and assembled on disk.

## What ships (top level the recipient sees)
- `SETUP.bat`, `RUN.bat`, `read-me-first.html`
- `Spreadsheets/` (created on first run)
- `app/` (the machinery - the README tells the user to ignore it)

## Bundled binaries (git-excluded; present on disk / in the zip)
- `app/python/` - CPython 3.12.10 **Windows embeddable** (amd64), from
  https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
  extracted as-is, with ONE edit to `app/python/python312._pth`:

  ```
  python312.zip
  .
  Lib\site-packages
  ..\src

  # Enable site so pip-installed packages import.
  import site
  ```

  (Adds `Lib\site-packages` for pip installs and `..\src` so the `bidboard`
  package imports without a wrapper. Uncomments `import site` so site-packages
  are picked up.)
- `app/browsers/` - created by SETUP if the user installs the mini-browser
  (Playwright Chromium). Not shipped pre-populated.
- `app/data/` - created at first run (SQLite DB, settings.json, logs, markers).

## What SETUP.bat does on the recipient's machine (needs internet, no admin)
1. Verifies the zip was extracted (not run from inside the archive).
2. Bootstraps pip into the embeddable python via bundled `app/get-pip.py`.
3. `pip install -r app/requirements.txt` into `app/python/Lib/site-packages`.
4. Optionally downloads Playwright Chromium into `app/browsers/`.
5. Runs `app/src/selftest.py`; on success writes `app/data/.setup-complete`.

Everything stays inside the folder; nothing touches system Python or the
registry. Re-running SETUP is safe (idempotent).

## To build the zip
Copy the working tree EXCLUDING `.git/`, `.claude/`, `.gitattributes`,
`app/data/`, `app/browsers/`, `Spreadsheets/` contents, all `__pycache__/`,
and - important - any `Lib/`, `Scripts/`, or `Include/` inside `app/python/`
(pip creates those; running SETUP or tests on the dev machine can contaminate
the bundled interpreter, and the recipient's SETUP must install into a clean
embeddable). Sanity check before zipping:
`app\python\python.exe -c "import flask"` must FAIL with ModuleNotFoundError.
Keep `app/python/` (clean embeddable + edited `._pth`) and `app/get-pip.py`.
Name the zip so extraction yields one `BidBoard/` folder.
