from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, urlparse

from runtime_paths import (
    resolve_desktop_bin_dir,
    resolve_instance_root,
    resolve_repo_root,
    resolve_run_root,
    resolve_source_root,
    resolve_v2_root,
    resolve_v3_root,
)

from config_store import (
    delete_v3_account_payload,
    get_account_payload,
    get_runtime_payload,
    get_settings_payload,
    get_trading_yaml_text,
    get_v3_account_payload,
    get_v3_runtime_payload,
    get_v3_settings_payload,
    save_account_payload,
    save_settings_payload,
    save_v3_account_payload,
    save_v3_settings_payload,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
SOURCE_ROOT = resolve_source_root()
REPO_ROOT = resolve_repo_root()
INSTANCE_ROOT = resolve_instance_root()
RUN_DIR = resolve_run_root() / "panel"

SERVICE_DEFS: Dict[str, Dict[str, str]] = {
    "copytrade": {
        "systemd": "polymaker-copytrade.service",
        "label": "Copytrade V2",
    },
    "autorun": {
        "systemd": "polymaker-autorun.service",
        "label": "Autorun V2",
    },
    "v3multi": {
        "systemd": "copytrade-v3-multi.service",
        "label": "SmartMoney V3 Multi",
    },
}

LOCAL_SERVICE_SPECS: Dict[str, Dict[str, Any]] = {}
SESSION_STORE: Dict[str, Dict[str, Any]] = {}
SESSION_COOKIE_NAME = "poly_panel_session"
SESSION_TTL_SEC = int(str(os.getenv("POLY_SESSION_TTL_SEC") or "43200"))
AUTH_REQUIRED = str(os.getenv("POLY_AUTH_REQUIRED") or "1").strip() != "0"
SESSION_SECRET = str(os.getenv("POLY_SESSION_SECRET") or "").strip()
DEFAULT_AUTH_USERNAME = str(os.getenv("POLY_AUTH_DEFAULT_USERNAME") or "admin").strip() or "admin"
DEFAULT_AUTH_PASSWORD = str(os.getenv("POLY_AUTH_DEFAULT_PASSWORD") or "admin").strip() or "admin"
AUTH_ITERATIONS = int(str(os.getenv("POLY_AUTH_PBKDF2_ITERATIONS") or "390000"))
AUTH_STATE_PATH = INSTANCE_ROOT / "panel" / "auth.json"
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)


def _now() -> float:
    return time.time()


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _auth_default_state() -> Dict[str, Any]:
    return {
        "username": DEFAULT_AUTH_USERNAME,
        "password_hash": "",
        "password_salt": "",
        "password_iterations": AUTH_ITERATIONS,
        "must_change_credentials": True,
        "updated_at": _utc_timestamp(),
    }


def _hash_password(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    ).hex()


def _build_password_record(password: str, iterations: int | None = None) -> Dict[str, Any]:
    actual_iterations = int(iterations or AUTH_ITERATIONS)
    salt = secrets.token_bytes(16)
    return {
        "password_salt": base64.b64encode(salt).decode("ascii"),
        "password_hash": _hash_password(password, salt, actual_iterations),
        "password_iterations": actual_iterations,
    }


def _write_auth_state(payload: Dict[str, Any]) -> Dict[str, Any]:
    AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(AUTH_STATE_PATH.parent),
        suffix=".tmp",
        prefix=".auth_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, AUTH_STATE_PATH)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return payload


