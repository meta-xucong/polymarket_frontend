from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml
from runtime_paths import (
    resolve_instance_root,
    resolve_source_root,
    resolve_v2_root,
    resolve_v2_source_root,
    resolve_v3_root,
    resolve_v3_source_root,
)

# Backward-compatible path constants (tests monkeypatch these names).
V2_BASE_DIR = resolve_v2_root()
ACCOUNT_PATH = V2_BASE_DIR / "account.json"
COPYTRADE_CONFIG_PATH = V2_BASE_DIR / "copytrade" / "copytrade_config.json"
GLOBAL_CONFIG_PATH = (
    V2_BASE_DIR / "POLYMARKET_MAKER_AUTO" / "POLYMARKET_MAKER" / "config" / "global_config.json"
)
RUN_PARAMS_PATH = (
    V2_BASE_DIR / "POLYMARKET_MAKER_AUTO" / "POLYMARKET_MAKER" / "config" / "run_params.json"
)
STRATEGY_DEFAULTS_PATH = (
    V2_BASE_DIR / "POLYMARKET_MAKER_AUTO" / "POLYMARKET_MAKER" / "config" / "strategy_defaults.json"
)
STATUS_PATH = V2_BASE_DIR / "POLYMARKET_MAKER_AUTO" / "data" / "autorun_status.json"
TOKENS_PATH = V2_BASE_DIR / "copytrade" / "tokens_from_copytrade.json"
COPYTRADE_STATE_PATH = V2_BASE_DIR / "copytrade" / "copytrade_state.json"
V3_BASE_DIR = resolve_v3_root()
V3_CONFIG_PATH = V3_BASE_DIR / "copytrade_config.json"
V3_ACCOUNTS_PATH = V3_BASE_DIR / "accounts.json"


def _instance_mode_enabled() -> bool:
    # When instance root is explicit or differs from source root, write into instance scope.
    override = str(os.getenv("POLY_INSTANCE_ROOT") or "").strip()
    if override:
        return True
    return resolve_instance_root() != resolve_source_root()


def _resolve_overlay_paths(
    source_path: Path,
    instance_candidates: List[Path],
) -> Tuple[Path, Path]:
    read_path = source_path
    for candidate in instance_candidates:
        if candidate.exists():
            read_path = candidate
            break

    if _instance_mode_enabled():
        write_path = next((path for path in instance_candidates if path.exists()), instance_candidates[0])
    else:
        write_path = read_path if read_path.exists() else source_path

    return read_path, write_path


def _resolve_v2_paths(*parts: str) -> Tuple[Path, Path]:
    source = resolve_v2_source_root().joinpath(*parts)
    instance_root = resolve_instance_root()
    instance_candidates = [
        instance_root / "v2" / Path(*parts),
        instance_root / "POLYMARKET_MAKER_copytrade_v2" / Path(*parts),
    ]
    return _resolve_overlay_paths(source, instance_candidates)


def _resolve_v3_paths(*parts: str) -> Tuple[Path, Path]:
    source = resolve_v3_source_root().joinpath(*parts)
    instance_root = resolve_instance_root()
    instance_candidates = [
        instance_root / "smartmoney" / Path(*parts),
        instance_root / "POLY_SMARTMONEY" / "copytrade_v3_muti" / Path(*parts),
    ]
    return _resolve_overlay_paths(source, instance_candidates)


def _account_paths() -> Tuple[Path, Path]:
    default = resolve_v2_root() / "account.json"
    if ACCOUNT_PATH != default:
        return ACCOUNT_PATH, ACCOUNT_PATH
    return _resolve_v2_paths("account.json")


def _copytrade_config_paths() -> Tuple[Path, Path]:
    default = resolve_v2_root() / "copytrade" / "copytrade_config.json"
    if COPYTRADE_CONFIG_PATH != default:
        return COPYTRADE_CONFIG_PATH, COPYTRADE_CONFIG_PATH
    return _resolve_v2_paths("copytrade", "copytrade_config.json")


def _global_config_paths() -> Tuple[Path, Path]:
    default = (
        resolve_v2_root() / "POLYMARKET_MAKER_AUTO" / "POLYMARKET_MAKER" / "config" / "global_config.json"
    )
    if GLOBAL_CONFIG_PATH != default:
        return GLOBAL_CONFIG_PATH, GLOBAL_CONFIG_PATH
    return _resolve_v2_paths(
        "POLYMARKET_MAKER_AUTO",
        "POLYMARKET_MAKER",
        "config",
        "global_config.json",
    )


