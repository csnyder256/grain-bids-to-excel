#!/bin/bash
# BidBoard Setup (Mac)
# Mirrors SETUP.bat's steps and tone. See app/PACKAGING-MAC.md for how this
# folder is assembled and why the interpreter is fetched here instead of
# being pre-bundled in the zip.
cd "$(dirname "$0")" || exit 1

LOG="$(pwd)/setup-log.txt"
ROOT="$(pwd)"
PYDIR="$ROOT/app/python-mac"
PY="$PYDIR/bin/python3.12"

PBS_RELEASE="20260623"
PBS_PYVER="3.12.13"
PBS_BASE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}"
SHA_AARCH64="3724aa4dafb5f7b6c2cf98e89914e4248dc6bd2fe40407df4a2d73de99615f16"
SHA_X86_64="7c57fdd1fa675190093700eb0d8e7117e1f9eae7c30a46dea5f8d5266bcfc791"

echo "================ Setup started $(date) ================" >> "$LOG"

fail() {
    # $1 = heading lines already echoed by the caller; just pause and exit.
    echo
    read -n 1 -s -r -p "  Press any key to close this window..."
    echo
    exit 1
}

not_extracted() {
    clear
    echo
    echo "  It looks like this file isn't next to the rest of the BidBoard folder."
    echo
    echo "  Please:"
    echo "    1. Make sure you unzipped BidBoard-Mac (don't run SETUP from inside"
    echo "       the .zip preview)"
    echo "    2. Open the new BidBoard-Mac folder"
    echo "    3. Double-click SETUP there"
    echo
    fail
}

no_internet() {
    echo
    echo "  I couldn't reach the internet. Please check your connection"
    echo "  (or your firewall) and double-click SETUP again. Nothing was"
    echo "  broken - it's safe to retry."
    echo
    fail
}

download_failed() {
    echo
    echo "  Downloading BidBoard's Python couldn't finish. This is usually a"
    echo "  dropped internet connection. Please double-click SETUP again."
    echo "  If it keeps happening, email \"setup-log.txt\" (next to SETUP)"
    echo "  to whoever set this up for you - it tells them exactly what happened."
    echo
    fail
}

checksum_failed() {
    echo
    echo "  The downloaded file didn't match what was expected, so it was"
    echo "  removed. This can happen with a flaky connection. Please"
    echo "  double-click SETUP again."
    echo
    fail
}

pip_failed() {
    echo
    echo "  The installer couldn't finish setting itself up."
    echo "  Please double-click SETUP again. If it still fails, email the"
    echo "  file \"setup-log.txt\" (right next to SETUP) to whoever set this up for you."
    echo
    fail
}

deps_failed() {
    echo
    echo "  Installing the components didn't finish. This is usually a"
    echo "  dropped internet connection. Please double-click SETUP again."
    echo "  If it keeps happening, email \"setup-log.txt\" (next to SETUP)"
    echo "  to whoever set this up for you - it tells them exactly what happened."
    echo
    fail
}

selftest_failed() {
    echo
    echo "  Setup installed everything but the final check didn't pass."
    echo "  Please email the file \"setup-log.txt\" (right next to SETUP)"
    echo "  to whoever set this up for you - it tells them exactly what to fix."
    echo
    fail
}

# ---- Step 0: did they actually extract the zip? -------------------
if [ ! -f "$ROOT/app/get-pip.py" ] || [ ! -f "$ROOT/app/requirements.txt" ]; then
    not_extracted
fi

clear
echo
echo "  ============================================================"
echo "                    BidBoard  -  Setup"
echo "  ============================================================"
echo
echo "  This gets everything ready. It takes about 3 to 6 minutes"
echo "  and needs an internet connection."
echo
echo "  You'll see some technical text scroll by during step 3 -"
echo "  that is completely normal."
echo
echo "  ------------------------------------------------------------"
echo

# ---- Step 1: internet check --------------------------------------
echo "  Step 1 of 6  -  Checking your internet connection..."
if ! curl -fsS --max-time 10 -o /dev/null "https://pypi.org" >> "$LOG" 2>&1; then
    no_internet
fi
echo "                 Connected."
echo

# ---- Step 2: fetch the right BidBoard Python for this Mac --------
echo "  Step 2 of 6  -  Getting BidBoard's Python ready for this Mac..."
if [ -x "$PY" ]; then
    echo "                 Already ready."
