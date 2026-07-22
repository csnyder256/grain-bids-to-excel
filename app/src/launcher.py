"""BidBoard launcher - the process RUN.bat starts.

Responsibilities, in order:
  1. Single-instance guard (server.lock + /api/ping) - a second double-click
     opens a browser tab at the running instance instead of erroring.
  2. Pick a free port from a candidate list.
  3. Serve the Flask app with waitress in a daemon thread.
  4. Open the user's browser only once the server answers its own ping.
  5. Print a friendly console panel and wait; clean up the lock on exit.

Set BIDBOARD_NO_BROWSER=1 to suppress the browser launch (used by tests).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bidboard import APP_NAME, create_app, paths  # noqa: E402

PORT_CANDIDATES = [8383, 8384, 8385, 8480, 8481, 0]
PING_TIMEOUT_S = 2.0


def _read_lock() -> dict | None:
    try:
        return json.loads(paths.LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_lock(port: int) -> None:
    paths.LOCK_PATH.write_text(
        json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8"
    )


def _remove_lock() -> None:
    try:
        paths.LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _ping(port: int) -> bool:
    """True if a BidBoard instance answers on this port."""
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/ping", timeout=PING_TIMEOUT_S
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload.get("app") == "bidboard"
    except Exception:
        return False


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port() -> int:
    for candidate in PORT_CANDIDATES:
        if candidate == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]
        if _port_is_free(candidate):
            return candidate
    raise RuntimeError("no free port found")


def _open_browser(url: str) -> None:
    if os.environ.get("BIDBOARD_NO_BROWSER") == "1":
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass  # the console panel prints the address as a fallback


def _console_panel(url: str) -> None:
    print()
    print("  -----------------------------------------------")
    print(f"   {APP_NAME} is running.")
    print()
    print("   Your browser should open by itself.")
    print(f"   If it doesn't, go to:  {url}")
    print()
    print("   Keep this window open while you use the app.")
    print('   To stop: click "Quit" inside the app,')
    print("   or simply close this window.")
    print("  -----------------------------------------------")
    print()


def main() -> int:
    paths.ensure_runtime_dirs()

    # --- single-instance guard -------------------------------------
    lock = _read_lock()
    if lock and _ping(lock.get("port", -1)):
        url = f"http://127.0.0.1:{lock['port']}"
        print(f"{APP_NAME} is already running - opening it in your browser.")
        _open_browser(url)
        return 0
    _remove_lock()  # stale lock from a crash, or no lock at all

    # --- start the server ------------------------------------------
    app = create_app()
    port = _pick_port()
    url = f"http://127.0.0.1:{port}"
    _write_lock(port)

    from waitress import serve

    server_thread = threading.Thread(
        target=serve,
        args=(app,),
        kwargs={"host": "127.0.0.1", "port": port, "threads": 8},
        daemon=True,
    )
    server_thread.start()

    # --- wait until it answers, then open the browser ----------------
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _ping(port):
            break
        time.sleep(0.2)
    else:
        _remove_lock()
        print("The app couldn't start its local server. Please try RUN again.")
        return 1

    _open_browser(url)
    _console_panel(url)

    # --- wait for quit ----------------------------------------------
    shutdown = app.config["SHUTDOWN_EVENT"]
    try:
        while not shutdown.wait(timeout=1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        _remove_lock()
    print(f"{APP_NAME} has stopped. You can close this window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
