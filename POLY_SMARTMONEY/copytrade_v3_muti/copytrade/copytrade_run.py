from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smartmoney_query.api_client import DataApiClient

DEFAULT_CONFIG_PATH = Path(__file__).with_name("copytrade_config.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # Concurrent writers may expose a transient partial JSON; skip this round.
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        suffix=".tmp",
        prefix=".copytrade_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"copytrade_{time.strftime('%Y%m%d')}.log"

    logger = logging.getLogger("copytrade_run")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def _deep_find_first(value: Any, keys: Tuple[str, ...], max_depth: int = 4) -> Any:
    if max_depth < 0:
        return None
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key] is not None:
                return value[key]
        for child in value.values():
            found = _deep_find_first(child, keys, max_depth=max_depth - 1)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _deep_find_first(child, keys, max_depth=max_depth - 1)
            if found is not None:
                return found
    return None


def _normalize_trade(trade: Any) -> Optional[Dict[str, Any]]:
    raw_side = str(getattr(trade, "side", "") or "").upper()
    side = raw_side if raw_side in {"BUY", "SELL"} else None
    if side is None:
        return None
    size = float(getattr(trade, "size", 0.0) or 0.0)
    if size <= 0:
        return None

    raw = getattr(trade, "raw", {}) or {}
    token_id = (
        raw.get("tokenId")
        or raw.get("token_id")
        or raw.get("clobTokenId")
        or raw.get("clob_token_id")
        or raw.get("asset")
        or raw.get("assetId")
        or raw.get("asset_id")
        or raw.get("outcomeTokenId")
        or raw.get("outcome_token_id")
    )
    if token_id is None:
        token_id = _deep_find_first(
            raw,
            (
                "tokenId",
                "token_id",
                "clobTokenId",
                "clob_token_id",
                "asset",
                "assetId",
                "asset_id",
                "outcomeTokenId",
                "outcome_token_id",
            ),
        )
    if token_id is None:
        return None
    timestamp = getattr(trade, "timestamp", None)
    if timestamp is None:
        return None

    return {
        "token_id": str(token_id) if token_id is not None else None,
        "side": side,
        "size": size,
        "timestamp": timestamp,
    }


