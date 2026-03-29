from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
import webbrowser

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
    if bin_dir:
        os.environ.setdefault("POLY_DESKTOP_BIN_DIR", str(bin_dir))

    port = _find_port()
    server = create_http_server("127.0.0.1", port)
    thread = threading.Thread(target=server.serve_forever, name="panel-browser-server", daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def main() -> None:
    server, url = _start_panel_server()
    try:
        webbrowser.open(url)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return
    finally:
        with contextlib.suppress(Exception):
            server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()


if __name__ == "__main__":
    main()
