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


def _runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _run_dir() -> Path:
    path = _runtime_dir() / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _instance_pid_path() -> Path:
    return _run_dir() / "panel_runtime.pid"


def _instance_port_path() -> Path:
    return _run_dir() / "panel_runtime.port"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_existing_instance() -> tuple[int | None, int | None]:
    try:
        pid = int(_read_text(_instance_pid_path()) or "0")
    except ValueError:
        pid = 0
    try:
        port = int(_read_text(_instance_port_path()) or "0")
    except ValueError:
        port = 0
    if _pid_exists(pid) and port > 0:
        return pid, port
    _clear_instance_files()
    return None, None


def _clear_instance_files() -> None:
    for path in (_instance_pid_path(), _instance_port_path()):
        with contextlib.suppress(Exception):
            path.unlink()


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


def _run_with_browser(url: str, server: object) -> None:
    webbrowser.open(url)
    idle_timeout = float(os.getenv("POLY_BROWSER_IDLE_TIMEOUT_SEC") or "45")
    startup_grace = float(os.getenv("POLY_BROWSER_IDLE_GRACE_SEC") or "20")
    try:
        while True:
            time.sleep(1)
            last_request_ts = float(getattr(server, "last_request_ts", time.time()))
            idle_seconds = time.time() - last_request_ts
            uptime_seconds = time.time() - float(getattr(server, "start_ts", time.time()))
            if uptime_seconds >= startup_grace and idle_seconds >= idle_timeout:
                return
    except KeyboardInterrupt:
        return


def main() -> None:
    mode_override = str(os.getenv("POLY_DESKTOP_APP_MODE") or "").strip().lower()
    if mode_override == "browser":
        force_browser = True
    elif mode_override == "desktop":
        force_browser = False
    else:
        force_browser = os.getenv("POLY_DESKTOP_FORCE_BROWSER") == "1"
        if not force_browser:
            exe_names: list[str] = []
            if getattr(sys, "frozen", False):
                exe_names.append(Path(sys.executable).stem.lower())
            if sys.argv:
                exe_names.append(Path(sys.argv[0]).stem.lower())
            force_browser = any("webpanel" in name for name in exe_names)

    existing_pid, existing_port = _read_existing_instance()
    if existing_pid and existing_port:
        if force_browser:
            webbrowser.open(f"http://127.0.0.1:{existing_port}")
        return

    server, url = _start_panel_server()
    setattr(server, "start_ts", time.time())
    _write_text(_instance_pid_path(), str(os.getpid()))
    _write_text(_instance_port_path(), str(getattr(server, "server_port", 0)))
    try:
        if force_browser:
            _run_with_browser(url, server)
            return

        try:
            import webview  # type: ignore
        except Exception:
            _run_with_browser(url, server)
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
            _run_with_browser(url, server)
    finally:
        _clear_instance_files()
        with contextlib.suppress(Exception):
            server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()


if __name__ == "__main__":
    main()
