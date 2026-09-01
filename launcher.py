"""three-rooms.exe — double-click entry point.

Starts the server, waits for it to answer, opens the browser. No terminal,
no pip install, no .env editing: a judge opens the app and is asked for a
name and (optionally) a key.

Build:
    .venv/Scripts/python -m PyInstaller --clean three-rooms.spec

The engine still runs standalone from the CLI — that stays true, and it is
part of the pitch: the model does not need any of this to work.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _bundle_root() -> Path:
    """Where the data files are: the PyInstaller temp dir when frozen, the
    repository when running from source."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _already_running(port: int = 8600) -> bool:
    """Is OUR app already serving on the usual port?

    Double-clicking twice should not start a second, competing server on a
    random port the user never sees — which is exactly what it did. If the
    app is already up, open a browser at it and stop.
    """
    try:
        import urllib.request  # noqa: PLC0415
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/config", timeout=2) as r:
            return b"agent_mode" in r.read(400)
    except Exception:
        return False


def _free_port(preferred: int = 8600) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def _say(*parts) -> None:
    """Frozen console output is block-buffered, so a launcher that prints
    without flushing looks like a window that does nothing."""
    print(*parts, flush=True)


def _open_browser(url: str) -> bool:
    """Open the default browser, from whatever thread we happen to be on.

    `os.startfile` and `webbrowser.open` both go through COM on Windows and
    fail SILENTLY when called from a thread that has not initialised it —
    which is this one. Shelling out to `start` does not, so it goes first.
    """
    import subprocess  # noqa: PLC0415
    if sys.platform == "win32":
        try:
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False,
                             creationflags=0x08000000)   # no extra window
            return True
        except Exception:
            pass
    else:
        try:
            subprocess.Popen(
                ["open" if sys.platform == "darwin" else "xdg-open", url])
            return True
        except Exception:
            pass
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _wait_then_open(port: int) -> None:
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 45
    while time.time() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.4)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                opened = _open_browser(url)
                _say("")
                _say("  " + "=" * 52)
                _say("   THREE-ROOMS is running")
                _say("")
                _say(f"   {url}")
                if not opened:
                    _say("")
                    _say("   Could not open your browser automatically —")
                    _say("   copy the address above into it.")
                _say("")
                _say("   Close this window to stop.")
                _say("  " + "=" * 52)
                _say("")
                return
        time.sleep(0.3)
    _say(f"Server did not start in time. Try {url} manually.")


def main() -> int:
    root = _bundle_root()
    os.chdir(root)
    sys.path.insert(0, str(root))

    # A frozen build ships read-only; keep writable state beside the exe so
    # a judge's database and any runs they trigger are not lost in a temp dir.
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent
        os.environ.setdefault("APP_DB_PATH", str(beside / "three-rooms.sqlite"))
        os.environ.setdefault("APP_RUNS_DIR", str(beside / "outputs"))

    if _already_running():
        url = "http://127.0.0.1:8600"
        _say("Three-Rooms is already running — opening it.")
        _open_browser(url)
        _say("")
        _say(f"   {url}")
        _say("")
        _say("   (Close the ORIGINAL window to stop the app.)")
        time.sleep(4)          # let the message be read before the window goes
        return 0

    port = _free_port()
    os.environ.setdefault("APP_PORT", str(port))
    threading.Thread(target=_wait_then_open, args=(port,), daemon=True).start()

    _say("Three-Rooms — starting, your browser will open in a moment…")
    import uvicorn  # noqa: PLC0415
    from app.server.main import app  # noqa: PLC0415
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