else
    ARCH="$(uname -m)"
    case "$ARCH" in
        arm64)
            PBS_FILE="cpython-${PBS_PYVER}+${PBS_RELEASE}-aarch64-apple-darwin-install_only.tar.gz"
            PBS_SHA="$SHA_AARCH64"
            ;;
        x86_64)
            PBS_FILE="cpython-${PBS_PYVER}+${PBS_RELEASE}-x86_64-apple-darwin-install_only.tar.gz"
            PBS_SHA="$SHA_X86_64"
            ;;
        *)
            echo "  This Mac's processor type ($ARCH) isn't one BidBoard recognizes." >> "$LOG"
            download_failed
            ;;
    esac

    TMPFILE="$ROOT/app/.pbs-download.tar.gz"
    rm -f "$TMPFILE"
    if ! curl -fL --max-time 300 -o "$TMPFILE" "$PBS_BASE_URL/$PBS_FILE" >> "$LOG" 2>&1; then
        rm -f "$TMPFILE"
        download_failed
    fi

    ACTUAL_SHA="$(shasum -a 256 "$TMPFILE" | awk '{print $1}')"
    if [ "$ACTUAL_SHA" != "$PBS_SHA" ]; then
        echo "  checksum mismatch: expected $PBS_SHA got $ACTUAL_SHA" >> "$LOG"
        rm -f "$TMPFILE"
        checksum_failed
    fi

    mkdir -p "$PYDIR"
    if ! tar -xzf "$TMPFILE" -C "$PYDIR" --strip-components=1 >> "$LOG" 2>&1; then
        rm -f "$TMPFILE"
        download_failed
    fi
    rm -f "$TMPFILE"
fi
echo "                 Ready."
echo

# ---- Step 3: pip bootstrap + dependencies -------------------------
echo "  Step 3 of 6  -  Installing the app's components..."
echo "                 (This is the longest step - 2 to 4 minutes.)"
echo
if ! "$PY" -m pip --version >/dev/null 2>&1; then
    if ! "$PY" "$ROOT/app/get-pip.py" --no-warn-script-location >> "$LOG" 2>&1; then
        pip_failed
    fi
fi
if ! "$PY" -m pip install --no-warn-script-location -r "$ROOT/app/requirements.txt"; then
    deps_failed
fi
"$PY" -m pip freeze >> "$LOG" 2>&1
echo
echo "                 Components installed."
echo

# ---- Step 4: optional mini-browser ------------------------------
echo "  Step 4 of 6  -  The mini-browser (for JavaScript-based sites)"
echo
echo "                 Some grain websites can only be read with a"
echo "                 built-in mini-browser (a one-time ~400 MB"
echo "                 download). Most people should install it now."
echo
read -t 30 -n 1 -r -p "                 Install it now? (Y/n, defaults to Y in 30s) " ANSWER
echo
if [[ "$ANSWER" =~ ^[Nn]$ ]]; then
    echo "                 Skipped - you can add it later from Settings." >> "$LOG"
    echo "                 Skipped. You can add it later inside the app."
else
    echo "                 Downloading Chromium - you'll see a progress bar."
    export PLAYWRIGHT_BROWSERS_PATH="$ROOT/app/browsers"
    if ! "$PY" -m playwright install chromium; then
        echo "                 The download had a problem - you can retry from"
        echo "                 Settings inside the app later."
    else
        echo "                 Mini-browser installed."
    fi
fi
echo

# ---- Step 5: self-test ------------------------------------------
echo "  Step 5 of 6  -  Checking everything works..."
if ! "$PY" "$ROOT/app/src/selftest.py" >> "$LOG" 2>&1; then
    selftest_failed
fi
echo "                 All checks passed."
echo

# ---- Step 6: done -----------------------------------------------
echo "  Step 6 of 6  -  Finishing up..."
echo "================ Setup completed OK $(date) ================" >> "$LOG"
echo
echo "  ============================================================"
echo "                    SETUP COMPLETE"
echo
echo "    You're ready to go. Double-click RUN to start BidBoard."
echo "    (You only need to run SETUP again if asked.)"
echo "  ============================================================"
echo
read -n 1 -s -r -p "  Press any key to close this window..."
echo
exit 0