def _load_auth_state() -> Dict[str, Any]:
    if AUTH_STATE_PATH.exists():
        try:
            payload = json.loads(AUTH_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    else:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    username = str(payload.get("username") or DEFAULT_AUTH_USERNAME).strip() or DEFAULT_AUTH_USERNAME
    password_hash = str(payload.get("password_hash") or "")
    password_salt = str(payload.get("password_salt") or "")
    iterations = int(payload.get("password_iterations") or AUTH_ITERATIONS)
    must_change = bool(payload.get("must_change_credentials", True))
    updated_at = str(payload.get("updated_at") or _utc_timestamp())

    if not password_hash or not password_salt:
        seeded = _auth_default_state()
        seeded.update(_build_password_record(DEFAULT_AUTH_PASSWORD, iterations))
        return _write_auth_state(seeded)

    normalized = {
        "username": username,
        "password_hash": password_hash,
        "password_salt": password_salt,
        "password_iterations": iterations,
        "must_change_credentials": must_change,
        "updated_at": updated_at,
    }
    return normalized


def _public_auth_state() -> Dict[str, Any]:
    state = _load_auth_state()
    return {
        "username": str(state.get("username") or DEFAULT_AUTH_USERNAME),
        "must_change_credentials": bool(state.get("must_change_credentials", True)),
        "updated_at": str(state.get("updated_at") or ""),
    }


def _verify_auth_credentials(username: str, password: str) -> bool:
    state = _load_auth_state()
    if username != str(state.get("username") or ""):
        return False
    try:
        salt = base64.b64decode(str(state.get("password_salt") or "").encode("ascii"))
    except Exception:
        return False
    expected = str(state.get("password_hash") or "")
    actual = _hash_password(password, salt, int(state.get("password_iterations") or AUTH_ITERATIONS))
    return hmac.compare_digest(actual, expected)


def _update_auth_credentials(username: str, password: str) -> Dict[str, Any]:
    clean_username = str(username or "").strip()
    clean_password = str(password or "")
    if len(clean_username) < 3:
        raise ValueError("username must be at least 3 characters")
    if len(clean_password) < 6:
        raise ValueError("password must be at least 6 characters")

    state = _load_auth_state()
    updated = {
        "username": clean_username,
        "must_change_credentials": False,
        "updated_at": _utc_timestamp(),
    }
    updated.update(_build_password_record(clean_password, int(state.get("password_iterations") or AUTH_ITERATIONS)))
    _write_auth_state(updated)
    SESSION_STORE.clear()
    return _public_auth_state()


def _windows_subprocess_kwargs() -> Dict[str, Any]:
    if os.name != "nt":
        return {}

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": creationflags,
        "startupinfo": startupinfo,
    }


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


def _resolve_service_executable(bin_dir: Path | None, stem: str) -> Path | None:
    if not bin_dir:
        return None

    candidates = (
        bin_dir / f"{stem}.exe",
        bin_dir / stem / f"{stem}.exe",
        bin_dir / f"{stem}.dist" / f"{stem}.exe",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _build_service_spec(
    *,
    packaged_executable: Path | None,
    source_cwd: Path,
    source_cmd: list[str],
    log_path: Path,
    frozen_runtime: bool,
    force_source: bool,
) -> Dict[str, Any]:
    if not force_source and packaged_executable and packaged_executable.exists():
        return {
            "cwd": packaged_executable.parent,
            "cmd": [str(packaged_executable)],
            "log": log_path,
            "mode": "packaged",
        }

    if force_source or not frozen_runtime:
        return {
            "cwd": source_cwd,
            "cmd": source_cmd,
            "log": log_path,
            "mode": "source",
        }

    return {
        "cwd": source_cwd,
        "cmd": None,
        "log": log_path,
        "mode": "missing",
        "error": "packaged service executable is missing",
    }


def _resolve_local_service_specs() -> Dict[str, Dict[str, Any]]:
    if LOCAL_SERVICE_SPECS:
        return LOCAL_SERVICE_SPECS

    python_cmd = _resolve_python_command()
    force_source = os.getenv("POLY_FORCE_SOURCE_SERVICES") == "1"
    frozen_runtime = bool(getattr(sys, "frozen", False))
    bin_dir = resolve_desktop_bin_dir()
    v2_root = resolve_v2_root()
    v3_root = resolve_v3_root()
    copytrade_bin = _resolve_service_executable(bin_dir, "copytrade_v2_service")
    autorun_bin = _resolve_service_executable(bin_dir, "autorun_v2_service")
    v3multi_bin = _resolve_service_executable(bin_dir, "copytrade_v3_multi_service")

    specs = {
        "copytrade": _build_service_spec(
            packaged_executable=copytrade_bin,
            source_cwd=v2_root / "copytrade",
            source_cmd=[*python_cmd, "copytrade_run.py", "--config", "copytrade_config.json"],
            log_path=v2_root / "copytrade" / "copytrade_systemd.log",
            frozen_runtime=frozen_runtime,
            force_source=force_source,
        ),
        "autorun": _build_service_spec(
            packaged_executable=autorun_bin,
            source_cwd=v2_root / "POLYMARKET_MAKER_AUTO",
            source_cmd=[*python_cmd, "poly_maker_autorun.py", "--no-repl"],
            log_path=v2_root / "POLYMARKET_MAKER_AUTO" / "autorun_systemd.log",
            frozen_runtime=frozen_runtime,
            force_source=force_source,
        ),
        "v3multi": _build_service_spec(
            packaged_executable=v3multi_bin,
            source_cwd=v3_root,
            source_cmd=[*python_cmd, "copytrade_run.py", "--config", "copytrade_config.json"],
            log_path=v3_root / "logs" / "panel_runtime.log",
            frozen_runtime=frozen_runtime,
            force_source=force_source,
        ),
    }
    LOCAL_SERVICE_SPECS.update(specs)
    return LOCAL_SERVICE_SPECS


def _json_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: Dict[str, Any],
    extra_headers: Dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if extra_headers:
        for key, value in extra_headers.items():
            handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    body: str,
    content_type: str,
    extra_headers: Dict[str, str] | None = None,
) -> None:
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", f"{content_type}; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    if extra_headers:
        for key, value in extra_headers.items():
            handler.send_header(key, value)
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
            **_windows_subprocess_kwargs(),
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