def _parse_last_seen(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    return None


def _load_token_state(path: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    payload = _load_json(path)
    tokens = payload.get("tokens") if isinstance(payload, dict) else []
    archived = payload.get("archived_tokens") if isinstance(payload, dict) else []
    mapping: Dict[str, Dict[str, Any]] = {}
    archived_mapping: Dict[str, Dict[str, Any]] = {}
    if not isinstance(tokens, list):
        tokens = []
    if not isinstance(archived, list):
        archived = []
    for item in tokens:
        if not isinstance(item, dict):
            continue
        token_id = item.get("token_id") or item.get("tokenId")
        if not token_id:
            continue
        key = str(token_id)
        entry = dict(item)
        entry.setdefault("introduced_by_buy", False)
        entry.setdefault("active", True)
        if not bool(entry.get("active", True)):
            archived_mapping[key] = entry
            continue
        mapping[key] = entry
    for item in archived:
        if not isinstance(item, dict):
            continue
        token_id = item.get("token_id") or item.get("tokenId")
        if not token_id:
            continue
        archived_mapping[str(token_id)] = dict(item)
    return mapping, archived_mapping


def _load_sell_signal_state(path: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    payload = _load_json(path)
    signals = payload.get("sell_tokens") if isinstance(payload, dict) else []
    archived = payload.get("archived_sell_tokens") if isinstance(payload, dict) else []
    mapping: Dict[str, Dict[str, Any]] = {}
    archived_mapping: Dict[str, Dict[str, Any]] = {}
    if not isinstance(signals, list):
        signals = []
    if not isinstance(archived, list):
        archived = []
    for item in signals:
        if not isinstance(item, dict):
            continue
        token_id = item.get("token_id") or item.get("tokenId")
        if not token_id:
            continue
        entry = dict(item)
        entry.setdefault("introduced_by_buy", False)
        entry.setdefault("active", True)
        entry.setdefault("status", "pending")
        try:
            entry["attempts"] = int(entry.get("attempts", 0) or 0)
        except (TypeError, ValueError):
            entry["attempts"] = 0
        status = str(entry.get("status") or "pending").strip().lower()
        if (not bool(entry.get("active", True))) or status in {"done", "stale_ignored", "canceled", "superseded"}:
            archived_mapping[str(token_id)] = entry
            continue
        mapping[str(token_id)] = entry
    for item in archived:
        if not isinstance(item, dict):
            continue
        token_id = item.get("token_id") or item.get("tokenId")
        if not token_id:
            continue
        archived_mapping[str(token_id)] = dict(item)
    return mapping, archived_mapping


def _write_sell_signals(
    path: Path,
    mapping: Dict[str, Dict[str, Any]],
    archived_mapping: Dict[str, Dict[str, Any]],
) -> None:
    def _sort_key(entry: Dict[str, Any]) -> float:
        ts = _parse_last_seen(entry.get("last_seen"))
        return ts.timestamp() if ts else 0.0

    tokens = sorted(mapping.values(), key=_sort_key, reverse=True)
    archived_tokens = sorted(archived_mapping.values(), key=_sort_key, reverse=True)
    payload = {
        "updated_at": _utc_now_iso(),
        "sell_tokens": tokens,
        "archived_sell_tokens": archived_tokens,
    }
    _write_json(path, payload)


def _write_tokens(
    path: Path,
    mapping: Dict[str, Dict[str, Any]],
    archived_mapping: Dict[str, Dict[str, Any]],
) -> None:
    def _sort_key(entry: Dict[str, Any]) -> float:
        ts = _parse_last_seen(entry.get("last_seen"))
        return ts.timestamp() if ts else 0.0

    tokens = [entry for entry in mapping.values() if bool(entry.get("introduced_by_buy", False))]
    tokens = sorted(tokens, key=_sort_key, reverse=True)
    archived_tokens = sorted(archived_mapping.values(), key=_sort_key, reverse=True)
    payload = {
        "updated_at": _utc_now_iso(),
        "tokens": tokens,
        "archived_tokens": archived_tokens,
    }
    _write_json(path, payload)


def _upsert_sell_signal(
    sell_map: Dict[str, Dict[str, Any]],
    token_id: str,
    entry: Dict[str, Any],
) -> bool:
    key = str(token_id)
    existing_sell = sell_map.get(key)
    existing_ts = _parse_last_seen(existing_sell.get("last_seen") if existing_sell else None)
    new_ts = _parse_last_seen(entry.get("last_seen"))
    if existing_ts is None or (new_ts and new_ts >= existing_ts):
        sell_map[key] = entry
        return True
    return False


def _promote_sell_signal_if_introduced(
    sell_map: Dict[str, Dict[str, Any]],
    token_id: str,
    *,
    last_seen: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> bool:
    key = str(token_id)
    entry = sell_map.get(key)
    if not entry:
        return False
    changed = False
    if not entry.get("introduced_by_buy", False):
        entry["introduced_by_buy"] = True
        changed = True
    status = str(entry.get("status") or "pending").strip().lower()
    if status == "deferred_wait_buy_introduction":
        entry["status"] = "pending"
        changed = True
    if last_seen:
        existing_ts = _parse_last_seen(entry.get("last_seen"))
        new_ts = _parse_last_seen(last_seen)
        if existing_ts is None or (new_ts and new_ts >= existing_ts):
            entry["last_seen"] = last_seen
            changed = True
    if changed:
        entry["active"] = True
        entry["updated_at"] = _utc_now_iso()
        if logger is not None:
            logger.info(
                "promote deferred sell signal after buy introduction: token=%s status=%s",
                key,
                entry.get("status"),
            )
    return changed


def _extract_position_token_id(entry: Dict[str, Any]) -> Optional[str]:
    for key in ("asset", "token_id", "tokenId", "asset_id", "assetId"):
        value = entry.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _extract_position_size(entry: Dict[str, Any]) -> float:
    for key in ("size", "position", "position_size", "amount", "shares", "balance"):
        value = entry.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _seed_existing_positions_on_init(
    *,
    client: DataApiClient,
    account: str,
    token_map: Dict[str, Dict[str, Any]],
    logger: logging.Logger,
) -> int:
    seeded = 0
    now_iso = _utc_now_iso()
    try:
        positions = client.fetch_positions(account, page_size=500, max_pages=5, size_threshold=0.0)
    except Exception as exc:
        logger.warning("seed existing positions failed: account=%s error=%s", account, exc)
        return 0

    for row in positions:
        if not isinstance(row, dict):
            continue
        token_id = _extract_position_token_id(row)
        if not token_id:
            continue
        if _extract_position_size(row) <= 0:
            continue
        existing = token_map.get(token_id)
        if existing and existing.get("introduced_by_buy", False):
            continue
        token_map[token_id] = {
            "token_id": token_id,
            "source_account": account,
            "last_seen": (existing or {}).get("last_seen") or now_iso,
            "active": True,
            "introduced_by_buy": True,
            "seeded_on_init": True,
        }
        seeded += 1

    if seeded:
        logger.info("seed existing positions on init: account=%s count=%s", account, seeded)
    return seeded


def _archive_record(
    active_mapping: Dict[str, Dict[str, Any]],
    archived_mapping: Dict[str, Dict[str, Any]],
    token_id: str,
    *,
    reason: str,
    source: str,
    status: Optional[str] = None,
) -> bool:
    entry = dict(active_mapping.pop(token_id, {}) or archived_mapping.get(token_id) or {})
    if not entry:
        return False
    now_iso = _utc_now_iso()
    entry["active"] = False
    entry["invalidated_at"] = now_iso
    entry["invalidate_reason"] = str(reason)
    entry["invalidate_source"] = str(source)
    entry["updated_at"] = now_iso
    if status is not None:
        entry["status"] = str(status)
    archived_mapping[token_id] = entry
    return True


def _load_blacklist_tokens(path: Path) -> set[str]:
    payload = _load_json(path)
    rows = payload.get("tokens") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return set()
    out: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        token_id = item.get("token_id") or item.get("tokenId")
        if token_id is not None and str(token_id).strip():
            out.add(str(token_id).strip())
    return out


def _collect_trades(
    client: DataApiClient,
    account: str,
    since_ms: int,
    min_size: float,
    logger: logging.Logger,
) -> Tuple[List[Dict[str, Any]], int]:
    start_dt = datetime.fromtimestamp(max(since_ms, 0) / 1000.0, tz=timezone.utc)
    trades = client.fetch_trades(account, start_time=start_dt, page_size=500, max_pages=5)
    actions: List[Dict[str, Any]] = []
    latest_ms = since_ms

    for trade in trades:
        raw_side = str(getattr(trade, "side", "") or "").upper()
        if raw_side and raw_side not in {"BUY", "SELL"}:
            logger.warning(
                "skip trade with unsupported side: account=%s side=%s",
                account,
                raw_side,
            )
        normalized = _normalize_trade(trade)
        if normalized is None:
            continue
        if normalized["size"] < min_size:
            continue
        ts_ms = int(normalized["timestamp"].timestamp() * 1000)
        if ts_ms <= since_ms:
            continue
        latest_ms = max(latest_ms, ts_ms)
        actions.append(normalized)

    logger.info(
        "account=%s trades=%s normalized=%s since_ms=%s",
        account,
        len(trades),
        len(actions),
        since_ms,
    )
    return actions, latest_ms


def run_once(
    config: Dict[str, Any],
    *,
    base_dir: Path,
    client: DataApiClient,
    logger: logging.Logger,
) -> None:
    poll_targets = config.get("targets") or []
    if not isinstance(poll_targets, list):
        raise ValueError("targets 必须是数组")

    token_output_path = base_dir / "tokens_from_copytrade.json"
    sell_signal_path = base_dir / "copytrade_sell_signals.json"
    state_path = base_dir / "copytrade_state.json"
    blacklist_path = Path(config.get("blacklist_path") or (base_dir / "liquidation_blacklist.json"))

    state = _load_json(state_path)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("targets", {})

    token_map, archived_token_map = _load_token_state(token_output_path)
    sell_map, archived_sell_map = _load_sell_signal_state(sell_signal_path)
    blacklist_tokens = _load_blacklist_tokens(blacklist_path)

    now_ms = int(time.time() * 1000)
    initial_lookback_sec = max(0.0, float(config.get("initial_lookback_sec", 3600) or 0.0))
    initial_lookback_ms = int(initial_lookback_sec * 1000.0)
    changed = False
    sell_changed = False

    for token_id in list(token_map.keys()):
        entry = token_map.get(token_id) or {}
        if token_id in blacklist_tokens or not bool(entry.get("introduced_by_buy", False)):
            changed = _archive_record(
                token_map,
                archived_token_map,
                token_id,
                reason=(
                "blacklist" if token_id in blacklist_tokens else "not_introduced_by_buy"
                ),
                source="run_once_preflight",
            ) or changed

    for token_id, entry in list(sell_map.items()):
        if token_id in blacklist_tokens:
            sell_changed = _archive_record(
                sell_map,
                archived_sell_map,
                token_id,
                reason="blacklist",
                source="run_once_preflight",
                status="stale_ignored",
            ) or sell_changed
            continue
        token_entry = token_map.get(token_id)
        status = str(entry.get("status") or "pending").strip().lower()
        if not token_entry or not token_entry.get("introduced_by_buy", False):
            if status == "deferred_wait_buy_introduction":
                continue
            sell_changed = _archive_record(
                sell_map,
                archived_sell_map,
                token_id,
                reason="missing_buy_introduction",
                source="run_once_preflight",
                status="stale_ignored",
            ) or sell_changed
            continue
        sell_changed = (
            _promote_sell_signal_if_introduced(
                sell_map,
                token_id,
                last_seen=token_entry.get("last_seen"),
                logger=logger,
            )
            or sell_changed
        )

    for target in poll_targets:
        if not isinstance(target, dict):
            continue
        if target.get("enabled", True) is False:
            continue
        account = str(target.get("account") or "").strip()
        if not account:
            continue
        min_size = float(target.get("min_size", 0.0) or 0.0)
        target_state = state["targets"].get(account, {})
        since_ms = int(target_state.get("last_timestamp_ms") or 0)
        if since_ms <= 0:
            init_ms = max(0, now_ms - initial_lookback_ms)
            state["targets"][account] = {
                "last_timestamp_ms": init_ms,
                "updated_at": _utc_now_iso(),
            }
            logger.info("初始化目标账户状态，忽略已有仓位: account=%s", account)
            since_ms = init_ms
            changed = (
                _seed_existing_positions_on_init(
                    client=client,
                    account=account,
                    token_map=token_map,
                    logger=logger,
                )
                > 0
            ) or changed

        actions, latest_ms = _collect_trades(client, account, since_ms, min_size, logger)
        if latest_ms > since_ms:
            state["targets"][account] = {
                "last_timestamp_ms": latest_ms,
                "updated_at": _utc_now_iso(),
            }
        for action in actions:
            token_id = action.get("token_id")
            if not token_id:
                continue
            side = str(action.get("side") or "").upper()
            key = str(token_id)
            if key in blacklist_tokens:
                logger.info("skip blacklisted token action: account=%s token=%s side=%s", account, key, action.get("side"))
                continue

            last_seen = action["timestamp"].astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
            existing = token_map.get(key)
            existing_intro = bool(existing and existing.get("introduced_by_buy", False))
            if side == "SELL" and not existing_intro:
                sell_entry = {
                    "token_id": token_id,
                    "source_account": account,
                    "last_seen": last_seen,
                    "signal_ts": float(action["timestamp"].timestamp()),
                    "introduced_by_buy": False,
                    "active": True,
                    "status": "deferred_wait_buy_introduction",
                    "attempts": 0,
                }
                sell_changed = _upsert_sell_signal(sell_map, key, sell_entry) or sell_changed
                logger.info(
                    "defer sell signal before buy introduction: account=%s token=%s",
                    account,
                    token_id,
                )
                continue
            new_entry = {
                "token_id": token_id,
                "source_account": account,
                "last_seen": last_seen,
                "active": True,
            }
            # 保留 existing 的 introduced_by_buy 标记，避免被覆盖丢失
            if existing and existing.get("introduced_by_buy", False):
                new_entry["introduced_by_buy"] = True
            if existing and existing.get("seeded_on_init", False):
                new_entry["seeded_on_init"] = True

            if existing:
                existing_ts = _parse_last_seen(existing.get("last_seen"))
                new_ts = _parse_last_seen(last_seen)
                if existing_ts is None or (new_ts and new_ts >= existing_ts):
                    token_map[key] = new_entry
                    changed = True
            else:
                token_map[key] = new_entry
                changed = True

            if action.get("side") == "BUY":
                if not token_map[key].get("introduced_by_buy", False):
                    token_map[key]["introduced_by_buy"] = True
                    changed = True
                if token_map[key].get("seeded_on_init", False):
                    token_map[key]["seeded_on_init"] = False
                    changed = True
                sell_changed = (
                    _promote_sell_signal_if_introduced(
                        sell_map,
                        key,
                        last_seen=last_seen,
                        logger=logger,
                    )
                    or sell_changed
                )

            if side == "SELL":
                sell_entry = {
                    "token_id": token_id,
                    "source_account": account,
                    "last_seen": last_seen,
                    "signal_ts": float(action["timestamp"].timestamp()),
                    "introduced_by_buy": True,
                    "active": True,
                    "status": "pending",
                    "attempts": 0,
                }
                sell_changed = _upsert_sell_signal(sell_map, key, sell_entry) or sell_changed

    if changed:
        _write_tokens(token_output_path, token_map, archived_token_map)
        logger.info("tokens output updated: %s (total=%s)", token_output_path, len(token_map))
    else:
        logger.info("no token updates, total=%s", len(token_map))
        _write_tokens(token_output_path, token_map, archived_token_map)

    if sell_changed:
        _write_sell_signals(sell_signal_path, sell_map, archived_sell_map)
        logger.info(
            "sell signal output updated: %s (total=%s)",
            sell_signal_path,
            len(sell_map),
        )
    else:
        _write_sell_signals(sell_signal_path, sell_map, archived_sell_map)

    _write_json(state_path, state)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copytrade watcher")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="copytrade_config.json 路径",
    )
    parser.add_argument("--once", action="store_true", help="仅执行一次抓取")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    config_path = args.config
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    config = _load_json(config_path)
    if not isinstance(config, dict):
        raise ValueError("配置文件必须是 JSON 对象")

    base_dir = config_path.parent
    log_dir = base_dir / "logs"
    logger = _setup_logger(log_dir)

    poll_interval = float(config.get("poll_interval_sec", 30))
    client = DataApiClient()

    logger.info("copytrade 启动 | poll_interval=%ss", poll_interval)
    while True:
        try:
            run_once(config, base_dir=base_dir, client=client, logger=logger)
        except Exception as exc:
            logger.exception("copytrade 运行异常: %s", exc)
        if args.once:
            break
        time.sleep(max(1.0, poll_interval))


if __name__ == "__main__":
    main()