def _run_params_paths() -> Tuple[Path, Path]:
    default = (
        resolve_v2_root() / "POLYMARKET_MAKER_AUTO" / "POLYMARKET_MAKER" / "config" / "run_params.json"
    )
    if RUN_PARAMS_PATH != default:
        return RUN_PARAMS_PATH, RUN_PARAMS_PATH
    return _resolve_v2_paths(
        "POLYMARKET_MAKER_AUTO",
        "POLYMARKET_MAKER",
        "config",
        "run_params.json",
    )


def _strategy_defaults_paths() -> Tuple[Path, Path]:
    default = (
        resolve_v2_root()
        / "POLYMARKET_MAKER_AUTO"
        / "POLYMARKET_MAKER"
        / "config"
        / "strategy_defaults.json"
    )
    if STRATEGY_DEFAULTS_PATH != default:
        return STRATEGY_DEFAULTS_PATH, STRATEGY_DEFAULTS_PATH
    return _resolve_v2_paths(
        "POLYMARKET_MAKER_AUTO",
        "POLYMARKET_MAKER",
        "config",
        "strategy_defaults.json",
    )


def _status_paths() -> Tuple[Path, Path]:
    default = resolve_v2_root() / "POLYMARKET_MAKER_AUTO" / "data" / "autorun_status.json"
    if STATUS_PATH != default:
        return STATUS_PATH, STATUS_PATH
    return _resolve_v2_paths("POLYMARKET_MAKER_AUTO", "data", "autorun_status.json")


def _tokens_paths() -> Tuple[Path, Path]:
    default = resolve_v2_root() / "copytrade" / "tokens_from_copytrade.json"
    if TOKENS_PATH != default:
        return TOKENS_PATH, TOKENS_PATH
    return _resolve_v2_paths("copytrade", "tokens_from_copytrade.json")


def _copytrade_state_paths() -> Tuple[Path, Path]:
    default = resolve_v2_root() / "copytrade" / "copytrade_state.json"
    if COPYTRADE_STATE_PATH != default:
        return COPYTRADE_STATE_PATH, COPYTRADE_STATE_PATH
    return _resolve_v2_paths("copytrade", "copytrade_state.json")


def _v3_config_paths() -> Tuple[Path, Path]:
    default = resolve_v3_root() / "copytrade_config.json"
    if V3_CONFIG_PATH != default:
        return V3_CONFIG_PATH, V3_CONFIG_PATH
    return _resolve_v3_paths("copytrade_config.json")


def _v3_accounts_paths() -> Tuple[Path, Path]:
    default = resolve_v3_root() / "accounts.json"
    if V3_ACCOUNTS_PATH != default:
        return V3_ACCOUNTS_PATH, V3_ACCOUNTS_PATH
    return _resolve_v3_paths("accounts.json")


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {path}")
    return payload


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".panel_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_json_with_optional_mirror(
    path: Path,
    payload: Dict[str, Any],
    mirror_path: Path | None = None,
) -> None:
    _atomic_write_json(path, payload)
    if mirror_path is None:
        return
    if path.resolve() == mirror_path.resolve():
        return
    _atomic_write_json(mirror_path, payload)


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return bool(default)


def _normalize_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _read_any_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_target_addresses(copytrade_cfg: Dict[str, Any]) -> List[str]:
    targets = copytrade_cfg.get("targets")
    if not isinstance(targets, list):
        return []
    addresses: List[str] = []
    for item in targets:
        if not isinstance(item, dict):
            continue
        account = str(item.get("account") or "").strip()
        if account:
            addresses.append(account)
    return addresses


def _extract_min_size(copytrade_cfg: Dict[str, Any]) -> float:
    targets = copytrade_cfg.get("targets")
    if not isinstance(targets, list):
        return 5.0
    for item in targets:
        if not isinstance(item, dict):
            continue
        if item.get("min_size") is not None:
            return _coerce_float(item.get("min_size"), 5.0)
    return 5.0


def _read_v3_accounts_file() -> Dict[str, Any]:
    read_path, _ = _v3_accounts_paths()
    payload = _read_any_json(read_path)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object expected: {read_path}")
    return payload


