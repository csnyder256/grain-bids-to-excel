#!/bin/bash
# BidBoard (Mac) - double-click to start. Mirrors RUN.bat.
cd "$(dirname "$0")" || exit 1

ROOT="$(pwd)"
PY="$ROOT/app/python-mac/bin/python3.12"

if [ ! -f "$ROOT/app/data/.setup-complete" ]; then
    clear
    echo
    echo "  It looks like Setup hasn't been completed yet."
    echo
    echo "  Please double-click SETUP first, wait for \"SETUP COMPLETE\","
    echo "  then double-click RUN again."
    echo
    read -n 1 -s -r -p "  Press any key to close this window..."
    echo
    exit 1
fi

export PLAYWRIGHT_BROWSERS_PATH="$ROOT/app/browsers"
"$PY" "$ROOT/app/src/launcher.py"
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
    echo
    echo "  BidBoard stopped unexpectedly. This is usually harmless -"
    echo "  just double-click RUN again."
    echo
    echo "  If it keeps happening, email whoever set this up for you the file:"
    echo "    app/data/logs/server-log.txt"
    echo
    read -n 1 -s -r -p "  Press any key to close this window..."
    echo
    exit 1
fi
exit 0
