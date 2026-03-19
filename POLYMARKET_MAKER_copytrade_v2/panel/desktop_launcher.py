from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from runtime_paths import resolve_desktop_bin_dir, resolve_repo_root
from server import create_http_server


def _find_port(preferred_port: int = 8787, max_attempts: int = 20) -> int:
    for offset in range(max_attempts):
        port = preferred_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError("no available localhost port found")


def _start_panel_server() -> tuple[object, str]:
    os.environ.setdefault("POLY_APP_ROOT", str(resolve_repo_root()))
    bin_dir = resolve_desktop_bin_dir()
    if not bin_dir and getattr(sys, "frozen", False):
        candidate = os.path.join(os.path.dirname(sys.executable), "bin")
        if os.path.isdir(candidate):
            bin_dir = candidate
    if bin_dir:
        os.environ.setdefault("POLY_DESKTOP_BIN_DIR", str(bin_dir))

    port = _find_port()
    server = create_http_server("127.0.0.1", port)
    thread = threading.Thread(target=server.serve_forever, name="panel-server", daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def _run_with_browser(url: str) -> None:
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return


def main() -> None:
    server, url = _start_panel_server()
    try:
        force_browser = os.getenv("POLY_DESKTOP_FORCE_BROWSER") == "1"
        if not force_browser:
            exe_names: list[str] = []
            if getattr(sys, "frozen", False):
                exe_names.append(Path(sys.executable).stem.lower())
            if sys.argv:
                exe_names.append(Path(sys.argv[0]).stem.lower())
            force_browser = any("webpanel" in name for name in exe_names)
        if force_browser:
            _run_with_browser(url)
            return

        try:
            import webview  # type: ignore
        except Exception:
            _run_with_browser(url)
            return

        try:
            window = webview.create_window(
                "Polymarket Control Panel",
                url,
                width=1480,
                height=960,
                min_size=(1180, 760),
            )
            webview.start()
            _ = window
        except Exception:
            _run_with_browser(url)
    finally:
        with contextlib.suppress(Exception):
            server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()


if __name__ == "__main__":
    main()