def _coerce_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _latest_log_path(log_dir: Path, pattern: str) -> Path | None:
    if not log_dir.exists():
        return None
    candidates = sorted(
        log_dir.glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _list_v3_accounts(accounts_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_accounts = accounts_payload.get("accounts")
    if not isinstance(raw_accounts, list):
        return []
    items: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_accounts):
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "index": index,
                "name": str(item.get("name") or f"Account {index + 1}"),
                "my_address": str(item.get("my_address") or ""),
                "enabled": _coerce_bool(item.get("enabled"), True),
            }
        )
    return items


def _get_v3_account(accounts_payload: Dict[str, Any], index: int) -> Dict[str, Any]:
    raw_accounts = accounts_payload.get("accounts")
    if not isinstance(raw_accounts, list):
        raise ValueError("accounts.json missing accounts array")
    if index < 0 or index >= len(raw_accounts):
        raise ValueError(f"invalid account index: {index}")
    item = raw_accounts[index]
    if not isinstance(item, dict):
        raise ValueError(f"account entry is not an object: {index}")
    return item


def get_account_payload() -> Dict[str, Any]:
    read_path, _ = _account_paths()
    payload = _read_json(read_path)
    return {
        "POLY_HOST": str(payload.get("POLY_HOST") or "https://clob.polymarket.com"),
        "POLY_CHAIN_ID": _coerce_int(payload.get("POLY_CHAIN_ID"), 137),
        "POLY_SIGNATURE": _coerce_int(payload.get("POLY_SIGNATURE"), 2),
        "POLY_KEY": str(payload.get("POLY_KEY") or ""),
        "POLY_FUNDER": str(payload.get("POLY_FUNDER") or ""),
        "POLY_API_KEY": str(payload.get("POLY_API_KEY") or ""),
        "POLY_API_SECRET": str(payload.get("POLY_API_SECRET") or ""),
        "POLY_API_PASSPHRASE": str(payload.get("POLY_API_PASSPHRASE") or ""),
        "POLY_DATA_ADDRESS": str(payload.get("POLY_DATA_ADDRESS") or ""),
    }


def save_account_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    _, write_path = _account_paths()
    default_source_path = resolve_v2_source_root() / "account.json"
    current = get_account_payload()
    current.update(
        {
            "POLY_HOST": str(payload.get("POLY_HOST") or current["POLY_HOST"]).strip(),
            "POLY_CHAIN_ID": _coerce_int(payload.get("POLY_CHAIN_ID"), current["POLY_CHAIN_ID"]),
            "POLY_SIGNATURE": _coerce_int(payload.get("POLY_SIGNATURE"), current["POLY_SIGNATURE"]),
            "POLY_KEY": str(payload.get("POLY_KEY") or current["POLY_KEY"]).strip(),
            "POLY_FUNDER": str(payload.get("POLY_FUNDER") or current["POLY_FUNDER"]).strip(),
            "POLY_API_KEY": str(payload.get("POLY_API_KEY") or current["POLY_API_KEY"]).strip(),
            "POLY_API_SECRET": str(payload.get("POLY_API_SECRET") or current["POLY_API_SECRET"]).strip(),
            "POLY_API_PASSPHRASE": str(
                payload.get("POLY_API_PASSPHRASE") or current["POLY_API_PASSPHRASE"]
            ).strip(),
            "POLY_DATA_ADDRESS": str(
                payload.get("POLY_DATA_ADDRESS") or current["POLY_DATA_ADDRESS"]
            ).strip(),
        }
    )
    mirror_path = None
    if ACCOUNT_PATH == default_source_path and _instance_mode_enabled():
        mirror_path = default_source_path
    _atomic_write_json_with_optional_mirror(write_path, current, mirror_path=mirror_path)
    return get_account_payload()


