from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, urlparse
from runtime_paths import (
    resolve_desktop_bin_dir,
    resolve_repo_root,
    resolve_v2_root,
    resolve_v3_root,
)

from config_store import (
    get_account_payload,
    get_runtime_payload,
    get_settings_payload,
    get_trading_yaml_text,
    get_v3_account_payload,
    delete_v3_account_payload,
    get_v3_runtime_payload,
    get_v3_settings_payload,
    save_account_payload,
    save_settings_payload,
    save_v3_account_payload,
    save_v3_settings_payload,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
REPO_ROOT = resolve_repo_root()
BASE_DIR = resolve_v2_root()
V3_BASE_DIR = resolve_v3_root()
RUN_DIR = Path(__file__).resolve().parent / "run"
SERVICE_NAMES = {
    "copytrade": "polymaker-copytrade.service",
    "autorun": "polymaker-autorun.service",
    "v3multi": "copytrade-v3-multi.service",
}
LOCAL_SERVICE_SPECS: Dict[str, Dict[str, Any]] = {}


def _resolve_python_command() -> list[str]:
    override = os.getenv("POLY_LOCAL_PYTHON")
    if override:
        return [override]

    python_bin = shutil.which("python")
    if python_bin:
        return [python_bin]

    py_launcher = shutil.which("py")
    if py_launcher:
        return [py_launcher, "-3"]

    return [sys.executable]


def _resolve_local_service_specs() -> Dict[str, Dict[str, Any]]:
    if LOCAL_SERVICE_SPECS:
        return LOCAL_SERVICE_SPECS

    python_cmd = _resolve_python_command()
    force_source = os.getenv("POLY_FORCE_SOURCE_SERVICES") == "1"
    bin_dir = resolve_desktop_bin_dir()
    copytrade_bin = bin_dir / "copytrade_v2_service.exe" if bin_dir else None
    autorun_bin = bin_dir / "autorun_v2_service.exe" if bin_dir else None
    v3_bin = bin_dir / "copytrade_v3_multi_service.exe" if bin_dir else None

    return {
        "copytrade": {
            "cwd": BASE_DIR / "copytrade",
            "cmd": (
                [str(copytrade_bin)]
                if (not force_source) and copytrade_bin and copytrade_bin.exists()
                else [*python_cmd, "copytrade_run.py", "--config", "copytrade_config.json"]
            ),
            "log": BASE_DIR / "copytrade" / "copytrade_systemd.log",
        },
        "autorun": {
            "cwd": BASE_DIR / "POLYMARKET_MAKER_AUTO",
            "cmd": (
                [str(autorun_bin)]
                if (not force_source) and autorun_bin and autorun_bin.exists()
                else [*python_cmd, "poly_maker_autorun.py", "--no-repl"]
            ),
            "log": BASE_DIR / "POLYMARKET_MAKER_AUTO" / "autorun_systemd.log",
        },
        "v3multi": {
            "cwd": V3_BASE_DIR,
            "cmd": (
                [str(v3_bin)]
                if (not force_source) and v3_bin and v3_bin.exists()
                else [*python_cmd, "copytrade_run.py", "--config", "copytrade_config.json"]
            ),
            "log": V3_BASE_DIR / "logs" / "panel_runtime.log",
        },
    }


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(handler: BaseHTTPRequestHandler, status: int, body: str, content_type: str) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON object expected")
    return payload


def _run_command(*command: str) -> Tuple[bool, str]:
    try:
        proc = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except FileNotFoundError:
        return False, f"command not found: {command[0]}"
    except Exception as exc:
        return False, str(exc)

    output = (proc.stdout or "").strip()
    error = (proc.stderr or "").strip()
    text = "\n".join(part for part in (output, error) if part).strip()
    return proc.returncode == 0, text


def _pid_file(service_key: str) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    return RUN_DIR / f"{service_key}.pid"


def _stop_file(service_key: str) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    return RUN_DIR / f"{service_key}.stop"


def _read_pid(service_key: str) -> int | None:
    path = _pid_file(service_key)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_pid(service_key: str, pid: int) -> None:
    _pid_file(service_key).write_text(str(pid), encoding="utf-8")


def _clear_pid(service_key: str) -> None:
    try:
        _pid_file(service_key).unlink()
    except FileNotFoundError:
        pass


def _clear_stop_file(service_key: str) -> None:
    try:
        _stop_file(service_key).unlink()
    except FileNotFoundError:
        pass


def _pid_exists(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        ok, output = _run_command("tasklist", "/FI", f"PID eq {pid}")
        return ok and str(pid) in output
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _local_service_status() -> Dict[str, Any]:
    services: Dict[str, Any] = {}
    for key in SERVICE_NAMES:
        pid = _read_pid(key)
        active = _pid_exists(pid)
        if pid and not active:
            _clear_pid(key)
        services[key] = {
            "service": key,
            "active": active,
            "raw": "active" if active else "inactive",
            "pid": pid if active else None,
            "mode": "local-process",
        }
    return {
        "supported": True,
        "mode": "local-process",
        "message": "systemctl unavailable, using local process control",
        "services": services,
    }


def _start_local_service(service_key: str) -> Dict[str, Any]:
    spec = _resolve_local_service_specs().get(service_key)
    if not spec:
        return {"ok": False, "message": f"unknown service: {service_key}"}

    current_pid = _read_pid(service_key)
    if _pid_exists(current_pid):
        return {"ok": True, "message": "already running", "pid": current_pid}

    _clear_stop_file(service_key)
    spec["log"].parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(spec["log"], "a", encoding="utf-8")
    creationflags = 0
    kwargs: Dict[str, Any] = {}
    child_env = os.environ.copy()
    child_env["POLY_PANEL_STOP_FILE"] = str(_stop_file(service_key))
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        spec["cmd"],
        cwd=str(spec["cwd"]),
        stdout=log_handle,
        stderr=log_handle,
        env=child_env,
        creationflags=creationflags,
        **kwargs,
    )
    log_handle.close()
    _write_pid(service_key, proc.pid)
    return {"ok": True, "message": "started", "pid": proc.pid}


def _stop_local_service(service_key: str) -> Dict[str, Any]:
    pid = _read_pid(service_key)
    if not _pid_exists(pid):
        _clear_pid(service_key)
        _clear_stop_file(service_key)
        return {"ok": True, "message": "already stopped"}

    stop_path = _stop_file(service_key)
    stop_path.write_text("stop\n", encoding="utf-8")
    for _ in range(100):
        if not _pid_exists(pid):
            _clear_pid(service_key)
            _clear_stop_file(service_key)
            return {"ok": True, "message": "stopped gracefully", "pid": pid}
        time.sleep(0.1)

    try:
        if os.name == "nt":
            ok, output = _run_command("taskkill", "/PID", str(pid), "/T", "/F")
            if not ok:
                return {"ok": False, "message": output or "taskkill failed"}
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

    for _ in range(20):
        if not _pid_exists(pid):
            break
        time.sleep(0.1)

    _clear_pid(service_key)
    _clear_stop_file(service_key)
    return {"ok": True, "message": "stopped", "pid": pid}


def _service_status() -> Dict[str, Any]:
    if shutil.which("systemctl") is None:
        return _local_service_status()

    services: Dict[str, Any] = {}
    for key, service_name in SERVICE_NAMES.items():
        ok, output = _run_command("systemctl", "is-active", service_name)
        services[key] = {
            "service": service_name,
            "active": ok and output.strip() == "active",
            "raw": output.strip() or "unknown",
        }
    return {"supported": True, "services": services}


def _service_action(action: str, service_key: str) -> Dict[str, Any]:
    service_name = SERVICE_NAMES.get(service_key)
    if not service_name:
        return {"ok": False, "message": f"unknown service: {service_key}"}
    if shutil.which("systemctl") is None:
        if action == "start":
            return _start_local_service(service_key)
        if action == "stop":
            return _stop_local_service(service_key)
        if action == "restart":
            stopped = _stop_local_service(service_key)
            if not stopped.get("ok"):
                return stopped
            return _start_local_service(service_key)
        return {"ok": False, "message": f"invalid action: {action}"}
    ok, output = _run_command("systemctl", action, service_name)
    return {"ok": ok, "message": output or ("ok" if ok else "failed"), "service": service_name}


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "PolymarketPanel/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/account":
            _json_response(self, HTTPStatus.OK, {"account": get_account_payload()})
            return
        if path == "/api/settings":
            _json_response(self, HTTPStatus.OK, {"settings": get_settings_payload()})
            return
        if path == "/api/runtime":
            payload = get_runtime_payload()
            payload["services"] = _service_status()
            _json_response(self, HTTPStatus.OK, payload)
            return
        if path == "/api/v3/settings":
            _json_response(self, HTTPStatus.OK, {"settings": get_v3_settings_payload()})
            return
        if path == "/api/v3/account":
            raw_index = str(query.get("index", ["0"])[0] or "0")
            _json_response(self, HTTPStatus.OK, {"account": get_v3_account_payload(int(raw_index))})
            return
        if path == "/api/v3/runtime":
            payload = get_v3_runtime_payload()
            payload["services"] = _service_status()
            _json_response(self, HTTPStatus.OK, payload)
            return
        if path == "/api/trading-yaml":
            _text_response(self, HTTPStatus.OK, get_trading_yaml_text(), "text/plain")
            return
        if path == "/" or path == "/index.html":
            _text_response(
                self,
                HTTPStatus.OK,
                (STATIC_DIR / "index.html").read_text(encoding="utf-8"),
                "text/html",
            )
            return
        if path == "/app.js":
            _text_response(
                self,
                HTTPStatus.OK,
                (STATIC_DIR / "app.js").read_text(encoding="utf-8"),
                "application/javascript",
            )
            return
        if path == "/styles.css":
            _text_response(
                self,
                HTTPStatus.OK,
                (STATIC_DIR / "styles.css").read_text(encoding="utf-8"),
                "text/css",
            )
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/account":
                payload = _read_json_body(self)
                _json_response(self, HTTPStatus.OK, {"account": save_account_payload(payload)})
                return
            if path == "/api/settings":
                payload = _read_json_body(self)
                _json_response(self, HTTPStatus.OK, {"settings": save_settings_payload(payload)})
                return
            if path == "/api/v3/settings":
                payload = _read_json_body(self)
                _json_response(self, HTTPStatus.OK, {"settings": save_v3_settings_payload(payload)})
                return
            if path == "/api/v3/account":
                raw_index = str(query.get("index", ["0"])[0] or "0")
                payload = _read_json_body(self)
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"account": save_v3_account_payload(int(raw_index), payload)},
                )
                return
            if path == "/api/v3/account/delete":
                raw_index = str(query.get("index", ["0"])[0] or "0")
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {"settings": delete_v3_account_payload(int(raw_index))},
                )
                return
            if path == "/api/service":
                service_key = str(query.get("name", [""])[0])
                action = str(query.get("action", [""])[0])
                if action not in {"start", "stop", "restart"}:
                    _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid action"})
                    return
                _json_response(self, HTTPStatus.OK, _service_action(action, service_key))
                return
        except Exception as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})


def create_http_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), PanelHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket local control panel")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    args = parser.parse_args()

    server = create_http_server(args.host, args.port)
    print(f"[PANEL] listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