def _tail_log(path: Path, max_lines: int = 20, max_chars: int = 2000) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
    return tail


def _local_service_status() -> Dict[str, Any]:
    services: Dict[str, Any] = {}
    for key, meta in SERVICE_DEFS.items():
        pid = _read_pid(key)
        active = _pid_exists(pid)
        if pid and not active:
            _clear_pid(key)
        services[key] = {
            "service": key,
            "label": meta["label"],
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
    if not spec.get("cmd"):
        return {"ok": False, "message": str(spec.get("error") or "service command unavailable")}

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
    child_env.setdefault("POLY_APP_ROOT", str(SOURCE_ROOT))
    child_env.setdefault("POLY_INSTANCE_ROOT", str(INSTANCE_ROOT))
    for env_key, env_value in get_account_payload().items():
        text = str(env_value or "").strip()
        if text:
            child_env[env_key] = text
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        kwargs["close_fds"] = True
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(
            spec["cmd"],
            cwd=str(spec["cwd"]),
            stdout=log_handle,
            stderr=log_handle,
            env=child_env,
            creationflags=creationflags,
            **kwargs,
        )
    except Exception as exc:
        log_handle.close()
        return {"ok": False, "message": str(exc)}

    log_handle.close()
    _write_pid(service_key, proc.pid)
    time.sleep(1.0)
    if not _pid_exists(proc.pid):
        _clear_pid(service_key)
        _clear_stop_file(service_key)
        log_excerpt = _tail_log(spec["log"])
        message = "process exited immediately"
        if log_excerpt:
            message += f"\n{log_excerpt}"
        return {"ok": False, "message": message}
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
    for key, meta in SERVICE_DEFS.items():
        service_name = meta["systemd"]
        exists_ok, load_state = _run_command("systemctl", "show", service_name, "--property", "LoadState", "--value")
        load_state_text = load_state.strip()
        if (not exists_ok) or load_state_text == "not-found":
            local_payload = _local_service_status()
            local_service = dict(local_payload["services"][key])
            local_service["service"] = service_name
            local_service["label"] = meta["label"]
            local_service["mode"] = "local-process"
            services[key] = local_service
            continue

        ok, output = _run_command("systemctl", "is-active", service_name)
        services[key] = {
            "service": service_name,
            "label": meta["label"],
            "active": ok and output.strip() == "active",
            "raw": output.strip() or "unknown",
            "mode": "systemd",
        }
    return {"supported": True, "mode": "hybrid", "services": services}


def _service_action(action: str, service_key: str) -> Dict[str, Any]:
    meta = SERVICE_DEFS.get(service_key)
    if not meta:
        return {"ok": False, "message": f"unknown service: {service_key}"}
    service_name = meta["systemd"]

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
    exists_ok, load_state = _run_command("systemctl", "show", service_name, "--property", "LoadState", "--value")
    if (not exists_ok) or load_state.strip() == "not-found":
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


def _parse_cookie_map(raw_cookie: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for part in raw_cookie.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _cookie_secure() -> bool:
    return str(os.getenv("POLY_REVERSE_PROXY_MODE") or "").strip() in {"1", "https", "secure"}


def _clear_expired_sessions() -> None:
    now = _now()
    expired = [token for token, item in SESSION_STORE.items() if float(item.get("exp", 0)) <= now]
    for token in expired:
        SESSION_STORE.pop(token, None)


def _make_signed_token(raw_token: str) -> str:
    signature = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{raw_token}.{signature}"


def _split_signed_token(value: str) -> Tuple[str, str] | None:
    if "." not in value:
        return None
    raw, signature = value.rsplit(".", 1)
    if not raw or not signature:
        return None
    return raw, signature


def _verify_signed_token(value: str) -> str | None:
    parsed = _split_signed_token(value)
    if parsed is None:
        return None
    raw, signature = parsed
    expected = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return raw


def _create_session(username: str) -> str:
    _clear_expired_sessions()
    raw_token = secrets.token_urlsafe(32)
    SESSION_STORE[raw_token] = {"user": username, "exp": _now() + SESSION_TTL_SEC}
    return _make_signed_token(raw_token)


def _set_session_cookie(value: str) -> str:
    attrs = [
        f"{SESSION_COOKIE_NAME}={value}",
        "HttpOnly",
        "Path=/",
        "SameSite=Strict",
        f"Max-Age={SESSION_TTL_SEC}",
    ]
    if _cookie_secure():
        attrs.append("Secure")
    return "; ".join(attrs)


def _clear_session_cookie() -> str:
    attrs = [
        f"{SESSION_COOKIE_NAME}=",
        "HttpOnly",
        "Path=/",
        "SameSite=Strict",
        "Max-Age=0",
    ]
    if _cookie_secure():
        attrs.append("Secure")
    return "; ".join(attrs)


def _active_session(handler: BaseHTTPRequestHandler) -> Dict[str, Any] | None:
    if not AUTH_REQUIRED:
        return {"user": DEFAULT_AUTH_USERNAME, "exp": _now() + SESSION_TTL_SEC}

    _clear_expired_sessions()
    raw_cookie = str(handler.headers.get("Cookie") or "")
    cookie_map = _parse_cookie_map(raw_cookie)
    signed = cookie_map.get(SESSION_COOKIE_NAME)
    if not signed:
        return None
    token = _verify_signed_token(signed)
    if token is None:
        return None
    session = SESSION_STORE.get(token)
    if not session:
        return None
    if float(session.get("exp", 0)) <= _now():
        SESSION_STORE.pop(token, None)
        return None
    return session


def _is_authenticated(handler: BaseHTTPRequestHandler) -> bool:
    return _active_session(handler) is not None


def _must_change_credentials(handler: BaseHTTPRequestHandler) -> bool:
    session = _active_session(handler)
    if not session:
        return False
    state = _load_auth_state()
    return session.get("user") == state.get("username") and bool(state.get("must_change_credentials", True))


def _public_api_path(path: str) -> bool:
    return path in {"/api/ping", "/api/auth/login", "/api/auth/session", "/api/auth/logout"}


def _auth_required_for(path: str) -> bool:
    return path.startswith("/api/") and not _public_api_path(path)


def _setup_allowed_path(path: str) -> bool:
    return path in {"/api/auth/session", "/api/auth/logout", "/api/auth/credentials"}


def _instance_payload() -> Dict[str, str]:
    return {
        "source_root": str(SOURCE_ROOT),
        "instance_root": str(INSTANCE_ROOT),
        "v2_root": str(resolve_v2_root()),
        "v3_root": str(resolve_v3_root()),
        "run_root": str(RUN_DIR),
    }


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "PolymarketPanel/0.2"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _touch_activity(self) -> None:
        setattr(self.server, "last_request_ts", _now())

    def _reject_unauthorized(self) -> None:
        _json_response(
            self,
            HTTPStatus.UNAUTHORIZED,
            {"error": "authentication required", "code": "AUTH_REQUIRED"},
        )

    def _reject_setup_required(self) -> None:
        _json_response(
            self,
            HTTPStatus.FORBIDDEN,
            {
                "error": "credentials update required",
                "code": "AUTH_SETUP_REQUIRED",
                "auth": _public_auth_state(),
            },
        )

    def _guard_auth(self, path: str) -> bool:
        if not _auth_required_for(path):
            return True
        if not _is_authenticated(self):
            self._reject_unauthorized()
            return False
        if _must_change_credentials(self) and not _setup_allowed_path(path):
            self._reject_setup_required()
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        self._touch_activity()
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/auth/session":
            session = _active_session(self)
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "required": AUTH_REQUIRED,
                    "authenticated": session is not None,
                    "auth": _public_auth_state(),
                    "must_change_credentials": _must_change_credentials(self),
                    "username": str(session.get("user") or "") if session else "",
                    "instance": _instance_payload(),
                },
            )
            return

        if not self._guard_auth(path):
            return
        if path == "/api/account":
            _json_response(self, HTTPStatus.OK, {"account": get_account_payload()})
            return
        if path == "/api/settings":
            _json_response(self, HTTPStatus.OK, {"settings": get_settings_payload()})
            return
        if path == "/api/runtime":
            payload = get_runtime_payload()
            payload["services"] = _service_status()
            payload["instance"] = _instance_payload()
            _json_response(self, HTTPStatus.OK, payload)
            return
        if path == "/api/ping":
            _json_response(self, HTTPStatus.OK, {"ok": True})
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
            payload["instance"] = _instance_payload()
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
        self._touch_activity()
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/auth/login":
            payload = _read_json_body(self)
            username = str(payload.get("username") or "")
            password = str(payload.get("password") or "")

            if not AUTH_REQUIRED:
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "required": False,
                        "authenticated": True,
                        "instance": _instance_payload(),
                    },
                )
                return

            if not _verify_auth_credentials(username, password):
                _json_response(
                    self,
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "invalid username or password"},
                )
                return

            signed_token = _create_session(username)
            auth_state = _public_auth_state()
            _json_response(
                self,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "required": True,
                    "authenticated": True,
                    "auth": auth_state,
                    "must_change_credentials": bool(auth_state.get("must_change_credentials", True)),
                    "username": auth_state.get("username", username),
                    "instance": _instance_payload(),
                },
                extra_headers={"Set-Cookie": _set_session_cookie(signed_token)},
            )
            return

        if path == "/api/auth/logout":
            raw_cookie = str(self.headers.get("Cookie") or "")
            cookie_map = _parse_cookie_map(raw_cookie)
            signed = cookie_map.get(SESSION_COOKIE_NAME, "")
            token = _verify_signed_token(signed) if signed else None
            if token:
                SESSION_STORE.pop(token, None)
            _json_response(
                self,
                HTTPStatus.OK,
                {"ok": True, "authenticated": False},
                extra_headers={"Set-Cookie": _clear_session_cookie()},
            )
            return

        if not self._guard_auth(path):
            return

        try:
            if path == "/api/auth/credentials":
                payload = _read_json_body(self)
                username = str(payload.get("username") or "")
                password = str(payload.get("password") or "")
                password_confirm = str(payload.get("password_confirm") or "")
                if password != password_confirm:
                    raise ValueError("password confirmation does not match")
                auth_state = _update_auth_credentials(username, password)
                signed_token = _create_session(str(auth_state.get("username") or username))
                _json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "required": True,
                        "authenticated": True,
                        "auth": auth_state,
                        "must_change_credentials": False,
                        "username": auth_state.get("username", username),
                        "instance": _instance_payload(),
                    },
                    extra_headers={"Set-Cookie": _set_session_cookie(signed_token)},
                )
                return
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
    server = ThreadingHTTPServer((host, port), PanelHandler)
    setattr(server, "last_request_ts", _now())
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket local control panel")
    parser.add_argument("--host", default=str(os.getenv("POLY_PANEL_HOST") or "127.0.0.1"))
    parser.add_argument("--port", default=int(str(os.getenv("POLY_PANEL_PORT") or "8787")), type=int)
    args = parser.parse_args()

    server = create_http_server(args.host, args.port)
    print(f"[PANEL] listening on http://{args.host}:{args.port}")
    print(f"[PANEL] source_root={SOURCE_ROOT}")
    print(f"[PANEL] instance_root={INSTANCE_ROOT}")
    print(f"[PANEL] auth_required={'1' if AUTH_REQUIRED else '0'}")
    server.serve_forever()


if __name__ == "__main__":
    main()