def get_settings_payload() -> Dict[str, Any]:
    copytrade_cfg = _read_json(_copytrade_config_paths()[0])
    global_cfg = _read_json(_global_config_paths()[0])
    run_cfg = _read_json(_run_params_paths()[0])
    strategy_defaults = _read_json(_strategy_defaults_paths()[0])

    scheduler = global_cfg.get("scheduler") if isinstance(global_cfg.get("scheduler"), dict) else {}
    defaults = (
        strategy_defaults.get("default")
        if isinstance(strategy_defaults.get("default"), dict)
        else {}
    )
    shock_guard = run_cfg.get("shock_guard") if isinstance(run_cfg.get("shock_guard"), dict) else {}

    return {
        "copytrade": {
            "target_addresses": _extract_target_addresses(copytrade_cfg),
            "poll_interval_sec": _coerce_float(copytrade_cfg.get("poll_interval_sec"), 60.0),
            "min_size": _coerce_float(_extract_min_size(copytrade_cfg), 5.0),
        },
        "scheduler": {
            "max_concurrent_tasks": _coerce_int(scheduler.get("max_concurrent_tasks"), 10),
            "copytrade_poll_seconds": _coerce_float(scheduler.get("copytrade_poll_seconds"), 30.0),
            "command_poll_seconds": _coerce_float(scheduler.get("command_poll_seconds"), 30.0),
            "strategy_mode": str(scheduler.get("strategy_mode") or "classic"),
            "burst_slots": _coerce_int(scheduler.get("burst_slots"), 10),
        },
        "strategy": {
            "order_size": _coerce_float(defaults.get("order_size"), 10.0),
            "max_position_per_market": _coerce_float(
                defaults.get("max_position_per_market"), 10.0
            ),
            "drop_pct": _coerce_float(run_cfg.get("drop_pct"), 0.0),
            "profit_pct": _coerce_float(run_cfg.get("profit_pct"), 0.003),
            "sell_mode": str(run_cfg.get("sell_mode") or "aggressive"),
            "shock_guard_enabled": _coerce_bool(shock_guard.get("enabled"), False),
            "shock_window_sec": _coerce_int(shock_guard.get("shock_window_sec"), 180),
            "shock_drop_pct": _coerce_float(shock_guard.get("shock_drop_pct"), 0.1),
        },
    }


