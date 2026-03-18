from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import time
import webbrowser

from runtime_paths import resolve_desktop_bin_dir, resolve_repo_root
from server import create_http_server


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_panel_server() -> tuple[object, str]:
    os.environ.setdefault("POLY_APP_ROOT", str(resolve_repo_root()))
    bin_dir = resolve_desktop_bin_dir()
    if not bin_dir and getattr(sys, "frozen", False):
        candidate = os.path.join(os.path.dirname(sys.executable), "bin")
        if os.path.isdir(candidate):
            bin_dir = candidate
    if bin_dir:
        os.environ.setdefault("POLY_DESKTOP_BIN_DIR", str(bin_dir))

    port = _find_free_port()
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
        try:
            import webview  # type: ignore
        except Exception:
            _run_with_browser(url)
            return

        window = webview.create_window(
            "Polymarket Control Panel",
            url,
            width=1480,
            height=960,
            min_size=(1180, 760),
        )
        webview.start()
        _ = window
    finally:
        with contextlib.suppress(Exception):
            server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()


if __name__ == "__main__":
    main()