def save_settings_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    copytrade_read, copytrade_write = _copytrade_config_paths()
    global_read, global_write = _global_config_paths()
    run_read, run_write = _run_params_paths()
    defaults_read, defaults_write = _strategy_defaults_paths()

    copytrade_cfg = _read_json(copytrade_read)
    global_cfg = _read_json(global_read)
    run_cfg = _read_json(run_read)
    strategy_defaults = _read_json(defaults_read)

    copytrade_in = payload.get("copytrade") if isinstance(payload.get("copytrade"), dict) else {}
    scheduler_in = payload.get("scheduler") if isinstance(payload.get("scheduler"), dict) else {}
    strategy_in = payload.get("strategy") if isinstance(payload.get("strategy"), dict) else {}

    target_addresses = copytrade_in.get("target_addresses")
    if isinstance(target_addresses, list):
        existing_targets = copytrade_cfg.get("targets")
        next_targets = []
        if not isinstance(existing_targets, list):
            existing_targets = []
        min_size = _coerce_float(copytrade_in.get("min_size"), _extract_min_size(copytrade_cfg))
        for index, address in enumerate(target_addresses):
            text = str(address or "").strip()
            if not text:
                continue
            base_item = (
                dict(existing_targets[index])
                if index < len(existing_targets) and isinstance(existing_targets[index], dict)
                else {}
            )
            base_item["account"] = text
            base_item["min_size"] = min_size
            base_item["enabled"] = bool(base_item.get("enabled", True))
            next_targets.append(base_item)
        copytrade_cfg["targets"] = next_targets

    if "poll_interval_sec" in copytrade_in:
        copytrade_cfg["poll_interval_sec"] = _coerce_float(
            copytrade_in.get("poll_interval_sec"), copytrade_cfg.get("poll_interval_sec", 60.0)
        )

    scheduler = global_cfg.get("scheduler")
    if not isinstance(scheduler, dict):
        scheduler = {}
        global_cfg["scheduler"] = scheduler
    if "max_concurrent_tasks" in scheduler_in:
        scheduler["max_concurrent_tasks"] = _coerce_int(
            scheduler_in.get("max_concurrent_tasks"), scheduler.get("max_concurrent_tasks", 10)
        )
    if "copytrade_poll_seconds" in scheduler_in:
        scheduler["copytrade_poll_seconds"] = _coerce_float(
            scheduler_in.get("copytrade_poll_seconds"),
            scheduler.get("copytrade_poll_seconds", 30.0),
        )
    if "command_poll_seconds" in scheduler_in:
        scheduler["command_poll_seconds"] = _coerce_float(
            scheduler_in.get("command_poll_seconds"),
            scheduler.get("command_poll_seconds", 30.0),
        )
    if "strategy_mode" in scheduler_in:
        scheduler["strategy_mode"] = str(scheduler_in.get("strategy_mode") or "classic").strip()
    if "burst_slots" in scheduler_in:
        scheduler["burst_slots"] = _coerce_int(
            scheduler_in.get("burst_slots"), scheduler.get("burst_slots", 10)
        )

    defaults = strategy_defaults.get("default")
    if not isinstance(defaults, dict):
        defaults = {}
        strategy_defaults["default"] = defaults
    if "order_size" in strategy_in:
        defaults["order_size"] = _coerce_float(
            strategy_in.get("order_size"), defaults.get("order_size", 10.0)
        )
    if "max_position_per_market" in strategy_in:
        defaults["max_position_per_market"] = _coerce_float(
            strategy_in.get("max_position_per_market"),
            defaults.get("max_position_per_market", 10.0),
        )

    if "drop_pct" in strategy_in:
        run_cfg["drop_pct"] = _coerce_float(strategy_in.get("drop_pct"), run_cfg.get("drop_pct", 0.0))
    if "profit_pct" in strategy_in:
        run_cfg["profit_pct"] = _coerce_float(
            strategy_in.get("profit_pct"), run_cfg.get("profit_pct", 0.003)
        )
    if "sell_mode" in strategy_in:
        run_cfg["sell_mode"] = str(strategy_in.get("sell_mode") or "aggressive").strip()
    shock_guard = run_cfg.get("shock_guard")
    if not isinstance(shock_guard, dict):
        shock_guard = {}
        run_cfg["shock_guard"] = shock_guard
    if "shock_guard_enabled" in strategy_in:
        shock_guard["enabled"] = _coerce_bool(
            strategy_in.get("shock_guard_enabled"), shock_guard.get("enabled", False)
        )
    if "shock_window_sec" in strategy_in:
        shock_guard["shock_window_sec"] = _coerce_int(
            strategy_in.get("shock_window_sec"), shock_guard.get("shock_window_sec", 180)
        )
    if "shock_drop_pct" in strategy_in:
        shock_guard["shock_drop_pct"] = _coerce_float(
            strategy_in.get("shock_drop_pct"), shock_guard.get("shock_drop_pct", 0.1)
        )

    copytrade_source = resolve_v2_source_root() / "copytrade" / "copytrade_config.json"
    global_source = (
        resolve_v2_source_root()
        / "POLYMARKET_MAKER_AUTO"
        / "POLYMARKET_MAKER"
        / "config"
        / "global_config.json"
    )
    run_source = (
        resolve_v2_source_root()
        / "POLYMARKET_MAKER_AUTO"
        / "POLYMARKET_MAKER"
        / "config"
        / "run_params.json"
    )
    defaults_source = (
        resolve_v2_source_root()
        / "POLYMARKET_MAKER_AUTO"
        / "POLYMARKET_MAKER"
        / "config"
        / "strategy_defaults.json"
    )
    copytrade_mirror = copytrade_source if COPYTRADE_CONFIG_PATH == copytrade_source and _instance_mode_enabled() else None
    global_mirror = global_source if GLOBAL_CONFIG_PATH == global_source and _instance_mode_enabled() else None
    run_mirror = run_source if RUN_PARAMS_PATH == run_source and _instance_mode_enabled() else None
    defaults_mirror = defaults_source if STRATEGY_DEFAULTS_PATH == defaults_source and _instance_mode_enabled() else None

    _atomic_write_json_with_optional_mirror(copytrade_write, copytrade_cfg, mirror_path=copytrade_mirror)
    _atomic_write_json_with_optional_mirror(global_write, global_cfg, mirror_path=global_mirror)
    _atomic_write_json_with_optional_mirror(run_write, run_cfg, mirror_path=run_mirror)
    _atomic_write_json_with_optional_mirror(defaults_write, strategy_defaults, mirror_path=defaults_mirror)
    return get_settings_payload()


def _read_log_tail(path: Path, max_lines: int = 120) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def _latest_autorun_log_path() -> Path | None:
    v2_root = resolve_v2_root()
    log_dir = v2_root / "POLYMARKET_MAKER_AUTO" / "logs" / "autorun"
    return _latest_log_path(log_dir, "autorun_main_*.log")


def get_runtime_payload() -> Dict[str, Any]:
    status = _read_json(_status_paths()[0])
    tokens = _read_json(_tokens_paths()[0])
    copytrade_state = _read_json(_copytrade_state_paths()[0])
    active_tokens = tokens.get("tokens") if isinstance(tokens.get("tokens"), list) else []
    autorun_log_path = _latest_autorun_log_path()
    v2_root = resolve_v2_root()
    return {
        "autorun_status": status,
        "active_token_count": len(active_tokens),
        "copytrade_updated_at": copytrade_state.get("updated_at"),
        "copytrade_log_tail": _read_log_tail(v2_root / "copytrade" / "copytrade_systemd.log"),
        "autorun_log_tail": _read_log_tail(autorun_log_path) if autorun_log_path else "",
    }


def get_v3_settings_payload() -> Dict[str, Any]:
    config = _read_json(_v3_config_paths()[0])
    accounts_payload = _read_v3_accounts_file()
    account_summaries = _list_v3_accounts(accounts_payload)
    selected_index = 0 if account_summaries else None
    selected_account = get_v3_account_payload(selected_index) if selected_index is not None else None

    return {
        "global": {
            "target_addresses": _coerce_string_list(config.get("target_addresses")),
            "poll_interval_sec": _coerce_float(config.get("poll_interval_sec"), 24.0),
            "poll_interval_sec_exiting": _coerce_float(
                config.get("poll_interval_sec_exiting"), 4.0
            ),
            "boot_sync_mode": str(config.get("boot_sync_mode") or "baseline_only"),
            "actions_replay_window_sec": _coerce_int(
                config.get("actions_replay_window_sec"), 86400
            ),
            "follow_new_topics_only": _coerce_bool(
                config.get("follow_new_topics_only"), False
            ),
            "min_order_usd": _coerce_float(config.get("min_order_usd"), 1.0),
            "max_order_usd": _coerce_float(config.get("max_order_usd"), 6.0),
            "max_notional_per_token": _coerce_float(config.get("max_notional_per_token"), 0.0),
            "max_notional_total": _coerce_float(config.get("max_notional_total"), 0.0),
            "taker_enabled": _coerce_bool(config.get("taker_enabled"), True),
            "taker_spread_threshold": _coerce_float(
                config.get("taker_spread_threshold"), 0.011
            ),
            "taker_order_type": str(config.get("taker_order_type") or "FAK"),
            "maker_max_wait_sec": _coerce_int(config.get("maker_max_wait_sec"), 0),
            "maker_to_taker_enabled": _coerce_bool(
                config.get("maker_to_taker_enabled"), False
            ),
            "lowp_guard_enabled": _coerce_bool(config.get("lowp_guard_enabled"), False),
            "lowp_price_threshold": _coerce_float(config.get("lowp_price_threshold"), 0.05),
            "lowp_follow_ratio_mult": _coerce_float(
                config.get("lowp_follow_ratio_mult"), 0.02
            ),
            "lowp_min_order_usd": _coerce_float(config.get("lowp_min_order_usd"), 1.0),
            "lowp_max_order_usd": _coerce_float(config.get("lowp_max_order_usd"), 2.0),
        },
        "accounts": account_summaries,
        "selected_account_index": selected_index,
        "selected_account": selected_account,
    }


def save_v3_settings_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    read_path, write_path = _v3_config_paths()
    default_source_path = resolve_v3_source_root() / "copytrade_config.json"
    config = _read_json(read_path)
    incoming = payload.get("global") if isinstance(payload.get("global"), dict) else payload

    if "target_addresses" in incoming:
        config["target_addresses"] = _coerce_string_list(incoming.get("target_addresses"))
    if "poll_interval_sec" in incoming:
        config["poll_interval_sec"] = _coerce_float(
            incoming.get("poll_interval_sec"), config.get("poll_interval_sec", 24.0)
        )
    if "poll_interval_sec_exiting" in incoming:
        config["poll_interval_sec_exiting"] = _coerce_float(
            incoming.get("poll_interval_sec_exiting"),
            config.get("poll_interval_sec_exiting", 4.0),
        )
    if "boot_sync_mode" in incoming:
        config["boot_sync_mode"] = str(
            incoming.get("boot_sync_mode") or config.get("boot_sync_mode") or "baseline_only"
        ).strip()
    if "actions_replay_window_sec" in incoming:
        config["actions_replay_window_sec"] = _coerce_int(
            incoming.get("actions_replay_window_sec"),
            config.get("actions_replay_window_sec", 86400),
        )
    if "follow_new_topics_only" in incoming:
        config["follow_new_topics_only"] = _coerce_bool(
            incoming.get("follow_new_topics_only"),
            config.get("follow_new_topics_only", False),
        )
    if "min_order_usd" in incoming:
        config["min_order_usd"] = _coerce_float(
            incoming.get("min_order_usd"), config.get("min_order_usd", 1.0)
        )
    if "max_order_usd" in incoming:
        config["max_order_usd"] = _coerce_float(
            incoming.get("max_order_usd"), config.get("max_order_usd", 6.0)
        )
    if "max_notional_per_token" in incoming:
        config["max_notional_per_token"] = _coerce_float(
            incoming.get("max_notional_per_token"),
            config.get("max_notional_per_token", 0.0),
        )
    if "max_notional_total" in incoming:
        config["max_notional_total"] = _coerce_float(
            incoming.get("max_notional_total"),
            config.get("max_notional_total", 0.0),
        )
    if "taker_enabled" in incoming:
        config["taker_enabled"] = _coerce_bool(
            incoming.get("taker_enabled"), config.get("taker_enabled", True)
        )
    if "taker_spread_threshold" in incoming:
        config["taker_spread_threshold"] = _coerce_float(
            incoming.get("taker_spread_threshold"),
            config.get("taker_spread_threshold", 0.011),
        )
    if "taker_order_type" in incoming:
        config["taker_order_type"] = str(
            incoming.get("taker_order_type") or config.get("taker_order_type") or "FAK"
        ).strip()
    if "maker_max_wait_sec" in incoming:
        config["maker_max_wait_sec"] = _coerce_int(
            incoming.get("maker_max_wait_sec"), config.get("maker_max_wait_sec", 0)
        )
    if "maker_to_taker_enabled" in incoming:
        config["maker_to_taker_enabled"] = _coerce_bool(
            incoming.get("maker_to_taker_enabled"),
            config.get("maker_to_taker_enabled", False),
        )
    if "lowp_guard_enabled" in incoming:
        config["lowp_guard_enabled"] = _coerce_bool(
            incoming.get("lowp_guard_enabled"), config.get("lowp_guard_enabled", False)
        )
    if "lowp_price_threshold" in incoming:
        config["lowp_price_threshold"] = _coerce_float(
            incoming.get("lowp_price_threshold"),
            config.get("lowp_price_threshold", 0.05),
        )
    if "lowp_follow_ratio_mult" in incoming:
        config["lowp_follow_ratio_mult"] = _coerce_float(
            incoming.get("lowp_follow_ratio_mult"),
            config.get("lowp_follow_ratio_mult", 0.02),
        )
    if "lowp_min_order_usd" in incoming:
        config["lowp_min_order_usd"] = _coerce_float(
            incoming.get("lowp_min_order_usd"),
            config.get("lowp_min_order_usd", 1.0),
        )
    if "lowp_max_order_usd" in incoming:
        config["lowp_max_order_usd"] = _coerce_float(
            incoming.get("lowp_max_order_usd"),
            config.get("lowp_max_order_usd", 2.0),
        )

    mirror_path = None
    if V3_CONFIG_PATH == default_source_path and _instance_mode_enabled():
        mirror_path = default_source_path
    _atomic_write_json_with_optional_mirror(write_path, config, mirror_path=mirror_path)
    return get_v3_settings_payload()


def get_v3_account_payload(index: int | None) -> Dict[str, Any]:
    accounts_payload = _read_v3_accounts_file()
    account_items = _list_v3_accounts(accounts_payload)
    if not account_items:
        return {
            "index": None,
            "name": "",
            "my_address": "",
            "private_key": "",
            "env_key_suffix": "",
            "follow_ratio": 1.0,
            "enabled": False,
            "max_notional_per_token": None,
            "max_notional_total": None,
        }
    safe_index = 0 if index is None else int(index)
    account = _get_v3_account(accounts_payload, safe_index)
    return {
        "index": safe_index,
        "name": str(account.get("name") or f"Account {safe_index + 1}"),
        "my_address": str(account.get("my_address") or ""),
        "private_key": str(account.get("private_key") or ""),
        "env_key_suffix": str(account.get("env_key_suffix") or ""),
        "follow_ratio": _coerce_float(account.get("follow_ratio"), 1.0),
        "enabled": _coerce_bool(account.get("enabled"), True),
        "max_notional_per_token": _normalize_optional_float(account.get("max_notional_per_token")),
        "max_notional_total": _normalize_optional_float(account.get("max_notional_total")),
    }


def save_v3_account_payload(index: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    _, write_path = _v3_accounts_paths()
    default_source_path = resolve_v3_source_root() / "accounts.json"
    accounts_payload = _read_v3_accounts_file()
    raw_accounts = accounts_payload.get("accounts")
    if not isinstance(raw_accounts, list):
        raise ValueError("accounts.json missing accounts array")
    account = _get_v3_account(accounts_payload, index)

    account["name"] = str(payload.get("name") or account.get("name") or "").strip()
    account["my_address"] = str(payload.get("my_address") or account.get("my_address") or "").strip()
    account["private_key"] = str(
        payload.get("private_key") or account.get("private_key") or ""
    ).strip()
    account["env_key_suffix"] = str(
        payload.get("env_key_suffix") or account.get("env_key_suffix") or ""
    ).strip()
    account["follow_ratio"] = _coerce_float(
        payload.get("follow_ratio"), account.get("follow_ratio", 1.0)
    )
    account["enabled"] = _coerce_bool(payload.get("enabled"), account.get("enabled", True))
    max_notional_per_token = _normalize_optional_float(payload.get("max_notional_per_token"))
    max_notional_total = _normalize_optional_float(payload.get("max_notional_total"))
    if max_notional_per_token is None:
        account.pop("max_notional_per_token", None)
    else:
        account["max_notional_per_token"] = max_notional_per_token
    if max_notional_total is None:
        account.pop("max_notional_total", None)
    else:
        account["max_notional_total"] = max_notional_total

    mirror_path = None
    if V3_ACCOUNTS_PATH == default_source_path and _instance_mode_enabled():
        mirror_path = default_source_path
    _atomic_write_json_with_optional_mirror(write_path, accounts_payload, mirror_path=mirror_path)
    return get_v3_account_payload(index)


def delete_v3_account_payload(index: int) -> Dict[str, Any]:
    _, write_path = _v3_accounts_paths()
    default_source_path = resolve_v3_source_root() / "accounts.json"
    accounts_payload = _read_v3_accounts_file()
    raw_accounts = accounts_payload.get("accounts")
    if not isinstance(raw_accounts, list):
        raise ValueError("accounts.json missing accounts array")
    if len(raw_accounts) <= 1:
        raise ValueError("at least one account must remain")
    if index < 0 or index >= len(raw_accounts):
        raise ValueError(f"invalid account index: {index}")

    raw_accounts.pop(index)
    mirror_path = None
    if V3_ACCOUNTS_PATH == default_source_path and _instance_mode_enabled():
        mirror_path = default_source_path
    _atomic_write_json_with_optional_mirror(write_path, accounts_payload, mirror_path=mirror_path)
    return get_v3_settings_payload()


def get_v3_runtime_payload() -> Dict[str, Any]:
    v3_root = V3_BASE_DIR if V3_BASE_DIR != resolve_v3_root() else resolve_v3_root()
    config = _read_json(_v3_config_paths()[0])
    accounts_payload = _read_v3_accounts_file()
    log_dir_value = str(config.get("log_dir") or "logs")
    log_dir = Path(log_dir_value)
    if not log_dir.is_absolute():
        log_dir = v3_root / log_dir
    log_path = _latest_log_path(log_dir, "copytrade_*.log")
    active_accounts = [
        item for item in _list_v3_accounts(accounts_payload) if item.get("enabled") is True
    ]
    return {
        "active_account_count": len(active_accounts),
        "target_address_count": len(_coerce_string_list(config.get("target_addresses"))),
        "copytrade_log_tail": _read_log_tail(log_path) if log_path else "",
        "log_file": str(log_path) if log_path else "",
    }


def get_trading_yaml_text() -> str:
    read_path, _ = _resolve_v2_paths(
        "POLYMARKET_MAKER_AUTO",
        "POLYMARKET_MAKER",
        "config",
        "trading.yaml",
    )
    if not read_path.exists():
        return ""
    data = yaml.safe_load(read_path.read_text(encoding="utf-8"))
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
