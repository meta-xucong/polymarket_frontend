"""
poly_maker_autorun
-------------------

基础骨架：配置加载、主循环、命令/交互入口。
当前版本通过 copytrade 产出的 token 文件驱动话题调度。
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import queue
import re
import requests
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from total_liquidation_manager import TotalLiquidationManager

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    class _FcntlCompat:
        LOCK_EX = 0
        LOCK_UN = 0

        @staticmethod
        def flock(_fd: int, _op: int) -> None:
            return

    fcntl = _FcntlCompat()  # type: ignore[assignment]

# =====================
# 配置与常量
# =====================
def _resolve_project_root() -> Path:
    override = os.getenv("POLY_APP_ROOT")
    if override:
        override_root = Path(override).resolve()
        candidates = (
            override_root / "POLYMARKET_MAKER_copytrade_v3" / "POLYMARKET_MAKER_AUTO",
            override_root
            / "POLYMARKET_MAKER_copytrade"
            / "POLYMARKET_MAKER_copytrade_v3"
            / "POLYMARKET_MAKER_AUTO",
            override_root / "POLYMARKET_MAKER_copytrade_v2" / "POLYMARKET_MAKER_AUTO",
            override_root
            / "POLYMARKET_MAKER_copytrade"
            / "POLYMARKET_MAKER_copytrade_v2"
            / "POLYMARKET_MAKER_AUTO",
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return Path(__file__).resolve().parent


PROJECT_ROOT = _resolve_project_root()
MAKER_ROOT = PROJECT_ROOT / "POLYMARKET_MAKER"
if str(MAKER_ROOT) not in sys.path:
    sys.path.insert(0, str(MAKER_ROOT))
REPO_ROOT = PROJECT_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# =====================
# 市场状态检测模块（新增）- 必须在 sys.path 修改后导入
# =====================
try:
    from market_state_checker import (
        MarketStateChecker,
        MarketClosedCleaner,
        MarketStatus,
        MarketState,
        check_market_state,
        clean_closed_market,
        should_refill_token,
        init_market_state_checker,
        init_cleaner,
    )
    MARKET_STATE_CHECKER_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] market_state_checker 模块导入失败: {e}")
    MARKET_STATE_CHECKER_AVAILABLE = False

from runtime_position_truth import (
    POSITION_TRUTH_ACTIONABLE,
    POSITION_TRUTH_DUST,
    classify_position_truth,
    extract_market_min_order_size,
    is_position_truth_terminal,
)

DEFAULT_GLOBAL_CONFIG = {
    "copytrade_poll_sec": 30.0,
    "command_poll_sec": 5.0,
    "max_concurrent_tasks": 10,
    "max_exit_cleanup_tasks": 3,  # 清仓任务独立槽位，不受 max_concurrent_tasks 限制
    "log_dir": str(PROJECT_ROOT / "logs" / "autorun"),
    "data_dir": str(PROJECT_ROOT / "data"),
    "handled_topics_path": str(PROJECT_ROOT / "data" / "handled_topics.json"),
    "copytrade_tokens_path": str(
        PROJECT_ROOT.parent / "copytrade" / "tokens_from_copytrade.json"
    ),
    "copytrade_sell_signals_path": str(
        PROJECT_ROOT.parent / "copytrade" / "copytrade_sell_signals.json"
    ),
    "copytrade_blacklist_path": str(
        PROJECT_ROOT.parent / "copytrade" / "liquidation_blacklist.json"
    ),
    "process_start_retries": 1,
    "process_retry_delay_sec": 2.0,
    "process_graceful_timeout_sec": 5.0,
    "process_stagger_max_sec": 3.0,
    "topic_start_cooldown_sec": 5.0,
    "active_unmanaged_rearm_cooldown_sec": 900.0,
    "active_unmanaged_rearm_budget_window_sec": 3600.0,
    "active_unmanaged_rearm_budget_max_attempts": 3,
    "active_unmanaged_rearm_budget_suppress_sec": 7200.0,
    "log_excerpt_interval_sec": 15.0,
    "runtime_status_path": str(PROJECT_ROOT / "data" / "autorun_status.json"),
    "token_cycle_state_path": str(PROJECT_ROOT / "data" / "token_cycle_gate.json"),
    "ws_debug_raw": False,
    # Slot refill (回填) 配置
    "enable_slot_refill": True,
    "refill_cooldown_minutes": 5.0,  # 兼容旧配置：等价于有持仓冷却
    "refill_cooldown_minutes_with_position": 5.0,
    "refill_cooldown_minutes_no_position": 20.0,
    "max_refill_retries": 6,
    "refill_check_interval_sec": 60.0,
    # 单阈值两步止损（首次50%，冷却后仍未恢复则全清）
    "stoploss": {
        "enabled": True,
        "check_interval_sec": 60.0,
        "drawdown_pct": 0.20,
        "base_drawdown_pct": 0.05,
        "aggressive_drawdown_pct": 0.05,
        "aggressive_retry_cooldown_minutes": 10.0,
        "reentry_window_cooldown_minutes": 120.0,
        "next_stoploss_cooldown_minutes": 30.0,
        "reentry_extra_delay_after_5_cuts_hours": 24.0,
        "reentry_timeout_hours": 168.0,
        "reference_tick_size": 0.01,
        "reentry_line_ticks": 2,
        "reentry_zone_lower_pct": 0.02,
        "probe_break_pct": 0.08,
        "drawdown_step_per_cycle_ticks": 1,
        "clear_confirm_timeout_sec": 180.0,
        "clear_confirm_retry_interval_sec": 1800.0,
        "clear_confirm_max_retries": 3,
        "daily_reentry_max_full_clears": 2,
        "drawdown_step_per_cycle_pct": 0.01,
        "min_age_minutes": 5.0,
        "confirm_rounds": 2,
        "min_maker_order_size": 5.0,
        "max_tokens_per_cycle": 3,
        "skip_if_global_liquidation_active": True,
    },
    # gap 快速淘汰后指数退避（分钟）：连续第1/2/3/4+次使用 5/15/45/120 分钟
    "gap_skip_backoff_enabled": True,
    "gap_skip_backoff_minutes": [5.0, 15.0, 45.0, 120.0],
    "inactive_token_reconcile_interval_sec": 1200.0,
    # Startup orphan position profit sweep:
    # tokens not tracked by copytrade but currently profitable and holding position
    "startup_orphan_profit_sweep_enabled": True,
    "startup_orphan_profit_sweep_min_profit_pct": 0.01,
    "startup_orphan_profit_sweep_skip_if_open_sell": True,
    # orphan token 自动恢复探测（仅恢复到 maker 管理，不触发 IOC 清仓）
    "orphan_recovery_enabled": True,
    "orphan_recovery_probe_interval_sec": 600.0,
    "orphan_recovery_max_backoff_sec": 7200.0,
    "orphan_recovery_max_attempts": 8,
    # 启动前买入硬门控：position=0 且 open_sell=0 连续命中 N 次才允许 BUY
    "startup_double_zero_confirm_hits": 2,
    "startup_double_zero_probe_interval_sec": 1.0,
    # Pending 软淘汰（避免无数据 token 长期卡在 pending）
    "enable_pending_soft_eviction": True,
    "pending_soft_eviction_minutes": 45.0,
    "pending_soft_eviction_check_interval_sec": 300.0,
    # 低余额买入暂停（M 阈值）：余额低于阈值时仅保留卖出路径，停止启动新买入任务
    "buy_pause_min_free_balance": 0.0,
    "buy_pause_balance_poll_interval_sec": 120.0,
    # 卡顿剔除（命中后会终止子进程并立即回填）
    "price_cap_refill_stuck_minutes": 120.0,
    "shock_refill_stuck_minutes": 45.0,
    # Shared WS 等待配置
    "shared_ws_max_pending_wait_sec": 20.0,
    "shared_ws_wait_poll_sec": 0.5,
    "shared_ws_wait_failures_before_pause": 3,
    "shared_ws_wait_pause_minutes": 1.0,
    "shared_ws_wait_escalation_window_sec": 240.0,
    "shared_ws_wait_escalation_min_failures": 2,
    "ws_no_event_warn_interval_sec": 30.0,
    "ws_cache_flush_min_interval_sec": 0.5,  # 限制脏缓存刷盘频率，避免高负载
    "ws_silence_timeout_sec": 1200.0,  # WS静默重连阈值（默认20分钟，降低低活跃误重连）
    "ws_custom_feature_enabled": True,  # 订阅 custom feature，启用 best_bid_ask/new_market 等事件
    "ws_subscribe_chunk_size": 20,  # WS订阅分片大小（当前负载下更保守，降低首包/增量包体积）
    "ws_subscribe_chunk_interval_ms": 150.0,  # WS分片发送间隔（毫秒）
    "ws_ready_use_confirmed": True,  # 允许以 WS confirmed 状态作为 ready 信号
    "ws_ready_confirm_grace_sec": 2.0,  # confirmed-ready 过渡窗口（秒）
    "ws_unsubscribe_grace_sec": 120.0,  # token离开调度后保留订阅宽限，降低订阅抖动
    "ws_ping_interval_sec": 20.0,  # 兼容字段：当前主要依赖应用层 text PING，每10秒发送一次
    "ws_ping_timeout_sec": 10.0,  # 兼容字段：保留配置面，当前未启用 websocket-client 协议层 ping
    "ws_enable_text_ping": True,  # 应用层文本 PING（Polymarket 官方建议每 10 秒）
    "exit_start_stagger_sec": 1.0,  # 清仓进程错峰启动间隔（秒）
    # 可选策略模式（classic/aggressive）
    "strategy_mode": "classic",
    "burst_slots": 10,
    "enable_burst_queue": True,
    "enable_reentry": True,
    "exhausted_keep_orders": True,
    "exhausted_enable_reentry": True,
    "aggressive_reentry_source": "self_account_fills_only",
    "aggressive_first_sell_fill_only": True,
    "aggressive_sell_fill_poll_sec": 15.0,
    "aggressive_burst_slots": 10,
    "aggressive_enable_self_sell_reentry": False,
    "classic_keep_orders_price_threshold": 0.8,
    "title_blacklist": {
        "enabled": False,
        "keywords": [],
        "match_on_slug": True,
        # liquidate: 走清仓通道（快）
        # sell_only_maker: 启动仅卖出 maker，不再买入（损失更小）
        "action_with_position": "sell_only_maker",
    },
    # 全局总清仓（默认关闭）
    "total_liquidation": {
        "enable_total_liquidation": False,
        "execution_mode": "restart_only",  # liquidate_then_restart: 清仓+可选硬重置+重启; restart_only: 仅重启
        "min_interval_hours": 72.0,
        "trigger": {
            "idle_slot_ratio_threshold": 0.5,
            "idle_slot_duration_minutes": 120.0,
            "startup_grace_hours": 6.0,
            "no_trade_duration_minutes": 180.0,
            "min_free_balance": 20.0,
            "low_balance_force_hours": 6.0,
            "enable_low_balance_force_trigger": True,
            "balance_poll_interval_sec": 120.0,
            "require_conditions": 2,
        },
        "liquidation": {
            "token_scope_mode": "copytrade",  # copytrade: 仅清仓copytrade记录token; all_positions: 清仓全部持仓token
            "position_value_threshold": 3.0,
            "spread_threshold": 0.01,
            "maker_timeout_minutes": 20.0,
            "taker_slippage_bps": 30.0,
        },
        "reset": {
            "hard_reset_enabled": False,
            "remove_logs": False,
            "remove_json_state": False,
        },
        "blacklist": {
            "enabled": True,
        },
    },
    # Maker 子进程配置
    "maker_poll_sec": 15.0,
    "maker_position_sync_interval": 60.0,
}

# Shared WS 等待防抖参数（写死，避免依赖外部 JSON）
SHARED_WS_WAIT_ESCALATION_WINDOW_SEC = 240.0
SHARED_WS_WAIT_ESCALATION_MIN_FAILURES = 2
PRICE_CAP_FLAT_STUCK_LIMIT_SEC = 600.0
ORDER_SIZE_DECIMALS = 4  # Polymarket 下单数量精度（按买单精度取整）
DATA_API_ROOT = os.getenv("POLY_DATA_API_ROOT", "https://data-api.polymarket.com")
CLOB_API_ROOT = os.getenv("POLY_CLOB_API_ROOT", "https://clob.polymarket.com")
GAMMA_API_ROOT = os.getenv("POLY_GAMMA_API_ROOT", "https://gamma-api.polymarket.com")
POSITION_CHECK_CACHE_TTL_SEC = 600.0
# 低余额筛查场景下：无持仓 token 启动时检查一次，之后每 30 分钟再复检，
# 避免在 copytrade 轮询周期（30s）下重复高频查询。
POSITION_CHECK_NEGATIVE_CACHE_TTL_SEC = 1800.0
SELL_SIGNAL_REPLAY_WARMUP_SEC = 300.0
SOURCE_DETACHED_HOLD_SEC = 20 * 60.0
EXIT_ORDERBOOK_MISSING_BACKOFF_SCHEDULE_SEC = (5 * 60.0, 15 * 60.0, 30 * 60.0)
POSITION_TRUTH_FALLBACK_MIN_ORDER_SIZE = 0.5
EXIT_CLEANUP_MAX_RETRIES = 3
STOPLOSS_NOFILL_SELL_ONLY_MAX_REQUEUES = 3
SELL_SIGNAL_FORCE_CLEANUP_TIMEOUT_SEC = 45.0
ALLOWED_IOC_EXIT_REASONS = {"COPYTRADE_SELL", "STOPLOSS_REENTRY"}
ALLOWED_EXIT_SIGNAL_REASONS = ALLOWED_IOC_EXIT_REASONS | {"TOTAL_LIQUIDATION"}
DATA_API_RATE_LIMIT_SEC = 1.0
DATA_API_TIMEOUT_SEC = 10.0
DATA_API_POSITION_RETRY_ATTEMPTS = 3
DATA_API_TRADE_RETRY_ATTEMPTS = 3
DATA_API_RETRY_BACKOFF_SEC = (1.0, 2.0, 4.0)
_data_api_last_request_ts = 0.0
_data_api_request_lock = threading.Lock()
CLOB_BOOK_PROBE_TIMEOUT_SEC = 5.0
CLOB_BOOK_PROBE_CACHE_TTL_SEC = 300.0
GAMMA_MARKET_PROBE_TIMEOUT_SEC = 10.0
GAMMA_MARKET_CACHE_TTL_SEC = 300.0


class _TeeStream:
    def __init__(self, primary: Any, secondary: Any) -> None:
        self._primary = primary
        self._secondary = secondary
        self._lock = threading.Lock()

    def write(self, data: str) -> int:
        with self._lock:
            written = self._primary.write(data)
            self._secondary.write(data)
            self._secondary.flush()
            return written

    def flush(self) -> None:
        with self._lock:
            self._primary.flush()
            self._secondary.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._primary, "isatty", lambda: False)())

    def fileno(self) -> int:
        return int(getattr(self._primary, "fileno", lambda: -1)())


def _setup_main_log(log_dir: Path) -> Optional[Path]:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        filename = time.strftime("autorun_main_%Y%m%d_%H%M%S.log", time.localtime())
        log_path = log_dir / filename
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = _TeeStream(sys.stdout, log_file)
        sys.stderr = _TeeStream(sys.stderr, log_file)
        return log_path
    except Exception as exc:
        print(f"[WARN] 无法创建主程序日志文件: {exc}")
        return None


# ========== 错误日志记录函数 ==========
def _log_error(error_type: str, error_data: Dict[str, Any]) -> None:
    """
    记录错误到独立的错误日志文件。

    :param error_type: 错误类型标识（如 WS_AGGREGATOR_ERROR, TASK_START_ERROR 等）
    :param error_data: 错误相关数据（字典格式）
    """
    try:
        # 确定错误日志文件路径
        log_dir = PROJECT_ROOT / "logs" / "autorun"
        log_dir.mkdir(parents=True, exist_ok=True)
        error_log_path = log_dir / "error_log.txt"

        # 构建日志条目
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        log_entry = {
            "timestamp": timestamp,
            "error_type": error_type,
            "data": error_data
        }

        # 追加写入日志文件
        with open(error_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    except Exception as e:
        # 错误日志记录失败时，仅打印到控制台，不中断程序
        print(f"[ERROR_LOG] 写入错误日志失败: {e}")


def _atomic_json_write(path: Path, data: Any) -> None:
    """原子写入 JSON 文件：先写临时文件，再 rename，避免与外部进程竞态导致数据丢失。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".copytrade_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Use replace for cross-platform atomic overwrite (Windows rename fails if target exists).
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _resolve_position_address_from_env() -> tuple[Optional[str], str]:
    """按官方 Data API 用法解析 user 地址（Proxy/Deposit 地址）。"""
    env_candidates = (
        "POLY_DATA_ADDRESS",
        "POLY_FUNDER",
    )
    for env_name in env_candidates:
        cand = os.getenv(env_name)
        if cand and str(cand).strip():
            return str(cand).strip(), f"env:{env_name}"
    return None, "缺少地址，无法从 data-api /positions 查询持仓。"


def _position_matches_token(entry: Dict[str, Any], token_id: str) -> bool:
    """官方 /positions 返回字段使用 asset 表示 token id。"""
    asset = entry.get("asset")
    return asset is not None and str(asset) == token_id


def _extract_position_token_id(entry: Dict[str, Any]) -> Optional[str]:
    """官方 /positions 返回字段使用 asset 表示 token id。"""
    val = entry.get("asset")
    if val is None:
        return None
    token_id = str(val).strip()
    return token_id or None


def _normalize_unix_ts_seconds(ts_raw: Any) -> int:
    """将秒/毫秒/微秒/纳秒时间戳统一归一到秒级 Unix 时间戳。"""
    try:
        ts = int(float(ts_raw)) if ts_raw is not None else 0
    except (TypeError, ValueError):
        return 0
    if ts <= 0:
        return 0
    # 1e11 秒已明显超出常规业务时间范围，通常说明是 ms/us/ns。
    while ts > 100_000_000_000:
        ts //= 1000
    return ts


def _extract_position_size(entry: Dict[str, Any]) -> Optional[float]:
    """官方 /positions 返回字段使用 size。"""
    val = entry.get("size")
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract_position_avg_price(entry: Dict[str, Any]) -> Optional[float]:
    for key in ("avgPrice", "avg_price", "averagePrice", "entryPrice", "entry_price"):
        val = entry.get(key)
        if val is None or isinstance(val, bool):
            continue
        try:
            out = float(val)
            if out >= 0:
                return out
        except (TypeError, ValueError):
            continue
    return None


def _extract_position_current_price(entry: Dict[str, Any]) -> Optional[float]:
    for key in ("curPrice", "currentPrice", "markPrice", "price", "lastPrice"):
        val = entry.get(key)
        if val is None or isinstance(val, bool):
            continue
        try:
            out = float(val)
            if out >= 0:
                return out
        except (TypeError, ValueError):
            continue
    return None


def _data_api_get_json_with_retry(
    url: str,
    *,
    params: Dict[str, Any],
    attempts: int,
    timeout_sec: float,
    label: str,
) -> tuple[Optional[Any], str]:
    last_err = ""
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            with _data_api_request_lock:
                global _data_api_last_request_ts
                now = time.time()
                wait_sec = DATA_API_RATE_LIMIT_SEC - (now - _data_api_last_request_ts)
                if wait_sec > 0:
                    time.sleep(wait_sec)
                _data_api_last_request_ts = time.time()

            resp = requests.get(url, params=params, timeout=max(1.0, float(timeout_sec)))
            if resp.status_code == 404:
                return None, "404"
            # Retry known transient statuses before raise_for_status.
            if resp.status_code in {429, 500, 502, 503, 504}:
                last_err = f"{label} transient http {resp.status_code} (attempt={attempt}/{attempts})"
                if attempt < attempts:
                    idx = min(attempt - 1, len(DATA_API_RETRY_BACKOFF_SEC) - 1)
                    time.sleep(max(0.1, float(DATA_API_RETRY_BACKOFF_SEC[idx])))
                    continue
                return None, last_err
            resp.raise_for_status()
            payload = resp.json()
            return payload, "ok"
        except ValueError:
            last_err = f"{label} decode failed (attempt={attempt}/{attempts})"
        except Exception as exc:
            last_err = f"{label} request failed (attempt={attempt}/{attempts}): {exc.__class__.__name__}: {exc}"

        if attempt < attempts:
            idx = min(attempt - 1, len(DATA_API_RETRY_BACKOFF_SEC) - 1)
            time.sleep(max(0.1, float(DATA_API_RETRY_BACKOFF_SEC[idx])))
    return None, (last_err or f"{label} request failed")


def _fetch_position_rows_from_data_api(address: str) -> tuple[List[Dict[str, Any]], str]:
    if not address:
        return [], "missing address"
    url = f"{DATA_API_ROOT}/positions"
    limit = 500
    offset = 0
    rows: List[Dict[str, Any]] = []
    while True:
        params = {
            "user": address,
            "limit": limit,
            "offset": offset,
            "sizeThreshold": 0,
        }
        payload, info = _data_api_get_json_with_retry(
            url,
            params=params,
            attempts=DATA_API_POSITION_RETRY_ATTEMPTS,
            timeout_sec=DATA_API_TIMEOUT_SEC,
            label="positions",
        )
        if info == "404":
            return [], "positions 404"
        if info != "ok":
            return [], info
        if not isinstance(payload, list):
            return [], "positions payload invalid"
        positions = payload
        for pos in positions:
            if isinstance(pos, dict):
                rows.append(pos)
        if not positions:
            break
        offset += len(positions)
    return rows, "ok"


def _fetch_position_size_from_data_api(
    address: str,
    token_id: str,
) -> tuple[Optional[float], str]:
    if not address:
        return None, "缺少地址，无法查询持仓。"
    url = f"{DATA_API_ROOT}/positions"
    limit = 500
    offset = 0
    while True:
        params = {
            "user": address,
            "limit": limit,
            "offset": offset,
            "sizeThreshold": 0,
        }
        payload, info = _data_api_get_json_with_retry(
            url,
            params=params,
            attempts=DATA_API_POSITION_RETRY_ATTEMPTS,
            timeout_sec=DATA_API_TIMEOUT_SEC,
            label="positions",
        )
        if info == "404":
            return None, "数据接口返回 404（请确认使用 Proxy/Deposit 地址查询 user 参数）"
        if info != "ok":
            return None, f"数据接口请求失败：{info}"

        if not isinstance(payload, list):
            return None, "数据接口返回格式异常：/positions 应返回列表。"
        positions = payload

        for pos in positions:
            if not isinstance(pos, dict):
                continue
            if not _position_matches_token(pos, token_id):
                continue
            pos_size = _extract_position_size(pos)
            return pos_size, "ok"

        if not positions:
            break

        offset += len(positions)
    return None, "未找到持仓记录"


def _fetch_recent_trades_from_data_api(
    address: str,
    *,
    limit: int = 200,
) -> tuple[List[Dict[str, Any]], str]:
    if not address:
        return [], "缺少地址，无法查询成交。"
    url = f"{DATA_API_ROOT}/trades"
    params = {
        "user": address,
        "limit": max(1, min(int(limit), 500)),
    }

    last_err = ""
    attempts = max(1, int(DATA_API_TRADE_RETRY_ATTEMPTS))
    for attempt in range(1, attempts + 1):
        try:
            with _data_api_request_lock:
                global _data_api_last_request_ts
                now = time.time()
                wait_sec = DATA_API_RATE_LIMIT_SEC - (now - _data_api_last_request_ts)
                if wait_sec > 0:
                    time.sleep(wait_sec)
                _data_api_last_request_ts = time.time()
            resp = requests.get(url, params=params, timeout=DATA_API_TIMEOUT_SEC)
            if resp.status_code == 404:
                return [], "数据接口返回 404（请确认使用 Proxy/Deposit 地址查询 user 参数）"
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            last_err = (
                f"数据接口请求失败(attempt={attempt}/{attempts})："
                f"{exc.__class__.__name__}: {exc}"
            )
        except ValueError:
            last_err = f"数据接口响应解析失败(attempt={attempt}/{attempts})"
        else:
            if not isinstance(payload, list):
                return [], "数据接口返回格式异常：/trades 应返回列表。"
            out: List[Dict[str, Any]] = []
            for row in payload:
                if not isinstance(row, dict):
                    continue
                out.append(row)
            return out, "ok"

        if attempt < attempts:
            idx = min(attempt - 1, len(DATA_API_RETRY_BACKOFF_SEC) - 1)
            backoff = max(0.1, float(DATA_API_RETRY_BACKOFF_SEC[idx]))
            time.sleep(backoff)

    return [], (last_err or "数据接口请求失败")


def _topic_id_from_entry(entry: Any) -> str:
    """从 copytrade token 条目中提取 token_id。"""

    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        token_id = entry.get("token_id") or entry.get("tokenId")
        if isinstance(token_id, str) and token_id.strip():
            return token_id.strip()
        return ""
    return str(entry).strip()


def _safe_topic_filename(topic_id: str) -> str:
    return topic_id.replace("/", "_").replace("\\", "_")


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            raw = value.replace(",", "").strip()
            if not raw:
                return None
            return float(raw)
    except Exception:
        return None
    return None


def _normalize_ws_event_timestamp(value: Any, *, now: Optional[float] = None) -> float:
    current = time.time() if now is None else float(now)
    ts = _coerce_float(value)
    if ts is None or ts <= 0.0:
        return current
    if ts > current + 86400.0:
        return current
    return float(ts)


def _extract_top_price_from_levels(levels: Any, side: str) -> Optional[float]:
    """从订单簿档位中提取最优价格（兼容 dict/list 结构）。

    注意：WS book 事件的档位列表不保证始终按最优价排序，且可能携带 size=0 的更新项。
    为避免把异常档位（例如 0.99/0.999）误当作 best，我们会：
    1) 遍历全部档位；
    2) 忽略无效价格或显式 size<=0 的项；
    3) bid 取最大价，ask 取最小价。
    """
    if not isinstance(levels, list) or not levels:
        return None

    side_norm = str(side).strip().lower()
    if side_norm not in {"bid", "ask"}:
        return None

    candidates: list[float] = []
    for level in levels:
        price: Optional[float] = None
        size: Optional[float] = None

        if isinstance(level, dict):
            price = _coerce_float(level.get("price"))
            size = _coerce_float(level.get("size") or level.get("quantity") or level.get("qty"))
        elif isinstance(level, (list, tuple)):
            if len(level) >= 1:
                price = _coerce_float(level[0])
            if len(level) >= 2:
                size = _coerce_float(level[1])

        if price is None or price <= 0:
            continue
        if size is not None and size <= 0:
            continue
        candidates.append(price)

    if not candidates:
        return None
    return max(candidates) if side_norm == "bid" else min(candidates)


def _extract_best_bid_ask_from_book_event(ev: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    """从 book 事件提取 bid/ask，兼容文档中的 buys/sells 与示例中的 bids/asks。"""
    # 优先使用显式 best_bid/best_ask（若有），避免从未排序或增量档位中误提取。
    bid = _coerce_float(ev.get("best_bid") or ev.get("bid"))
    ask = _coerce_float(ev.get("best_ask") or ev.get("ask"))

    bid_levels = ev.get("buys")
    if not isinstance(bid_levels, list):
        bid_levels = ev.get("bids")
    ask_levels = ev.get("sells")
    if not isinstance(ask_levels, list):
        ask_levels = ev.get("asks")

    if bid is None:
        bid = _extract_top_price_from_levels(bid_levels, "bid")
    if ask is None:
        ask = _extract_top_price_from_levels(ask_levels, "ask")

    return bid, ask


def _detect_suspicious_quote(
    bid: float,
    ask: float,
    *,
    last_trade_price: Optional[float] = None,
) -> Optional[str]:
    """识别明显异常的盘口，避免污染共享 WS 缓存。"""
    if bid <= 0 or ask <= 0:
        return "non_positive_quote"
    if ask < bid:
        return "crossed_book"

    mid = (bid + ask) / 2.0
    if mid <= 0:
        return "invalid_mid"

    spread_ratio = (ask - bid) / mid
    if spread_ratio >= 1.8:
        return f"spread_ratio_too_wide:{spread_ratio:.3f}"

    if bid <= 0.02 and ask >= 0.98:
        return "edge_quote_0.01_0.99"

    if (
        last_trade_price is not None
        and last_trade_price > 0
        and spread_ratio >= 1.0
    ):
        last_dev = abs(last_trade_price - mid) / mid
        if last_dev >= 0.6:
            return f"last_mid_diverge:{last_dev:.3f}"

    return None


def _env_flag(name: str) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in {"1", "true", "yes", "on", "y", "debug"}


def _ceil_to_precision(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.ceil(value * factor - 1e-12) / factor


def _ticks_for_price_move(
    anchor_price: float,
    pct: float,
    tick: float,
    *,
    min_ticks: int = 1,
    coarse_tick_ratio: float = 0.03,
    coarse_min_ticks: int = 2,
) -> int:
    if tick <= 0:
        return max(1, int(min_ticks))
    anchor = max(float(anchor_price), float(tick))
    effective_min = max(1, int(min_ticks))
    if float(tick) / anchor >= float(coarse_tick_ratio):
        effective_min = max(effective_min, int(coarse_min_ticks))
    target_ticks = int(math.ceil((anchor * max(0.0, float(pct))) / float(tick) - 1e-12))
    return max(effective_min, target_ticks)


def _scale_order_size_by_volume(
    base_size: float,
    total_volume: float,
    *,
    base_volume: Optional[float] = None,
    growth_factor: float = 0.5,
    decimals: int = ORDER_SIZE_DECIMALS,
) -> float:
    """根据市场成交量对基础下单份数进行递增（边际递减）。"""

    if base_size <= 0 or total_volume <= 0:
        return base_size

    effective_base_volume = _coerce_float(base_volume) or total_volume
    if effective_base_volume <= 0:
        return base_size

    effective_growth = max(growth_factor, 0.0)
    vol_ratio = max(total_volume / effective_base_volume, 1.0)
    # 使用对数增长控制放大：
    #   - base_volume 附近仅有轻微提升；
    #   - 成交量每提升 10 倍仅线性增加 growth_factor，边际效用递减。
    weight = 1.0 + effective_growth * math.log10(vol_ratio)
    weighted_size = base_size * weight
    return _ceil_to_precision(weighted_size, decimals)


def _load_json_file(path: Path) -> Dict[str, Any]:
    """读取 JSON 配置，不存在则返回空 dict。"""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:  # pragma: no cover - 粗略校验
            raise RuntimeError(f"无法解析 JSON 配置: {path}: {exc}") from exc


def _dump_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_handled_topics(path: Path) -> set[str]:
    """读取历史已处理话题集合，空文件或字段缺失则返回空集合。"""

    data = _load_json_file(path)
    topics = data.get("topics") or data.get("handled_topics")
    if topics is None:
        return set()
    if not isinstance(topics, list):  # pragma: no cover - 容错
        print(f"[WARN] handled_topics 文件格式异常，已忽略: {path}")
        return set()
    return {str(t) for t in topics}


def write_handled_topics(path: Path, topics: set[str]) -> None:
    """写入最新的已处理话题集合。"""

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(topics),
        "topics": sorted(topics),
    }
    _dump_json_file(path, payload)


def compute_new_topics(latest: List[Any], handled: set[str]) -> List[str]:
    """从最新筛选结果中筛出尚未处理的话题列表。"""

    result: List[str] = []
    seen: set[str] = set()
    for entry in latest:
        topic_id = _topic_id_from_entry(entry)
        if topic_id and topic_id not in handled and topic_id not in seen:
            result.append(topic_id)
            seen.add(topic_id)
    return result


@dataclass
class GlobalConfig:
    copytrade_poll_sec: float = DEFAULT_GLOBAL_CONFIG["copytrade_poll_sec"]
    command_poll_sec: float = DEFAULT_GLOBAL_CONFIG["command_poll_sec"]
    max_concurrent_tasks: int = DEFAULT_GLOBAL_CONFIG["max_concurrent_tasks"]
    max_exit_cleanup_tasks: int = DEFAULT_GLOBAL_CONFIG["max_exit_cleanup_tasks"]
    log_dir: Path = field(default_factory=lambda: Path(DEFAULT_GLOBAL_CONFIG["log_dir"]))
    data_dir: Path = field(default_factory=lambda: Path(DEFAULT_GLOBAL_CONFIG["data_dir"]))
    handled_topics_path: Path = field(
        default_factory=lambda: Path(DEFAULT_GLOBAL_CONFIG["handled_topics_path"])
    )
    copytrade_tokens_path: Path = field(
        default_factory=lambda: Path(DEFAULT_GLOBAL_CONFIG["copytrade_tokens_path"])
    )
    copytrade_sell_signals_path: Path = field(
        default_factory=lambda: Path(DEFAULT_GLOBAL_CONFIG["copytrade_sell_signals_path"])
    )
    copytrade_blacklist_path: Path = field(
        default_factory=lambda: Path(DEFAULT_GLOBAL_CONFIG["copytrade_blacklist_path"])
    )
    global_config_path: Optional[Path] = None
    process_start_retries: int = DEFAULT_GLOBAL_CONFIG["process_start_retries"]
    process_retry_delay_sec: float = DEFAULT_GLOBAL_CONFIG["process_retry_delay_sec"]
    process_graceful_timeout_sec: float = DEFAULT_GLOBAL_CONFIG[
        "process_graceful_timeout_sec"
    ]
    process_stagger_max_sec: float = DEFAULT_GLOBAL_CONFIG["process_stagger_max_sec"]
    topic_start_cooldown_sec: float = DEFAULT_GLOBAL_CONFIG["topic_start_cooldown_sec"]
    active_unmanaged_rearm_cooldown_sec: float = DEFAULT_GLOBAL_CONFIG[
        "active_unmanaged_rearm_cooldown_sec"
    ]
    active_unmanaged_rearm_budget_window_sec: float = DEFAULT_GLOBAL_CONFIG[
        "active_unmanaged_rearm_budget_window_sec"
    ]
    active_unmanaged_rearm_budget_max_attempts: int = DEFAULT_GLOBAL_CONFIG[
        "active_unmanaged_rearm_budget_max_attempts"
    ]
    active_unmanaged_rearm_budget_suppress_sec: float = DEFAULT_GLOBAL_CONFIG[
        "active_unmanaged_rearm_budget_suppress_sec"
    ]
    log_excerpt_interval_sec: float = DEFAULT_GLOBAL_CONFIG["log_excerpt_interval_sec"]
    runtime_status_path: Path = field(
        default_factory=lambda: Path(DEFAULT_GLOBAL_CONFIG["runtime_status_path"])
    )
    token_cycle_state_path: Path = field(
        default_factory=lambda: Path(DEFAULT_GLOBAL_CONFIG["token_cycle_state_path"])
    )
    ws_debug_raw: bool = bool(DEFAULT_GLOBAL_CONFIG["ws_debug_raw"])
    # Shared WS 等待配置
    shared_ws_max_pending_wait_sec: float = DEFAULT_GLOBAL_CONFIG[
        "shared_ws_max_pending_wait_sec"
    ]
    shared_ws_wait_poll_sec: float = DEFAULT_GLOBAL_CONFIG["shared_ws_wait_poll_sec"]
    shared_ws_wait_failures_before_pause: int = DEFAULT_GLOBAL_CONFIG[
        "shared_ws_wait_failures_before_pause"
    ]
    shared_ws_wait_pause_minutes: float = DEFAULT_GLOBAL_CONFIG[
        "shared_ws_wait_pause_minutes"
    ]
    shared_ws_wait_escalation_window_sec: float = DEFAULT_GLOBAL_CONFIG[
        "shared_ws_wait_escalation_window_sec"
    ]
    shared_ws_wait_escalation_min_failures: int = DEFAULT_GLOBAL_CONFIG[
        "shared_ws_wait_escalation_min_failures"
    ]
    ws_no_event_warn_interval_sec: float = DEFAULT_GLOBAL_CONFIG[
        "ws_no_event_warn_interval_sec"
    ]
    ws_cache_flush_min_interval_sec: float = DEFAULT_GLOBAL_CONFIG[
        "ws_cache_flush_min_interval_sec"
    ]
    ws_silence_timeout_sec: float = DEFAULT_GLOBAL_CONFIG[
        "ws_silence_timeout_sec"
    ]
    ws_custom_feature_enabled: bool = bool(
        DEFAULT_GLOBAL_CONFIG["ws_custom_feature_enabled"]
    )
    ws_subscribe_chunk_size: int = int(DEFAULT_GLOBAL_CONFIG["ws_subscribe_chunk_size"])
    ws_subscribe_chunk_interval_ms: float = float(
        DEFAULT_GLOBAL_CONFIG["ws_subscribe_chunk_interval_ms"]
    )
    ws_ready_use_confirmed: bool = bool(DEFAULT_GLOBAL_CONFIG["ws_ready_use_confirmed"])
    ws_ready_confirm_grace_sec: float = float(
        DEFAULT_GLOBAL_CONFIG["ws_ready_confirm_grace_sec"]
    )
    ws_unsubscribe_grace_sec: float = float(DEFAULT_GLOBAL_CONFIG["ws_unsubscribe_grace_sec"])
    ws_ping_interval_sec: float = float(DEFAULT_GLOBAL_CONFIG["ws_ping_interval_sec"])
    ws_ping_timeout_sec: float = float(DEFAULT_GLOBAL_CONFIG["ws_ping_timeout_sec"])
    ws_enable_text_ping: bool = bool(DEFAULT_GLOBAL_CONFIG["ws_enable_text_ping"])
    exit_start_stagger_sec: float = DEFAULT_GLOBAL_CONFIG["exit_start_stagger_sec"]
    strategy_mode: str = str(DEFAULT_GLOBAL_CONFIG["strategy_mode"])
    # 【重构】burst和reentry参数与mode解耦
    burst_slots: int = int(DEFAULT_GLOBAL_CONFIG["burst_slots"])
    enable_burst_queue: bool = bool(DEFAULT_GLOBAL_CONFIG["enable_burst_queue"])
    enable_reentry: bool = bool(DEFAULT_GLOBAL_CONFIG["enable_reentry"])
    exhausted_keep_orders: bool = bool(DEFAULT_GLOBAL_CONFIG["exhausted_keep_orders"])
    exhausted_enable_reentry: bool = bool(DEFAULT_GLOBAL_CONFIG["exhausted_enable_reentry"])
    aggressive_reentry_source: str = str(
        DEFAULT_GLOBAL_CONFIG["aggressive_reentry_source"]
    )
    aggressive_first_sell_fill_only: bool = bool(
        DEFAULT_GLOBAL_CONFIG["aggressive_first_sell_fill_only"]
    )
    aggressive_sell_fill_poll_sec: float = float(
        DEFAULT_GLOBAL_CONFIG["aggressive_sell_fill_poll_sec"]
    )
    aggressive_burst_slots: int = int(DEFAULT_GLOBAL_CONFIG.get("aggressive_burst_slots", 10))
    aggressive_enable_self_sell_reentry: bool = bool(
        DEFAULT_GLOBAL_CONFIG.get("aggressive_enable_self_sell_reentry", False)
    )
    classic_keep_orders_price_threshold: float = float(
        DEFAULT_GLOBAL_CONFIG.get("classic_keep_orders_price_threshold", 0.8)
    )
    title_blacklist_enabled: bool = bool(
        (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get("enabled", False)
    )
    title_blacklist_keywords: List[str] = field(default_factory=list)
    title_blacklist_match_on_slug: bool = bool(
        (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get("match_on_slug", True)
    )
    title_blacklist_action_with_position: str = str(
        (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get(
            "action_with_position", "sell_only_maker"
        )
    )
    # Slot refill (回填) 配置
    enable_slot_refill: bool = bool(DEFAULT_GLOBAL_CONFIG["enable_slot_refill"])
    refill_cooldown_minutes: float = DEFAULT_GLOBAL_CONFIG["refill_cooldown_minutes"]
    refill_cooldown_minutes_with_position: float = DEFAULT_GLOBAL_CONFIG[
        "refill_cooldown_minutes_with_position"
    ]
    refill_cooldown_minutes_no_position: float = DEFAULT_GLOBAL_CONFIG[
        "refill_cooldown_minutes_no_position"
    ]
    max_refill_retries: int = DEFAULT_GLOBAL_CONFIG["max_refill_retries"]
    refill_check_interval_sec: float = DEFAULT_GLOBAL_CONFIG["refill_check_interval_sec"]
    enable_stoploss: bool = bool(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("enabled", False)
    )
    stoploss_check_interval_sec: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("check_interval_sec", 60.0)
    )
    stoploss_base_drawdown_pct: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("base_drawdown_pct", 0.05)
    )
    stoploss_drawdown_pct: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("drawdown_pct", 0.20)
    )
    stoploss_aggressive_drawdown_pct: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("aggressive_drawdown_pct", 0.05)
    )
    stoploss_aggressive_retry_cooldown_minutes: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get(
            "aggressive_retry_cooldown_minutes", 10.0
        )
    )
    stoploss_reentry_window_cooldown_minutes: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("reentry_window_cooldown_minutes", 120.0)
    )
    stoploss_next_stoploss_cooldown_minutes: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("next_stoploss_cooldown_minutes", 30.0)
    )
    stoploss_reentry_extra_delay_after_5_cuts_hours: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("reentry_extra_delay_after_5_cuts_hours", 24.0)
    )
    stoploss_reentry_timeout_hours: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("reentry_timeout_hours", 168.0)
    )
    stoploss_reference_tick_size: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("reference_tick_size", 0.01)
    )
    stoploss_reentry_line_ticks: int = int(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("reentry_line_ticks", 2)
    )
    stoploss_reentry_zone_lower_pct: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("reentry_zone_lower_pct", 0.02)
    )
    stoploss_probe_break_pct: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("probe_break_pct", 0.08)
    )
    stoploss_clear_confirm_timeout_sec: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("clear_confirm_timeout_sec", 180.0)
    )
    stoploss_clear_confirm_retry_interval_sec: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("clear_confirm_retry_interval_sec", 1800.0)
    )
    stoploss_clear_confirm_max_retries: int = int(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("clear_confirm_max_retries", 3)
    )
    stoploss_daily_reentry_max_full_clears: int = int(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("daily_reentry_max_full_clears", 2)
    )
    stoploss_drawdown_step_per_cycle_pct: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("drawdown_step_per_cycle_pct", 0.01)
    )
    stoploss_drawdown_step_per_cycle_ticks: int = int(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("drawdown_step_per_cycle_ticks", 1)
    )
    stoploss_min_age_minutes: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("min_age_minutes", 5.0)
    )
    stoploss_confirm_rounds: int = int(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("confirm_rounds", 2)
    )
    stoploss_min_maker_order_size: float = float(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("min_maker_order_size", 5.0)
    )
    stoploss_max_tokens_per_cycle: int = int(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get("max_tokens_per_cycle", 3)
    )
    stoploss_skip_if_global_liquidation_active: bool = bool(
        (DEFAULT_GLOBAL_CONFIG.get("stoploss") or {}).get(
            "skip_if_global_liquidation_active", True
        )
    )
    stoploss_reentry_state_path: Path = field(
        default_factory=lambda: PROJECT_ROOT.parent / "copytrade" / "stoploss_reentry_state.json"
    )
    stoploss_reentry_state_backup_path: Path = field(
        default_factory=lambda: PROJECT_ROOT.parent / "copytrade" / "stoploss_reentry_state.bak.json"
    )
    gap_skip_backoff_enabled: bool = bool(
        DEFAULT_GLOBAL_CONFIG["gap_skip_backoff_enabled"]
    )
    gap_skip_backoff_minutes: List[float] = field(
        default_factory=lambda: list(DEFAULT_GLOBAL_CONFIG["gap_skip_backoff_minutes"])
    )
    inactive_token_reconcile_interval_sec: float = DEFAULT_GLOBAL_CONFIG[
        "inactive_token_reconcile_interval_sec"
    ]
    startup_orphan_profit_sweep_enabled: bool = bool(
        DEFAULT_GLOBAL_CONFIG["startup_orphan_profit_sweep_enabled"]
    )
    startup_orphan_profit_sweep_min_profit_pct: float = float(
        DEFAULT_GLOBAL_CONFIG["startup_orphan_profit_sweep_min_profit_pct"]
    )
    startup_orphan_profit_sweep_skip_if_open_sell: bool = bool(
        DEFAULT_GLOBAL_CONFIG["startup_orphan_profit_sweep_skip_if_open_sell"]
    )
    orphan_recovery_enabled: bool = bool(
        DEFAULT_GLOBAL_CONFIG.get("orphan_recovery_enabled", True)
    )
    orphan_recovery_probe_interval_sec: float = float(
        DEFAULT_GLOBAL_CONFIG.get("orphan_recovery_probe_interval_sec", 600.0)
    )
    orphan_recovery_max_backoff_sec: float = float(
        DEFAULT_GLOBAL_CONFIG.get("orphan_recovery_max_backoff_sec", 7200.0)
    )
    orphan_recovery_max_attempts: int = int(
        DEFAULT_GLOBAL_CONFIG.get("orphan_recovery_max_attempts", 8)
    )
    startup_double_zero_confirm_hits: int = int(
        DEFAULT_GLOBAL_CONFIG.get("startup_double_zero_confirm_hits", 2)
    )
    startup_double_zero_probe_interval_sec: float = float(
        DEFAULT_GLOBAL_CONFIG.get("startup_double_zero_probe_interval_sec", 1.0)
    )
    enable_pending_soft_eviction: bool = DEFAULT_GLOBAL_CONFIG[
        "enable_pending_soft_eviction"
    ]
    pending_soft_eviction_minutes: float = DEFAULT_GLOBAL_CONFIG[
        "pending_soft_eviction_minutes"
    ]
    pending_soft_eviction_check_interval_sec: float = DEFAULT_GLOBAL_CONFIG[
        "pending_soft_eviction_check_interval_sec"
    ]
    buy_pause_min_free_balance: float = DEFAULT_GLOBAL_CONFIG[
        "buy_pause_min_free_balance"
    ]
    buy_pause_balance_poll_interval_sec: float = DEFAULT_GLOBAL_CONFIG[
        "buy_pause_balance_poll_interval_sec"
    ]
    price_cap_refill_stuck_minutes: float = DEFAULT_GLOBAL_CONFIG[
        "price_cap_refill_stuck_minutes"
    ]
    shock_refill_stuck_minutes: float = DEFAULT_GLOBAL_CONFIG[
        "shock_refill_stuck_minutes"
    ]
    # Maker 子进程配置
    maker_poll_sec: float = 10.0  # 挂单轮询间隔（秒）
    maker_position_sync_interval: float = 60.0  # 仓位同步间隔（秒）
    total_liquidation: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_GLOBAL_CONFIG["total_liquidation"]))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GlobalConfig":
        data = data or {}
        scheduler = data.get("scheduler") or {}
        paths = data.get("paths") or {}
        debug = data.get("debug") or {}
        maker = data.get("maker") or {}
        flat_overrides = {k: v for k, v in data.items() if k not in {"scheduler", "paths", "maker"}}
        merged = {**DEFAULT_GLOBAL_CONFIG, **flat_overrides}
        title_blacklist_cfg = scheduler.get("title_blacklist")
        if not isinstance(title_blacklist_cfg, dict):
            title_blacklist_cfg = merged.get("title_blacklist")
        if not isinstance(title_blacklist_cfg, dict):
            title_blacklist_cfg = {}
        stoploss_cfg = scheduler.get("stoploss")
        if not isinstance(stoploss_cfg, dict):
            stoploss_cfg = merged.get("stoploss")
        if not isinstance(stoploss_cfg, dict):
            stoploss_cfg = {}

        log_dir = Path(
            paths.get("log_directory")
            or merged.get("log_dir", DEFAULT_GLOBAL_CONFIG["log_dir"])
        )
        data_dir = Path(
            paths.get("data_directory")
            or merged.get("data_dir", DEFAULT_GLOBAL_CONFIG["data_dir"])
        )

        handled_topics_path = Path(
            merged.get("handled_topics_path")
            or paths.get("handled_topics_file")
            or PROJECT_ROOT / "data" / "handled_topics.json"
        )
        copytrade_tokens_path = Path(
            merged.get("copytrade_tokens_path")
            or paths.get("copytrade_tokens_file")
            or PROJECT_ROOT.parent / "copytrade" / "tokens_from_copytrade.json"
        )
        copytrade_sell_signals_path = Path(
            merged.get("copytrade_sell_signals_path")
            or paths.get("copytrade_sell_signals_file")
            or PROJECT_ROOT.parent / "copytrade" / "copytrade_sell_signals.json"
        )
        copytrade_blacklist_path = Path(
            merged.get("copytrade_blacklist_path")
            or paths.get("copytrade_blacklist_file")
            or PROJECT_ROOT.parent / "copytrade" / "liquidation_blacklist.json"
        )
        runtime_status_path = Path(
            merged.get("runtime_status_path")
            or paths.get("run_state_file")
            or data_dir / "autorun_status.json"
        )
        token_cycle_state_path = Path(
            paths.get("token_cycle_state_file")
            or flat_overrides.get("token_cycle_state_path")
            or data_dir / "token_cycle_gate.json"
        )
        stoploss_reentry_state_path = Path(
            paths.get("stoploss_reentry_state_file")
            or flat_overrides.get("stoploss_reentry_state_path")
            or PROJECT_ROOT.parent / "copytrade" / "stoploss_reentry_state.json"
        )
        stoploss_reentry_state_backup_path = Path(
            paths.get("stoploss_reentry_state_backup_file")
            or flat_overrides.get("stoploss_reentry_state_backup_path")
            or PROJECT_ROOT.parent / "copytrade" / "stoploss_reentry_state.bak.json"
        )

        ws_ping_interval_sec = max(
            5.0,
            float(
                scheduler.get(
                    "ws_ping_interval_sec",
                    merged.get(
                        "ws_ping_interval_sec",
                        cls.ws_ping_interval_sec,
                    ),
                )
            ),
        )
        ws_ping_timeout_sec = max(
            1.0,
            float(
                scheduler.get(
                    "ws_ping_timeout_sec",
                    merged.get(
                        "ws_ping_timeout_sec",
                        cls.ws_ping_timeout_sec,
                    ),
                )
            ),
        )
        # websocket-client 要求 ping_interval > ping_timeout，否则 run_forever 直接抛错。
        if ws_ping_timeout_sec >= ws_ping_interval_sec:
            adjusted = max(1.0, ws_ping_interval_sec - 1.0)
            print(
                "[WS][CONFIG][WARN] ws_ping_timeout_sec 必须小于 ws_ping_interval_sec，"
                f"已自动调整 timeout: {ws_ping_timeout_sec:.1f}s -> {adjusted:.1f}s"
            )
            ws_ping_timeout_sec = adjusted

        return cls(
            copytrade_poll_sec=float(
                scheduler.get("copytrade_poll_seconds")
                or merged.get(
                    "copytrade_poll_sec", DEFAULT_GLOBAL_CONFIG["copytrade_poll_sec"]
                )
            ),
            command_poll_sec=float(
                scheduler.get("command_poll_seconds")
                or scheduler.get("poll_interval_seconds")
                or merged.get("command_poll_sec", DEFAULT_GLOBAL_CONFIG["command_poll_sec"])
            ),
            max_concurrent_tasks=int(
                scheduler.get(
                    "max_concurrent_tasks", DEFAULT_GLOBAL_CONFIG["max_concurrent_tasks"]
                )
            ),
            max_exit_cleanup_tasks=int(
                scheduler.get(
                    "max_exit_cleanup_tasks", DEFAULT_GLOBAL_CONFIG["max_exit_cleanup_tasks"]
                )
            ),
            log_dir=log_dir,
            data_dir=data_dir,
            handled_topics_path=handled_topics_path,
            copytrade_tokens_path=copytrade_tokens_path,
            copytrade_sell_signals_path=copytrade_sell_signals_path,
            copytrade_blacklist_path=copytrade_blacklist_path,
            process_start_retries=int(
                merged.get("process_start_retries", cls.process_start_retries)
            ),
            process_retry_delay_sec=float(
                merged.get("process_retry_delay_sec", cls.process_retry_delay_sec)
            ),
            process_graceful_timeout_sec=float(
                merged.get(
                    "process_graceful_timeout_sec", cls.process_graceful_timeout_sec
                )
            ),
            process_stagger_max_sec=float(
                merged.get("process_stagger_max_sec", cls.process_stagger_max_sec)
            ),
            topic_start_cooldown_sec=float(
                merged.get("topic_start_cooldown_sec", cls.topic_start_cooldown_sec)
            ),
            active_unmanaged_rearm_cooldown_sec=max(
                60.0,
                float(
                    merged.get(
                        "active_unmanaged_rearm_cooldown_sec",
                        cls.active_unmanaged_rearm_cooldown_sec,
                    )
                ),
            ),
            active_unmanaged_rearm_budget_window_sec=max(
                300.0,
                float(
                    merged.get(
                        "active_unmanaged_rearm_budget_window_sec",
                        cls.active_unmanaged_rearm_budget_window_sec,
                    )
                ),
            ),
            active_unmanaged_rearm_budget_max_attempts=max(
                1,
                int(
                    merged.get(
                        "active_unmanaged_rearm_budget_max_attempts",
                        cls.active_unmanaged_rearm_budget_max_attempts,
                    )
                ),
            ),
            active_unmanaged_rearm_budget_suppress_sec=max(
                300.0,
                float(
                    merged.get(
                        "active_unmanaged_rearm_budget_suppress_sec",
                        cls.active_unmanaged_rearm_budget_suppress_sec,
                    )
                ),
            ),
            log_excerpt_interval_sec=float(
                merged.get("log_excerpt_interval_sec", cls.log_excerpt_interval_sec)
            ),
            runtime_status_path=runtime_status_path,
            token_cycle_state_path=token_cycle_state_path,
            ws_debug_raw=bool(
                debug.get("ws_debug_raw")
                or debug.get("ws_raw")
                or merged.get("ws_debug_raw", cls.ws_debug_raw)
            ),
            shared_ws_max_pending_wait_sec=float(
                merged.get(
                    "shared_ws_max_pending_wait_sec",
                    merged.get(
                        "shared_ws_wait_timeout_sec",
                        cls.shared_ws_max_pending_wait_sec,
                    ),
                )
            ),
            shared_ws_wait_poll_sec=float(
                merged.get(
                    "shared_ws_wait_poll_sec",
                    cls.shared_ws_wait_poll_sec,
                )
            ),
            shared_ws_wait_failures_before_pause=int(
                merged.get(
                    "shared_ws_wait_failures_before_pause",
                    cls.shared_ws_wait_failures_before_pause,
                )
            ),
            shared_ws_wait_pause_minutes=float(
                merged.get(
                    "shared_ws_wait_pause_minutes",
                    cls.shared_ws_wait_pause_minutes,
                )
            ),
            shared_ws_wait_escalation_window_sec=float(
                merged.get(
                    "shared_ws_wait_escalation_window_sec",
                    cls.shared_ws_wait_escalation_window_sec,
                )
            ),
            shared_ws_wait_escalation_min_failures=int(
                merged.get(
                    "shared_ws_wait_escalation_min_failures",
                    cls.shared_ws_wait_escalation_min_failures,
                )
            ),
            ws_no_event_warn_interval_sec=float(
                merged.get(
                    "ws_no_event_warn_interval_sec",
                    cls.ws_no_event_warn_interval_sec,
                )
            ),
            ws_cache_flush_min_interval_sec=float(
                merged.get(
                    "ws_cache_flush_min_interval_sec",
                    cls.ws_cache_flush_min_interval_sec,
                )
            ),
            ws_silence_timeout_sec=float(
                merged.get(
                    "ws_silence_timeout_sec",
                    cls.ws_silence_timeout_sec,
                )
            ),
            ws_custom_feature_enabled=bool(
                merged.get(
                    "ws_custom_feature_enabled",
                    cls.ws_custom_feature_enabled,
                )
            ),
            ws_subscribe_chunk_size=max(
                1,
                int(
                    scheduler.get(
                        "ws_subscribe_chunk_size",
                        merged.get(
                            "ws_subscribe_chunk_size",
                            cls.ws_subscribe_chunk_size,
                        ),
                    )
                ),
            ),
            ws_subscribe_chunk_interval_ms=max(
                0.0,
                float(
                    scheduler.get(
                        "ws_subscribe_chunk_interval_ms",
                        merged.get(
                            "ws_subscribe_chunk_interval_ms",
                            cls.ws_subscribe_chunk_interval_ms,
                        ),
                    )
                ),
            ),
            ws_ready_use_confirmed=bool(
                scheduler.get(
                    "ws_ready_use_confirmed",
                    merged.get(
                        "ws_ready_use_confirmed",
                        cls.ws_ready_use_confirmed,
                    ),
                )
            ),
            ws_ready_confirm_grace_sec=max(
                0.0,
                float(
                    scheduler.get(
                        "ws_ready_confirm_grace_sec",
                        merged.get(
                            "ws_ready_confirm_grace_sec",
                            cls.ws_ready_confirm_grace_sec,
                        ),
                    )
                ),
            ),
            ws_unsubscribe_grace_sec=max(
                0.0,
                float(
                    scheduler.get(
                        "ws_unsubscribe_grace_sec",
                        merged.get(
                            "ws_unsubscribe_grace_sec",
                            cls.ws_unsubscribe_grace_sec,
                        ),
                    )
                ),
            ),
            ws_ping_interval_sec=ws_ping_interval_sec,
            ws_ping_timeout_sec=ws_ping_timeout_sec,
            ws_enable_text_ping=bool(
                scheduler.get(
                    "ws_enable_text_ping",
                    merged.get("ws_enable_text_ping", cls.ws_enable_text_ping),
                )
            ),
            exit_start_stagger_sec=float(
                merged.get("exit_start_stagger_sec", cls.exit_start_stagger_sec)
            ),
            strategy_mode=str(
                scheduler.get("strategy_mode", merged.get("strategy_mode", cls.strategy_mode))
                or "classic"
            ).strip().lower(),
            # 【重构】burst和reentry参数与mode解耦
            burst_slots=max(
                0,
                int(
                    scheduler.get(
                        "burst_slots",
                        merged.get("burst_slots", cls.burst_slots),
                    )
                ),
            ),
            enable_burst_queue=bool(
                scheduler.get(
                    "enable_burst_queue",
                    merged.get("enable_burst_queue", cls.enable_burst_queue),
                )
            ),
            enable_reentry=bool(
                scheduler.get(
                    "enable_reentry",
                    merged.get("enable_reentry", cls.enable_reentry),
                )
            ),
            exhausted_keep_orders=bool(
                scheduler.get(
                    "exhausted_keep_orders",
                    merged.get("exhausted_keep_orders", cls.exhausted_keep_orders),
                )
            ),
            exhausted_enable_reentry=bool(
                scheduler.get(
                    "exhausted_enable_reentry",
                    merged.get("exhausted_enable_reentry", cls.exhausted_enable_reentry),
                )
            ),
            # 兼容旧配置
            aggressive_burst_slots=max(
                0,
                int(
                    scheduler.get(
                        "aggressive_burst_slots",
                        merged.get("aggressive_burst_slots", cls.burst_slots),
                    )
                ),
            ),
            aggressive_enable_self_sell_reentry=bool(
                scheduler.get(
                    "aggressive_enable_self_sell_reentry",
                    merged.get(
                        "aggressive_enable_self_sell_reentry",
                        cls.enable_reentry,
                    ),
                )
            ),
            classic_keep_orders_price_threshold=float(
                scheduler.get(
                    "classic_keep_orders_price_threshold",
                    merged.get(
                        "classic_keep_orders_price_threshold",
                        cls.classic_keep_orders_price_threshold,
                    ),
                )
            ),
            title_blacklist_enabled=bool(
                title_blacklist_cfg.get(
                    "enabled",
                    (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get(
                        "enabled", False
                    ),
                )
            ),
            title_blacklist_keywords=[
                str(k).strip()
                for k in (title_blacklist_cfg.get("keywords") or [])
                if str(k).strip()
            ],
            title_blacklist_match_on_slug=bool(
                title_blacklist_cfg.get(
                    "match_on_slug",
                    (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get(
                        "match_on_slug", True
                    ),
                )
            ),
            title_blacklist_action_with_position=str(
                title_blacklist_cfg.get(
                    "action_with_position",
                    (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get(
                        "action_with_position", "sell_only_maker"
                    ),
                )
                or "sell_only_maker"
            ).strip().lower(),
            aggressive_reentry_source=str(
                scheduler.get(
                    "aggressive_reentry_source",
                    merged.get("aggressive_reentry_source", cls.aggressive_reentry_source),
                )
                or cls.aggressive_reentry_source
            ).strip().lower(),
            aggressive_first_sell_fill_only=bool(
                scheduler.get(
                    "aggressive_first_sell_fill_only",
                    merged.get(
                        "aggressive_first_sell_fill_only",
                        cls.aggressive_first_sell_fill_only,
                    ),
                )
            ),
            aggressive_sell_fill_poll_sec=max(
                1.0,
                float(
                    scheduler.get(
                        "aggressive_sell_fill_poll_sec",
                        merged.get(
                            "aggressive_sell_fill_poll_sec",
                            cls.aggressive_sell_fill_poll_sec,
                        ),
                    )
                ),
            ),
            # Slot refill (回填) 配置
            enable_slot_refill=bool(
                scheduler.get("enable_slot_refill", merged.get("enable_slot_refill", True))
            ),
            refill_cooldown_minutes=float(
                scheduler.get(
                    "refill_cooldown_minutes",
                    merged.get("refill_cooldown_minutes", cls.refill_cooldown_minutes),
                )
            ),
            refill_cooldown_minutes_with_position=float(
                scheduler.get(
                    "refill_cooldown_minutes_with_position",
                    merged.get(
                        "refill_cooldown_minutes_with_position",
                        merged.get(
                            "refill_cooldown_minutes",
                            cls.refill_cooldown_minutes_with_position,
                        ),
                    ),
                )
            ),
            refill_cooldown_minutes_no_position=float(
                scheduler.get(
                    "refill_cooldown_minutes_no_position",
                    merged.get(
                        "refill_cooldown_minutes_no_position",
                        cls.refill_cooldown_minutes_no_position,
                    ),
                )
            ),
            max_refill_retries=int(
                scheduler.get(
                    "max_refill_retries",
                    merged.get("max_refill_retries", cls.max_refill_retries),
                )
            ),
            refill_check_interval_sec=float(
                scheduler.get("refill_check_interval_sec", merged.get("refill_check_interval_sec", 60.0))
            ),
            enable_stoploss=bool(
                stoploss_cfg.get(
                    "enabled",
                    merged.get("enable_stoploss", cls.enable_stoploss),
                )
            ),
            stoploss_check_interval_sec=max(
                10.0,
                float(
                    stoploss_cfg.get(
                        "check_interval_sec",
                        merged.get(
                            "stoploss_check_interval_sec",
                            cls.stoploss_check_interval_sec,
                        ),
                    )
                ),
            ),
            stoploss_base_drawdown_pct=max(
                0.001,
                float(
                    stoploss_cfg.get(
                        "base_drawdown_pct",
                        merged.get("stoploss_base_drawdown_pct", cls.stoploss_base_drawdown_pct),
                    )
                ),
            ),
            stoploss_drawdown_pct=max(
                0.001,
                float(
                    stoploss_cfg.get(
                        "drawdown_pct",
                        merged.get("stoploss_drawdown_pct", cls.stoploss_drawdown_pct),
                    )
                ),
            ),
            stoploss_reentry_window_cooldown_minutes=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "reentry_window_cooldown_minutes",
                        merged.get(
                            "stoploss_reentry_window_cooldown_minutes",
                            cls.stoploss_reentry_window_cooldown_minutes,
                        ),
                    )
                ),
            ),
            stoploss_next_stoploss_cooldown_minutes=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "next_stoploss_cooldown_minutes",
                        merged.get(
                            "stoploss_next_stoploss_cooldown_minutes",
                            cls.stoploss_next_stoploss_cooldown_minutes,
                        ),
                    )
                ),
            ),
            stoploss_reentry_extra_delay_after_5_cuts_hours=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "reentry_extra_delay_after_5_cuts_hours",
                        merged.get(
                            "stoploss_reentry_extra_delay_after_5_cuts_hours",
                            cls.stoploss_reentry_extra_delay_after_5_cuts_hours,
                        ),
                    )
                ),
            ),
            stoploss_reentry_timeout_hours=max(
                1.0,
                float(
                    stoploss_cfg.get(
                        "reentry_timeout_hours",
                        merged.get(
                            "stoploss_reentry_timeout_hours",
                            cls.stoploss_reentry_timeout_hours,
                        ),
                    )
                ),
            ),
            stoploss_reference_tick_size=max(
                1e-8,
                float(
                    stoploss_cfg.get(
                        "reference_tick_size",
                        merged.get("stoploss_reference_tick_size", cls.stoploss_reference_tick_size),
                    )
                ),
            ),
            stoploss_reentry_line_ticks=max(
                1,
                int(
                    stoploss_cfg.get(
                        "reentry_line_ticks",
                        merged.get("stoploss_reentry_line_ticks", cls.stoploss_reentry_line_ticks),
                    )
                ),
            ),
            stoploss_reentry_zone_lower_pct=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "reentry_zone_lower_pct",
                        merged.get(
                            "stoploss_reentry_zone_lower_pct",
                            cls.stoploss_reentry_zone_lower_pct,
                        ),
                    )
                ),
            ),
            stoploss_probe_break_pct=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "probe_break_pct",
                        merged.get("stoploss_probe_break_pct", cls.stoploss_probe_break_pct),
                    )
                ),
            ),
            stoploss_clear_confirm_timeout_sec=max(
                30.0,
                float(
                    stoploss_cfg.get(
                        "clear_confirm_timeout_sec",
                        merged.get(
                            "stoploss_clear_confirm_timeout_sec",
                            cls.stoploss_clear_confirm_timeout_sec,
                        ),
                    )
                ),
            ),
            stoploss_clear_confirm_retry_interval_sec=max(
                60.0,
                float(
                    stoploss_cfg.get(
                        "clear_confirm_retry_interval_sec",
                        merged.get(
                            "stoploss_clear_confirm_retry_interval_sec",
                            cls.stoploss_clear_confirm_retry_interval_sec,
                        ),
                    )
                ),
            ),
            stoploss_clear_confirm_max_retries=max(
                1,
                int(
                    stoploss_cfg.get(
                        "clear_confirm_max_retries",
                        merged.get(
                            "stoploss_clear_confirm_max_retries",
                            cls.stoploss_clear_confirm_max_retries,
                        ),
                    )
                ),
            ),
            stoploss_daily_reentry_max_full_clears=max(
                1,
                int(
                    stoploss_cfg.get(
                        "daily_reentry_max_full_clears",
                        merged.get(
                            "stoploss_daily_reentry_max_full_clears",
                            cls.stoploss_daily_reentry_max_full_clears,
                        ),
                    )
                ),
            ),
            stoploss_drawdown_step_per_cycle_pct=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "drawdown_step_per_cycle_pct",
                        merged.get(
                            "stoploss_drawdown_step_per_cycle_pct",
                            cls.stoploss_drawdown_step_per_cycle_pct,
                        ),
                    )
                ),
            ),
            stoploss_drawdown_step_per_cycle_ticks=max(
                0,
                int(
                    stoploss_cfg.get(
                        "drawdown_step_per_cycle_ticks",
                        merged.get(
                            "stoploss_drawdown_step_per_cycle_ticks",
                            cls.stoploss_drawdown_step_per_cycle_ticks,
                        ),
                    )
                ),
            ),
            stoploss_aggressive_drawdown_pct=max(
                0.001,
                float(
                    stoploss_cfg.get(
                        "aggressive_drawdown_pct",
                        merged.get(
                            "stoploss_aggressive_drawdown_pct",
                            cls.stoploss_aggressive_drawdown_pct,
                        ),
                    )
                ),
            ),
            stoploss_aggressive_retry_cooldown_minutes=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "aggressive_retry_cooldown_minutes",
                        merged.get(
                            "stoploss_aggressive_retry_cooldown_minutes",
                            cls.stoploss_aggressive_retry_cooldown_minutes,
                        ),
                    )
                ),
            ),
            stoploss_min_age_minutes=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "min_age_minutes",
                        merged.get(
                            "stoploss_min_age_minutes", cls.stoploss_min_age_minutes
                        ),
                    )
                ),
            ),
            stoploss_confirm_rounds=max(
                1,
                int(
                    stoploss_cfg.get(
                        "confirm_rounds",
                        merged.get("stoploss_confirm_rounds", cls.stoploss_confirm_rounds),
                    )
                ),
            ),
            stoploss_min_maker_order_size=max(
                0.0,
                float(
                    stoploss_cfg.get(
                        "min_maker_order_size",
                        merged.get(
                            "stoploss_min_maker_order_size",
                            cls.stoploss_min_maker_order_size,
                        ),
                    )
                ),
            ),
            stoploss_max_tokens_per_cycle=max(
                1,
                int(
                    stoploss_cfg.get(
                        "max_tokens_per_cycle",
                        merged.get(
                            "stoploss_max_tokens_per_cycle",
                            cls.stoploss_max_tokens_per_cycle,
                        ),
                    )
                ),
            ),
            stoploss_skip_if_global_liquidation_active=bool(
                stoploss_cfg.get(
                    "skip_if_global_liquidation_active",
                    merged.get(
                        "stoploss_skip_if_global_liquidation_active",
                        cls.stoploss_skip_if_global_liquidation_active,
                    ),
                )
            ),
            stoploss_reentry_state_path=stoploss_reentry_state_path,
            stoploss_reentry_state_backup_path=stoploss_reentry_state_backup_path,
            gap_skip_backoff_enabled=bool(
                scheduler.get(
                    "gap_skip_backoff_enabled",
                    merged.get("gap_skip_backoff_enabled", True),
                )
            ),
            gap_skip_backoff_minutes=[
                float(v)
                for v in (
                    scheduler.get(
                        "gap_skip_backoff_minutes",
                        merged.get(
                            "gap_skip_backoff_minutes",
                            DEFAULT_GLOBAL_CONFIG["gap_skip_backoff_minutes"],
                        ),
                    )
                    or []
                )
                if _coerce_float(v) is not None and float(v) > 0
            ]
            or list(DEFAULT_GLOBAL_CONFIG["gap_skip_backoff_minutes"]),
            inactive_token_reconcile_interval_sec=max(
                60.0,
                float(
                    scheduler.get(
                        "inactive_token_reconcile_interval_sec",
                        merged.get("inactive_token_reconcile_interval_sec", 1200.0),
                    )
                ),
            ),
            startup_orphan_profit_sweep_enabled=bool(
                scheduler.get(
                    "startup_orphan_profit_sweep_enabled",
                    merged.get("startup_orphan_profit_sweep_enabled", True),
                )
            ),
            startup_orphan_profit_sweep_min_profit_pct=float(
                scheduler.get(
                    "startup_orphan_profit_sweep_min_profit_pct",
                    merged.get("startup_orphan_profit_sweep_min_profit_pct", 0.01),
                )
            ),
            startup_orphan_profit_sweep_skip_if_open_sell=bool(
                scheduler.get(
                    "startup_orphan_profit_sweep_skip_if_open_sell",
                    merged.get("startup_orphan_profit_sweep_skip_if_open_sell", True),
                )
            ),
            orphan_recovery_enabled=bool(
                scheduler.get(
                    "orphan_recovery_enabled",
                    merged.get("orphan_recovery_enabled", True),
                )
            ),
            orphan_recovery_probe_interval_sec=max(
                30.0,
                float(
                    scheduler.get(
                        "orphan_recovery_probe_interval_sec",
                        merged.get("orphan_recovery_probe_interval_sec", 600.0),
                    )
                ),
            ),
            orphan_recovery_max_backoff_sec=max(
                60.0,
                float(
                    scheduler.get(
                        "orphan_recovery_max_backoff_sec",
                        merged.get("orphan_recovery_max_backoff_sec", 7200.0),
                    )
                ),
            ),
            orphan_recovery_max_attempts=max(
                1,
                int(
                    scheduler.get(
                        "orphan_recovery_max_attempts",
                        merged.get("orphan_recovery_max_attempts", 8),
                    )
                ),
            ),
            startup_double_zero_confirm_hits=max(
                1,
                int(
                    scheduler.get(
                        "startup_double_zero_confirm_hits",
                        merged.get("startup_double_zero_confirm_hits", 2),
                    )
                ),
            ),
            startup_double_zero_probe_interval_sec=max(
                0.0,
                float(
                    scheduler.get(
                        "startup_double_zero_probe_interval_sec",
                        merged.get("startup_double_zero_probe_interval_sec", 1.0),
                    )
                ),
            ),
            enable_pending_soft_eviction=bool(
                scheduler.get(
                    "enable_pending_soft_eviction",
                    merged.get("enable_pending_soft_eviction", True),
                )
            ),
            pending_soft_eviction_minutes=float(
                scheduler.get(
                    "pending_soft_eviction_minutes",
                    merged.get("pending_soft_eviction_minutes", 60.0),
                )
            ),
            pending_soft_eviction_check_interval_sec=float(
                scheduler.get(
                    "pending_soft_eviction_check_interval_sec",
                    merged.get("pending_soft_eviction_check_interval_sec", 300.0),
                )
            ),
            buy_pause_min_free_balance=max(
                0.0,
                float(
                    scheduler.get(
                        "buy_pause_min_free_balance",
                        merged.get("buy_pause_min_free_balance", 0.0),
                    )
                ),
            ),
            buy_pause_balance_poll_interval_sec=max(
                1.0,
                float(
                    scheduler.get(
                        "buy_pause_balance_poll_interval_sec",
                        merged.get("buy_pause_balance_poll_interval_sec", 120.0),
                    )
                ),
            ),
            price_cap_refill_stuck_minutes=float(
                scheduler.get(
                    "price_cap_refill_stuck_minutes",
                    merged.get("price_cap_refill_stuck_minutes", 120.0),
                )
            ),
            shock_refill_stuck_minutes=float(
                scheduler.get(
                    "shock_refill_stuck_minutes",
                    merged.get("shock_refill_stuck_minutes", 45.0),
                )
            ),
            # Maker 子进程配置
            maker_poll_sec=float(
                maker.get("poll_sec", merged.get("maker_poll_sec", 10.0))
            ),
            maker_position_sync_interval=float(
                maker.get("position_sync_interval", merged.get("maker_position_sync_interval", 60.0))
            ),
            total_liquidation=dict(
                scheduler.get("total_liquidation")
                or merged.get("total_liquidation")
                or DEFAULT_GLOBAL_CONFIG["total_liquidation"]
            ),
        )

    def ensure_dirs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class TopicTask:
    topic_id: str
    status: str = "pending"
    start_time: float = field(default_factory=time.time)
    last_heartbeat: Optional[float] = None
    notes: List[str] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None
    log_path: Optional[Path] = None
    config_path: Optional[Path] = None
    log_excerpt: str = ""
    restart_attempts: int = 0
    no_restart: bool = False
    end_reason: Optional[str] = None
    last_log_excerpt_ts: float = 0.0

    def heartbeat(self, message: str) -> None:
        self.last_heartbeat = time.time()
        self.notes.append(message)

    def is_running(self) -> bool:
        return bool(self.process) and (self.process.poll() is None)


class AutoRunManager:
    def __init__(
        self,
        global_config: GlobalConfig,
        strategy_defaults: Dict[str, Any],
        run_params_template: Dict[str, Any],
    ):
        self.config = global_config
        self.strategy_defaults = strategy_defaults
        self.run_params_template = run_params_template or {}
        self.stop_event = threading.Event()
        self.command_queue: "queue.Queue[str]" = queue.Queue()
        self.tasks: Dict[str, TopicTask] = {}
        self.latest_topics: List[Dict[str, Any]] = []
        self.topic_details: Dict[str, Dict[str, Any]] = {}
        self.handled_topics: set[str] = set()
        self.pending_topics: List[str] = []
        self.pending_burst_topics: List[str] = []
        self.pending_exit_topics: List[str] = []
        self._next_topics_refresh: float = 0.0
        self._next_status_dump: float = 0.0
        self._next_topic_start_at: float = 0.0
        self._next_config_reload_check: float = 0.0
        self._config_mtime_ns: Optional[int] = None
        self.status_path = self.config.runtime_status_path
        self._ws_cache_path = self.config.data_dir / "ws_cache.json"
        self._orphan_tokens_path = self.config.data_dir / "orphan_tokens.json"
        self._ws_cache_lock = threading.Lock()
        self._ws_cache: Dict[str, Dict[str, Any]] = {}
        # 【修复】添加 topic_details 锁，保护并发修改
        self._topic_details_lock = threading.Lock()
        self._ws_cache_dirty = False
        self._ws_cache_last_flush = 0.0
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_thread_stop: Optional[threading.Event] = None
        self._ws_token_ids: List[str] = []
        self._ws_recently_active_until: Dict[str, float] = {}
        self._ws_aggregator_thread: Optional[threading.Thread] = None
        self._ws_debug_raw = _env_flag("POLY_WS_DEBUG_RAW") or self.config.ws_debug_raw
        self._refill_debug = _env_flag("POLY_REFILL_DEBUG")
        # 增量订阅客户端（替代完全重启WS的方式）
        self._ws_client: Optional[Any] = None  # WSAggregatorClient 实例
        self._ws_client_lock = threading.RLock()
        self._ws_updates_suspended: bool = False
        # Slot refill (回填) 相关
        self._exit_tokens_path = self.config.data_dir / "exit_tokens.json"
        self._token_cycle_state_path = self.config.token_cycle_state_path
        self._token_cycle_states: Dict[str, Dict[str, Any]] = {}
        self._refill_retry_counts: Dict[str, int] = {}  # token_id -> 已重试次数
        self._next_refill_check: float = 0.0
        self._next_stoploss_check: float = 0.0
        self._next_exit_signal_reconcile_check: float = 0.0
        self._next_inactive_token_reconcile_check: float = 0.0
        self._next_orphan_recovery_probe_check: float = 0.0
        self._refilled_tokens: set[str] = set()  # 已回填的token（避免重复回填）
        # Shared WS 等待保护
        self._shared_ws_wait_failures: Dict[str, int] = {}
        self._shared_ws_paused_until: Dict[str, float] = {}
        self._shared_ws_wait_timeout_events: Dict[str, List[float]] = {}
        self._ws_starting_topics: set[str] = set()  # 启动窗口内保留订阅，避免刚订阅即取消
        self._pending_first_seen: Dict[str, float] = {}
        self._active_unmanaged_rearm_last_ts: Dict[str, float] = {}
        self._active_unmanaged_rearm_recent_ts: Dict[str, List[float]] = {}
        self._active_unmanaged_rearm_suppressed_until: Dict[str, float] = {}
        self._active_unmanaged_rearm_blocked_until: Dict[str, float] = {}
        self._active_unmanaged_rearm_block_reasons: Dict[str, str] = {}
        self._shared_ws_pending_since: Dict[str, float] = {}
        self._clob_book_probe_cache: Dict[str, Dict[str, Any]] = {}
        self._gamma_market_probe_cache: Dict[str, Dict[str, Any]] = {}
        self._next_pending_eviction: float = 0.0
        self._last_sell_fill_poll_at: float = 0.0
        self._process_started_at: float = time.time()
        self._last_self_sell_trade_ts: int = int(self._process_started_at)
        self._seen_self_sell_trade_keys: set[str] = set()
        self._seen_self_sell_tokens: set[str] = set()
        self._price_cap_stuck_since: Dict[str, float] = {}
        self._price_cap_flat_stuck_since: Dict[str, float] = {}
        self._shock_stuck_since: Dict[str, float] = {}
        self._task_run_config_cache: Dict[str, Tuple[str, float, Dict[str, Any]]] = {}
        # 已完成 exit-only cleanup 的 token（避免重复触发清仓）
        self._completed_exit_cleanup_tokens: set[str] = set()
        self._handled_sell_signals: set[str] = set()
        self._sell_exit_deadlines: Dict[str, float] = {}
        self._position_snapshot_cache: Dict[str, Dict[str, Any]] = {}
        self._unified_position_rows: List[Dict[str, Any]] = []
        self._unified_position_snapshot: Dict[str, float] = {}
        self._unified_position_info: str = ""
        self._unified_position_ts: float = 0.0
        self._exit_cleanup_retry_counts: Dict[str, int] = {}
        self._startup_sync_retry_needed: bool = True
        self._next_startup_sync_retry_at: float = 0.0
        self._startup_orphan_profit_sweep_done: bool = False
        self._position_address: Optional[str] = None
        self._position_address_origin: Optional[str] = None
        self._position_address_warned: bool = False
        self._sell_bootstrap_done: bool = False
        self._next_sell_full_recheck_at: float = 0.0
        self._sell_position_snapshot: Dict[str, float] = {}
        self._sell_position_snapshot_info: str = ""
        self._exit_cleanup_suppressed_until: Dict[str, float] = {}
        # WS 健康分层清理状态
        self._ws_recent_recovery_ts: Dict[str, float] = {}
        self._ws_prev_stale_state: Dict[str, bool] = {}
        self._ws_reconnect_reason_counts: Dict[str, int] = {
            "closed": 0,
            "error": 0,
            "silence": 0,
        }
        self._ws_last_reconnect_reason: Optional[str] = None
        self._ws_last_reconnect_detail: Dict[str, Any] = {}
        self._reentry_eligible_tokens: set[str] = set()
        self._reentry_eligible_reasons: Dict[str, str] = {}
        # 日志清理相关（每天清理7天前的日志）
        self._next_log_cleanup: float = 0.0
        self._log_cleanup_interval_sec: float = 3600.0  # 每小时检查一次
        self._log_retention_days: int = 7  # 保留7天
        self._last_cleanup_date: Optional[str] = None  # 记录上次清理日期，避免同一天重复清理
        self._buy_paused_due_to_balance: bool = False
        self._buy_pause_last_balance: Optional[float] = None
        self._buy_pause_deferred_tokens: set[str] = set()
        self._buy_pause_keep_snapshot: set[str] = set()
        self._title_blacklist_finalized_no_position: set[str] = set()
        self._log_throttle_ts: Dict[str, float] = {}
        self._total_liquidation = TotalLiquidationManager(self.config, PROJECT_ROOT)
        
        # =====================
        # 市场状态检测器初始化（新增）
        # =====================
        self._market_state_checker: Optional[MarketStateChecker] = None
        self._market_closed_cleaner: Optional[MarketClosedCleaner] = None
        self._file_io_lock = threading.RLock()  # 文件操作锁（共享给检测器和清理器）
        self._load_token_cycle_states()
        self._stoploss_reentry_state_path = self.config.stoploss_reentry_state_path
        self._stoploss_reentry_state_backup_path = self.config.stoploss_reentry_state_backup_path
        self._stoploss_reentry_states: Dict[str, Dict[str, Any]] = {}
        self._stoploss_quote_cycle_cache: Dict[str, Dict[str, Any]] = {}
        self._load_stoploss_reentry_states()
        
        if MARKET_STATE_CHECKER_AVAILABLE:
            try:
                self._market_state_checker = init_market_state_checker(
                    file_lock=self._file_io_lock
                )
                self._market_closed_cleaner = init_cleaner(
                    file_lock=self._file_io_lock
                )
                print("[INIT] 市场状态检测器已初始化")
            except Exception as e:
                print(f"[WARNING] 市场状态检测器初始化失败: {e}")
                self._market_state_checker = None
                self._market_closed_cleaner = None
        else:
            print("[INIT] 市场状态检测器不可用（模块导入失败）")

        if self.config.global_config_path:
            try:
                self._config_mtime_ns = self.config.global_config_path.stat().st_mtime_ns
            except OSError:
                self._config_mtime_ns = None

    @staticmethod
    def _normalize_ratio_value(raw_value: Any) -> Optional[float]:
        value = _coerce_float(raw_value)
        if value is None:
            return None
        if value > 1:
            value = value / 100.0
        return max(0.0, float(value))

    @staticmethod
    def _normalize_cycle_state_record(raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        try:
            cycle_round = max(0, int(raw.get("cycle_round", 0) or 0))
        except (TypeError, ValueError):
            cycle_round = 0
        try:
            next_buy_allowed_ts = max(0.0, float(raw.get("next_buy_allowed_ts", 0.0) or 0.0))
        except (TypeError, ValueError):
            next_buy_allowed_ts = 0.0
        next_drop_pct = AutoRunManager._normalize_ratio_value(raw.get("next_drop_pct"))
        next_profit_pct = AutoRunManager._normalize_ratio_value(raw.get("next_profit_pct"))
        try:
            last_cycle_completed_ts = max(
                0.0, float(raw.get("last_cycle_completed_ts", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            last_cycle_completed_ts = 0.0
        local_cycle_status = str(raw.get("local_cycle_status") or "idle").strip().lower()
        if local_cycle_status not in {
            "idle",
            "started_not_bought",
            "position_resume",
            "position_confirmed",
            "sell_pending",
            "cycle_closed",
            "invalidated",
        }:
            local_cycle_status = "idle"
        try:
            local_cycle_started_ts = max(
                0.0, float(raw.get("local_cycle_started_ts", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            local_cycle_started_ts = 0.0
        try:
            local_cycle_invalidated_ts = max(
                0.0, float(raw.get("local_cycle_invalidated_ts", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            local_cycle_invalidated_ts = 0.0
        local_cycle_invalidate_reason = str(
            raw.get("local_cycle_invalidate_reason") or ""
        ).strip()
        local_cycle_started_mode = str(raw.get("local_cycle_started_mode") or "").strip().lower()
        stage = str(raw.get("stoploss_stage") or "none").strip().lower()
        if stage not in {"none", "first_cut_done", "fully_cleared"}:
            stage = "none"
        try:
            stoploss_first_cut_ts = max(
                0.0, float(raw.get("stoploss_first_cut_ts", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            stoploss_first_cut_ts = 0.0
        try:
            stoploss_cooldown_until_ts = max(
                0.0, float(raw.get("stoploss_cooldown_until_ts", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            stoploss_cooldown_until_ts = 0.0
        try:
            stoploss_confirm_hits = max(
                0, int(raw.get("stoploss_confirm_hits", 0) or 0)
            )
        except (TypeError, ValueError):
            stoploss_confirm_hits = 0
        stoploss_first_cut_drawdown = _coerce_float(raw.get("stoploss_first_cut_drawdown"))
        try:
            stoploss_last_action_ts = max(
                0.0, float(raw.get("stoploss_last_action_ts", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            stoploss_last_action_ts = 0.0
        try:
            stoploss_position_opened_ts = max(
                0.0, float(raw.get("stoploss_position_opened_ts", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            stoploss_position_opened_ts = 0.0
        normalized = {
            "cycle_round": cycle_round,
            "next_buy_allowed_ts": next_buy_allowed_ts,
            "last_cycle_completed_ts": last_cycle_completed_ts,
            "local_cycle_status": local_cycle_status,
            "local_cycle_started_ts": local_cycle_started_ts,
            "local_cycle_invalidated_ts": local_cycle_invalidated_ts,
            "local_cycle_invalidate_reason": local_cycle_invalidate_reason,
            "local_cycle_started_mode": local_cycle_started_mode,
            "stoploss_stage": stage,
            "stoploss_first_cut_ts": stoploss_first_cut_ts,
            "stoploss_cooldown_until_ts": stoploss_cooldown_until_ts,
            "stoploss_confirm_hits": stoploss_confirm_hits,
            "stoploss_last_action_ts": stoploss_last_action_ts,
            "stoploss_position_opened_ts": stoploss_position_opened_ts,
        }
        if next_drop_pct is not None:
            normalized["next_drop_pct"] = next_drop_pct
        if next_profit_pct is not None:
            normalized["next_profit_pct"] = next_profit_pct
        if stoploss_first_cut_drawdown is not None:
            normalized["stoploss_first_cut_drawdown"] = float(stoploss_first_cut_drawdown)
        return normalized

    @staticmethod
    def _is_likely_real_token_id(token_id: Any) -> bool:
        token = str(token_id or "").strip()
        return len(token) >= 20 and token.isdigit()

    @classmethod
    def _filter_cycle_state_rows(
        cls, rows: Dict[str, Any]
    ) -> tuple[Dict[str, Dict[str, Any]], int]:
        has_real_token = any(cls._is_likely_real_token_id(token_id) for token_id in rows.keys())
        normalized: Dict[str, Dict[str, Any]] = {}
        dropped = 0
        for token_id, raw_record in rows.items():
            token = str(token_id).strip()
            if not token:
                dropped += 1
                continue
            if has_real_token and not cls._is_likely_real_token_id(token):
                dropped += 1
                continue
            record = cls._normalize_cycle_state_record(raw_record)
            if record is None:
                dropped += 1
                continue
            normalized[token] = record
        return normalized, dropped

    def _load_token_cycle_states(self) -> None:
        with self._file_io_lock:
            if not self._token_cycle_state_path.exists():
                self._token_cycle_states = {}
                return
            try:
                with self._token_cycle_state_path.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[CYCLE_GATE][WARN] 读取状态失败: {exc}")
                self._token_cycle_states = {}
                return
            token_states = payload.get("token_states") if isinstance(payload, dict) else {}
            if not isinstance(token_states, dict):
                self._token_cycle_states = {}
                return
            normalized, dropped = self._filter_cycle_state_rows(token_states)
            self._token_cycle_states = normalized
        if dropped:
            print(f"[CYCLE_GATE][CLEANUP] 已忽略 {dropped} 条占位/脏 token 状态")
        if self._token_cycle_states:
            print(f"[CYCLE_GATE] 已加载 {len(self._token_cycle_states)} 条 token 周期状态")

    def _save_token_cycle_states(self) -> None:
        with self._file_io_lock:
            disk_states: Dict[str, Dict[str, Any]] = {}
            if self._token_cycle_state_path.exists():
                try:
                    with self._token_cycle_state_path.open("r", encoding="utf-8") as f:
                        payload = json.load(f)
                    raw_token_states = payload.get("token_states") if isinstance(payload, dict) else {}
                    if isinstance(raw_token_states, dict):
                        disk_states, _ = self._filter_cycle_state_rows(raw_token_states)
                except (json.JSONDecodeError, OSError):
                    disk_states = {}

            merged_states: Dict[str, Dict[str, Any]] = dict(disk_states)
            for token_id, raw_record in self._token_cycle_states.items():
                memory_state = self._normalize_cycle_state_record(raw_record)
                if memory_state is None:
                    continue
                disk_state = disk_states.get(token_id)
                if not disk_state:
                    merged_states[token_id] = memory_state
                    continue

                memory_round = int(memory_state.get("cycle_round", 0) or 0)
                disk_round = int(disk_state.get("cycle_round", 0) or 0)
                memory_last_completed = float(
                    memory_state.get("last_cycle_completed_ts", 0.0) or 0.0
                )
                disk_last_completed = float(
                    disk_state.get("last_cycle_completed_ts", 0.0) or 0.0
                )
                memory_next_buy = float(memory_state.get("next_buy_allowed_ts", 0.0) or 0.0)
                disk_next_buy = float(disk_state.get("next_buy_allowed_ts", 0.0) or 0.0)
                disk_cycle_is_newer = (
                    disk_round > memory_round
                    or (
                        disk_round == memory_round
                        and (
                            disk_last_completed > memory_last_completed
                            or (
                                abs(disk_last_completed - memory_last_completed) <= 1e-9
                                and disk_next_buy > memory_next_buy
                            )
                        )
                    )
                )

                merged_state = dict(memory_state)
                if disk_cycle_is_newer:
                    for key in (
                        "cycle_round",
                        "next_buy_allowed_ts",
                        "last_cycle_completed_ts",
                        "next_drop_pct",
                        "next_profit_pct",
                    ):
                        if key in disk_state:
                            merged_state[key] = disk_state[key]
                        else:
                            merged_state.pop(key, None)
                merged_states[token_id] = self._normalize_cycle_state_record(merged_state) or merged_state

            merged_states, dropped = self._filter_cycle_state_rows(merged_states)
            self._token_cycle_states = merged_states
            payload = {
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "token_states": merged_states,
            }
            try:
                _atomic_json_write(self._token_cycle_state_path, payload)
            except OSError as exc:
                print(f"[CYCLE_GATE][WARN] 写入状态失败: {exc}")
            else:
                if dropped:
                    print(f"[CYCLE_GATE][CLEANUP] 已清理 {dropped} 条占位/脏 token 状态")

    @staticmethod
    def _today_utc_date(now: Optional[float] = None) -> str:
        ts = time.time() if now is None else float(now)
        return time.strftime("%Y-%m-%d", time.gmtime(ts))

    def _default_stoploss_reentry_state(self, token_id: str) -> Dict[str, Any]:
        base_threshold = max(0.001, float(self.config.stoploss_base_drawdown_pct))
        return {
            "token_id": str(token_id),
            "version": 4,
            "state": "NORMAL_MAKER",
            "stoploss_cycle_count": 0,
            "next_stoploss_threshold_pct": base_threshold,
            "next_stoploss_trigger_ticks": 0,
            "stoploss_confirm_hits": 0,
            "stop_exit_price": None,
            "stop_exit_price_source": "",
            "stop_exit_ts": 0.0,
            "last_stoploss_size": 0.0,
            "reentry_line_price": None,
            "reentry_zone_lower_price": None,
            "probe_line_price": None,
            "probe_seen": False,
            "probe_seen_ts": 0.0,
            "probe_confirm_hits": 0,
            "rebound_confirm_hits": 0,
            "reentry_earliest_ts": 0.0,
            "next_stoploss_earliest_ts": 0.0,
            "old_maker_floor_price": None,
            "old_maker_profit_pct": None,
            "old_entry_price": None,
            "old_last_buy_price": None,
            "daily_stoploss_full_clear_count": 0,
            "today_realized_loss_pct": 0.0,
            "loss_date_utc": self._today_utc_date(),
            "reentry_paused_for_day": False,
            "source_detached": False,
            "source_detached_since_ts": 0.0,
            "source_detached_cleanup_started": False,
            "source_detached_cleanup_started_ts": 0.0,
            "market_status_last": "",
            "last_price_check_ts": 0.0,
            "last_position_seen_ts": 0.0,
            "position_opened_ts": 0.0,
            "last_error": "",
            "reentry_quote_missing_hits": 0,
            "closed_final_ts": 0.0,
            "closed_final_reason": "",
            "pending_cleanup_since_ts": 0.0,
            "pending_confirm_started_ts": 0.0,
            "pending_confirm_next_retry_ts": 0.0,
            "pending_confirm_retry_count": 0,
            "pending_confirm_before_size": 0.0,
            "pending_confirm_after_size_snapshot": 0.0,
            "pending_confirm_filled_size": 0.0,
            "pending_confirm_exec_price": None,
            "pending_confirm_exec_price_source": "",
            "pending_confirm_drawdown": 0.0,
            "nofill_retry_count": 0,
            "nofill_next_retry_ts": 0.0,
            "last_reentry_above_line": False,
            "last_reentry_above_line_price": None,
        }

    def _normalize_stoploss_reentry_state_record(
        self, token_id: str, raw: Any
    ) -> Dict[str, Any]:
        base = self._default_stoploss_reentry_state(token_id)
        if not isinstance(raw, dict):
            return base
        merged = dict(base)
        merged.update(raw)
        merged["token_id"] = str(token_id)
        merged["version"] = 4
        merged["state"] = str(merged.get("state") or "NORMAL_MAKER")
        merged["stoploss_cycle_count"] = max(
            0, int(_coerce_float(merged.get("stoploss_cycle_count")) or 0)
        )
        merged["stoploss_confirm_hits"] = max(
            0, int(_coerce_float(merged.get("stoploss_confirm_hits")) or 0)
        )
        merged["daily_stoploss_full_clear_count"] = max(
            0, int(_coerce_float(merged.get("daily_stoploss_full_clear_count")) or 0)
        )
        merged["probe_confirm_hits"] = max(
            0, int(_coerce_float(merged.get("probe_confirm_hits")) or 0)
        )
        merged["rebound_confirm_hits"] = max(
            0, int(_coerce_float(merged.get("rebound_confirm_hits")) or 0)
        )
        merged["pending_confirm_retry_count"] = max(
            0, int(_coerce_float(merged.get("pending_confirm_retry_count")) or 0)
        )
        merged["nofill_retry_count"] = max(
            0, int(_coerce_float(merged.get("nofill_retry_count")) or 0)
        )
        for key in (
            "next_stoploss_threshold_pct",
            "next_stoploss_trigger_ticks",
            "stop_exit_price",
            "stop_exit_ts",
            "last_stoploss_size",
            "reentry_line_price",
            "reentry_zone_lower_price",
            "probe_line_price",
            "probe_seen_ts",
            "reentry_earliest_ts",
            "next_stoploss_earliest_ts",
            "old_maker_floor_price",
            "old_maker_profit_pct",
            "old_entry_price",
            "old_last_buy_price",
            "today_realized_loss_pct",
            "source_detached_since_ts",
            "source_detached_cleanup_started_ts",
            "last_price_check_ts",
            "last_position_seen_ts",
            "position_opened_ts",
            "closed_final_ts",
            "pending_cleanup_since_ts",
            "pending_confirm_started_ts",
            "pending_confirm_next_retry_ts",
            "pending_confirm_before_size",
            "pending_confirm_after_size_snapshot",
            "pending_confirm_filled_size",
            "pending_confirm_exec_price",
            "pending_confirm_drawdown",
            "nofill_next_retry_ts",
        ):
            val = _coerce_float(merged.get(key))
            merged[key] = float(val) if val is not None else None
        merged["stop_exit_ts"] = max(0.0, float(merged.get("stop_exit_ts") or 0.0))
        merged["probe_seen_ts"] = max(0.0, float(merged.get("probe_seen_ts") or 0.0))
        merged["closed_final_ts"] = max(0.0, float(merged.get("closed_final_ts") or 0.0))
        merged["reentry_earliest_ts"] = max(0.0, float(merged.get("reentry_earliest_ts") or 0.0))
        merged["next_stoploss_earliest_ts"] = max(0.0, float(merged.get("next_stoploss_earliest_ts") or 0.0))
        merged["last_stoploss_size"] = max(0.0, float(merged.get("last_stoploss_size") or 0.0))
        merged["next_stoploss_threshold_pct"] = max(
            0.001,
            float(merged.get("next_stoploss_threshold_pct") or self.config.stoploss_base_drawdown_pct),
        )
        merged["next_stoploss_trigger_ticks"] = max(
            0, int(_coerce_float(merged.get("next_stoploss_trigger_ticks")) or 0)
        )
        merged["today_realized_loss_pct"] = max(0.0, float(merged.get("today_realized_loss_pct") or 0.0))
        merged["source_detached_since_ts"] = max(0.0, float(merged.get("source_detached_since_ts") or 0.0))
        merged["source_detached_cleanup_started_ts"] = max(
            0.0, float(merged.get("source_detached_cleanup_started_ts") or 0.0)
        )
        merged["last_position_seen_ts"] = max(0.0, float(merged.get("last_position_seen_ts") or 0.0))
        merged["position_opened_ts"] = max(0.0, float(merged.get("position_opened_ts") or 0.0))
        merged["pending_cleanup_since_ts"] = max(0.0, float(merged.get("pending_cleanup_since_ts") or 0.0))
        merged["pending_confirm_started_ts"] = max(
            0.0, float(merged.get("pending_confirm_started_ts") or 0.0)
        )
        merged["pending_confirm_next_retry_ts"] = max(
            0.0, float(merged.get("pending_confirm_next_retry_ts") or 0.0)
        )
        merged["pending_confirm_before_size"] = max(
            0.0, float(merged.get("pending_confirm_before_size") or 0.0)
        )
        merged["pending_confirm_after_size_snapshot"] = max(
            0.0, float(merged.get("pending_confirm_after_size_snapshot") or 0.0)
        )
        merged["pending_confirm_filled_size"] = max(
            0.0, float(merged.get("pending_confirm_filled_size") or 0.0)
        )
        merged["pending_confirm_drawdown"] = float(merged.get("pending_confirm_drawdown") or 0.0)
        merged["nofill_next_retry_ts"] = max(0.0, float(merged.get("nofill_next_retry_ts") or 0.0))
        merged["probe_seen"] = bool(merged.get("probe_seen", False))
        merged["reentry_paused_for_day"] = bool(merged.get("reentry_paused_for_day", False))
        merged["source_detached"] = bool(merged.get("source_detached", False))
        merged["source_detached_cleanup_started"] = bool(
            merged.get("source_detached_cleanup_started", False)
        )
        merged["loss_date_utc"] = str(merged.get("loss_date_utc") or self._today_utc_date())
        merged["market_status_last"] = str(merged.get("market_status_last") or "")
        merged["last_error"] = str(merged.get("last_error") or "")
        merged["stop_exit_price_source"] = str(merged.get("stop_exit_price_source") or "")
        merged["closed_final_reason"] = str(merged.get("closed_final_reason") or "")
        merged["pending_confirm_exec_price_source"] = str(
            merged.get("pending_confirm_exec_price_source") or ""
        )
        merged["reentry_quote_missing_hits"] = max(
            0, int(_coerce_float(merged.get("reentry_quote_missing_hits")) or 0)
        )
        # Legacy informational field retained for observability; pause gating now uses
        # daily_stoploss_full_clear_count plus reentry_paused_for_day.
        self._sync_stoploss_pause_status_fields(merged)
        self._sanitize_stoploss_runtime_release_state(merged)
        return merged

    @staticmethod
    def _sanitize_stoploss_runtime_release_state(state: Dict[str, Any]) -> None:
        state_name = str(state.get("state") or "").strip().upper()
        if state_name != "NORMAL_MAKER":
            return
        if str(state.get("market_status_last") or "").strip() == "source_detached_guard_hold":
            state["market_status_last"] = "source_detached"
        last_error = str(state.get("last_error") or "").strip()
        if last_error == "source_detached guard hold (within grace)":
            state["last_error"] = ""
        for key in (
            "pending_stoploss_before_size",
            "pending_stoploss_after_size",
            "pending_stoploss_exit_price",
            "pending_stoploss_exec_source",
            "pending_stoploss_drawdown",
        ):
            state.pop(key, None)

    def _load_stoploss_reentry_states(self) -> None:
        with self._file_io_lock:
            payload: Any = {}
            loaded = False
            for path in (
                self._stoploss_reentry_state_path,
                self._stoploss_reentry_state_backup_path,
            ):
                if not path.exists():
                    continue
                try:
                    with path.open("r", encoding="utf-8") as f:
                        payload = json.load(f)
                    loaded = True
                    break
                except (json.JSONDecodeError, OSError):
                    continue
            if not loaded:
                self._stoploss_reentry_states = {}
                return
            rows = payload.get("token_states") if isinstance(payload, dict) else {}
            if not isinstance(rows, dict):
                self._stoploss_reentry_states = {}
                return
            normalized: Dict[str, Dict[str, Any]] = {}
            for token_id, raw in rows.items():
                token = str(token_id).strip()
                if not token:
                    continue
                normalized[token] = self._normalize_stoploss_reentry_state_record(token, raw)
            self._stoploss_reentry_states = normalized
            self._save_stoploss_reentry_states()

    def _save_stoploss_reentry_states(self) -> None:
        normalized_states: Dict[str, Dict[str, Any]] = {}
        for token_id, raw in self._stoploss_reentry_states.items():
            token = str(token_id).strip()
            if not token:
                continue
            normalized_states[token] = self._normalize_stoploss_reentry_state_record(token, raw)
        self._stoploss_reentry_states = normalized_states
        payload = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": 4,
            "token_states": self._stoploss_reentry_states,
        }
        with self._file_io_lock:
            for path in (self._stoploss_reentry_state_path, self._stoploss_reentry_state_backup_path):
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_json_write(path, payload)
                except OSError:
                    continue

    def _append_stoploss_event_journal(
        self,
        token_id: str,
        event: str,
        *,
        stage: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        record = {
            "ts": float(time.time()),
            "token_id": str(token_id),
            "event": str(event),
            "stage": str(stage),
            "source": "autorun_stoploss",
        }
        if isinstance(payload, dict) and payload:
            record["data"] = payload
        path = self.config.data_dir / "stoploss_event_journal.jsonl"
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            with self._file_io_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(line)
        except OSError as exc:
            print(
                f"[STOPLOSS][JOURNAL][WARN] token={token_id[:20]}... "
                f"event={event} write_failed={exc}"
            )

    def _remove_stoploss_reentry_state(self, token_id: str) -> None:
        self._stoploss_reentry_states.pop(str(token_id), None)

    def _update_stoploss_source_detached_flags(
        self,
        copytrade_scope: set[str],
        *,
        now: float,
        scope_available: bool,
    ) -> bool:
        changed = False
        if not scope_available:
            return False
        for token_id, state in self._stoploss_reentry_states.items():
            detached = token_id not in copytrade_scope
            prev_detached = bool(state.get("source_detached", False))
            if prev_detached != detached:
                state["source_detached"] = detached
                state["source_detached_since_ts"] = float(max(now, 0.0)) if detached else 0.0
                state["pending_cleanup_since_ts"] = 0.0
                if not detached:
                    state["source_detached_cleanup_started"] = False
                    state["source_detached_cleanup_started_ts"] = 0.0
                changed = True
                continue
            if detached and float(state.get("source_detached_since_ts") or 0.0) <= 0.0:
                state["source_detached_since_ts"] = float(max(now, 0.0))
                changed = True
        return changed

    @staticmethod
    def _rollover_stoploss_daily_fields(state: Dict[str, Any], today: str) -> bool:
        if str(state.get("loss_date_utc") or "") == str(today):
            return False
        state["loss_date_utc"] = str(today)
        state["daily_stoploss_full_clear_count"] = 0
        state["today_realized_loss_pct"] = 0.0
        state["reentry_paused_for_day"] = False
        return True

    @staticmethod
    def _sync_stoploss_pause_status_fields(state: Dict[str, Any]) -> None:
        if not isinstance(state, dict):
            return
        paused = bool(state.get("reentry_paused_for_day", False))
        status = str(state.get("market_status_last") or "")
        if paused:
            state["market_status_last"] = "reentry_paused_for_day"
            return
        if status == "reentry_paused_for_day":
            state["market_status_last"] = ""

    def _estimate_token_tick_size(
        self, token_id: str, position_row: Optional[Dict[str, Any]] = None
    ) -> float:
        cache = self._ws_cache.get(token_id) or {}
        for key in ("tick_size", "min_tick_size", "tickSize", "minimum_tick_size", "minimumTickSize"):
            val = _coerce_float(cache.get(key))
            if val is not None and val > 0:
                return float(val)
        client_tick = self._fetch_tick_size_from_official_client(token_id)
        if client_tick is not None and client_tick > 0:
            self._cache_token_tick_size(token_id, client_tick)
            return float(client_tick)
        quote = self._fetch_clob_top_of_book(token_id)
        quote_tick = _coerce_float((quote or {}).get("tick_size"))
        if quote_tick is not None and quote_tick > 0:
            self._cache_token_tick_size(token_id, quote_tick)
            return float(quote_tick)
        return 0.0001

    def _cache_token_tick_size(self, token_id: str, tick_size: Any) -> None:
        tick = _coerce_float(tick_size)
        if not token_id or tick is None or tick <= 0:
            return
        with self._ws_cache_lock:
            cache = self._ws_cache.get(token_id) or {}
            cache["tick_size"] = float(tick)
            cache["updated_at"] = float(time.time())
            self._ws_cache[token_id] = cache
            self._ws_cache_dirty = True

    @staticmethod
    def _extract_tick_size_value(payload: Any) -> Optional[float]:
        if not isinstance(payload, dict):
            return None
        for key in (
            "new_tick_size",
            "newTickSize",
            "tick_size",
            "min_tick_size",
            "tickSize",
            "minimum_tick_size",
            "minimumTickSize",
        ):
            val = _coerce_float(payload.get(key))
            if val is not None and 0 < val < 1:
                return float(val)
        return None

    def _fetch_tick_size_from_official_client(self, token_id: str) -> Optional[float]:
        if not token_id:
            return None
        try:
            from Volatility_arbitrage_main_rest import get_client

            client = get_client()
        except Exception:
            return None
        getter = getattr(client, "get_tick_size", None)
        if not callable(getter):
            getter = getattr(client, "getTickSize", None)
        if not callable(getter):
            return None
        try:
            tick = _coerce_float(getter(token_id))
        except Exception:
            return None
        if tick is None or tick <= 0:
            return None
        return float(tick)

    def _stoploss_threshold_ticks(
        self,
        *,
        anchor_price: float,
        threshold_pct: float,
        tick: float,
        cycle_count: int = 0,
    ) -> int:
        reference_tick = max(1e-8, float(self.config.stoploss_reference_tick_size))
        reference_ticks = _ticks_for_price_move(
            anchor_price,
            threshold_pct,
            reference_tick,
            min_ticks=2,
            coarse_min_ticks=2,
        )
        reference_ticks += max(0, int(self.config.stoploss_drawdown_step_per_cycle_ticks)) * max(
            0, int(cycle_count)
        )
        move = float(reference_ticks) * reference_tick
        return max(1, int(math.ceil(move / max(float(tick), 1e-8) - 1e-12)))

    def _build_stoploss_reentry_band(
        self,
        *,
        token_id: str,
        exec_price: float,
        line_ticks: int,
        zone_lower_pct: float,
        probe_break_pct: float,
    ) -> Tuple[float, float, float]:
        tick = max(1e-8, float(self._estimate_token_tick_size(token_id, None)))
        reference_tick = max(1e-8, float(self.config.stoploss_reference_tick_size))
        reference_line_ticks = max(1, int(line_ticks))
        reference_lower_ticks = max(
            reference_line_ticks * 2,
            _ticks_for_price_move(
                exec_price,
                zone_lower_pct,
                reference_tick,
                min_ticks=1,
                coarse_min_ticks=1,
            ),
        )
        reference_probe_ticks = max(
            reference_lower_ticks + 2,
            _ticks_for_price_move(
                exec_price,
                probe_break_pct,
                reference_tick,
                min_ticks=1,
                coarse_min_ticks=1,
            ),
        )
        actual_line_ticks = max(
            1, int(math.ceil((float(reference_line_ticks) * reference_tick) / tick - 1e-12))
        )
        actual_lower_ticks = max(
            actual_line_ticks + 1,
            int(math.ceil((float(reference_lower_ticks) * reference_tick) / tick - 1e-12)),
        )
        actual_probe_ticks = max(
            actual_lower_ticks + 1,
            int(math.ceil((float(reference_probe_ticks) * reference_tick) / tick - 1e-12)),
        )
        reentry_line_epsilon = min(float(tick) * 0.1, 1e-6)
        reentry_line = max(0.0, float(exec_price) - float(actual_line_ticks) * tick - reentry_line_epsilon)
        zone_lower = max(0.0, float(exec_price) - float(actual_lower_ticks) * tick)
        if zone_lower >= reentry_line:
            zone_lower = max(0.0, reentry_line - float(tick))
        probe_line = max(0.0, float(exec_price) - float(actual_probe_ticks) * tick)
        if probe_line >= zone_lower:
            probe_line = max(0.0, zone_lower - float(tick))
        return (reentry_line, zone_lower, probe_line)

    def _resolve_profit_pct_for_token(self, token_id: str) -> float:
        detail = self.topic_details.get(token_id) or {}
        resume = detail.get("resume_state") if isinstance(detail, dict) else {}
        value = _coerce_float((resume or {}).get("profit_pct")) if isinstance(resume, dict) else None
        if value is not None:
            return max(0.0, float(value))
        return 0.01

    def _estimate_reentry_buyable_price(
        self, token_id: str, position_row: Optional[Dict[str, Any]] = None
    ) -> Optional[float]:
        # Use live CLOB book first to avoid stale WS cache blocking reentry checks.
        quote = self._get_stoploss_cycle_quote(token_id)
        ask = _coerce_float((quote or {}).get("ask"))
        if ask is not None and ask > 0:
            return float(ask)
        cache = self._ws_cache.get(token_id) or {}
        ask = _coerce_float(cache.get("best_ask"))
        updated_at = _coerce_float(cache.get("updated_at"))
        if ask is not None and ask > 0 and updated_at is not None and (time.time() - updated_at) <= 120.0:
            return float(ask)
        return None

    @staticmethod
    def _extract_book_side_best_price(levels: Any, *, is_bid: bool) -> Optional[float]:
        if not isinstance(levels, list) or not levels:
            return None
        best: Optional[float] = None
        for lv in levels:
            if isinstance(lv, dict):
                px = _coerce_float(lv.get("price"))
                sz = _coerce_float(lv.get("size"))
                # Ignore empty levels when size is provided.
                if sz is not None and sz <= 0:
                    continue
            elif isinstance(lv, (list, tuple)) and len(lv) >= 1:
                px = _coerce_float(lv[0])
            else:
                px = _coerce_float(lv)
            if px is None or px <= 0:
                continue
            if best is None:
                best = float(px)
            elif is_bid:
                if px > best:
                    best = float(px)
            else:
                if px < best:
                    best = float(px)
        return best

    @staticmethod
    def _extract_book_tick_size(payload: Any) -> Optional[float]:
        return AutoRunManager._extract_tick_size_value(payload)

    def _fetch_clob_top_of_book(self, token_id: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"bid": None, "ask": None, "tick_size": None, "source": "clob_book", "ok": False}
        if not token_id:
            out["error"] = "empty_token"
            return out
        try:
            resp = requests.get(
                f"{CLOB_API_ROOT}/book",
                params={"token_id": token_id},
                timeout=max(1.0, float(CLOB_BOOK_PROBE_TIMEOUT_SEC)),
            )
            if resp.status_code == 404:
                out["error"] = "book_404"
                return out
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, dict):
                out["error"] = "book_payload_invalid"
                return out
            tick_size = self._extract_book_tick_size(payload)
            if tick_size is not None and tick_size > 0:
                out["tick_size"] = float(tick_size)
            bid = self._extract_book_side_best_price(
                payload.get("bids") if payload.get("bids") is not None else payload.get("buys"),
                is_bid=True,
            )
            ask = self._extract_book_side_best_price(
                payload.get("asks") if payload.get("asks") is not None else payload.get("sells"),
                is_bid=False,
            )
            if bid is None:
                bid = _coerce_float(payload.get("best_bid") or payload.get("bid"))
            if ask is None:
                ask = _coerce_float(payload.get("best_ask") or payload.get("ask"))
            if bid is not None and bid > 0:
                out["bid"] = float(bid)
            if ask is not None and ask > 0:
                out["ask"] = float(ask)
            out["ok"] = bool((out.get("bid") or 0.0) > 0 or (out.get("ask") or 0.0) > 0)
            if not out["ok"]:
                out["error"] = "book_empty"
        except Exception as exc:
            out["error"] = f"book_request_failed:{exc.__class__.__name__}"
        return out

    def _get_stoploss_cycle_quote(self, token_id: str) -> Dict[str, Any]:
        cached = self._stoploss_quote_cycle_cache.get(token_id)
        if isinstance(cached, dict):
            return cached
        quote = self._fetch_clob_top_of_book(token_id)
        if not bool(quote.get("ok")):
            # Fallback to WS snapshot to avoid hard-block when /book has transient errors.
            ws = self._ws_cache.get(token_id) or {}
            bid = _coerce_float(ws.get("best_bid"))
            ask = _coerce_float(ws.get("best_ask"))
            updated_at = _coerce_float(ws.get("updated_at"))
            if (
                updated_at is not None
                and updated_at > 0
                and (time.time() - updated_at) <= 120.0
                and ((bid is not None and bid > 0) or (ask is not None and ask > 0))
            ):
                quote = {
                    "bid": float(bid) if bid is not None and bid > 0 else None,
                    "ask": float(ask) if ask is not None and ask > 0 else None,
                    "source": "ws_fallback",
                    "ok": True,
                }
        self._stoploss_quote_cycle_cache[token_id] = quote
        return quote

    def _stoploss_is_market_closed(self, token_id: str) -> bool:
        data = self._ws_cache.get(token_id) or {}
        return bool(data.get("market_closed") or data.get("is_closed") or data.get("closed"))

    def _queue_reentry_task_after_buy(
        self,
        token_id: str,
        *,
        position_size: float,
        entry_price: float,
        hold_floor_price: Optional[float],
        hold_profit_pct: float,
    ) -> None:
        detail = self.topic_details.setdefault(token_id, {})
        detail["resume_state"] = {
            "has_position": True,
            "position_size": float(max(position_size, 0.0)),
            "entry_price": float(entry_price),
            "skip_buy": True,
        }
        detail["queue_role"] = "reentry_token"
        detail["schedule_lane"] = "burst"
        detail["stoploss_reentry_resume"] = {
            "enabled": True,
            "hold_floor_price": hold_floor_price,
            "hold_profit_pct": hold_profit_pct,
            "state": "REENTRY_HOLD",
        }
        self._enqueue_burst_topic(token_id, promote=True)

    def _sync_reentry_hold_recovered_from_log(self, task: TopicTask) -> bool:
        excerpt = str(task.log_excerpt or "")
        if "[REENTRY_HOLD]" not in excerpt:
            return False
        token_id = str(task.topic_id or "").strip()
        if not token_id:
            return False
        state = self._stoploss_reentry_states.get(token_id)
        if not isinstance(state, dict):
            return False
        if str(state.get("state") or "") != "REENTRY_HOLD":
            return False
        state["state"] = "NORMAL_MAKER"
        state["last_error"] = ""
        state["market_status_last"] = "reentry_hold_recovered"
        self._save_stoploss_reentry_states()
        return True

    @staticmethod
    def _config_requires_buy_stage(run_cfg: Dict[str, Any]) -> bool:
        if bool(run_cfg.get("exit_only")):
            return False
        if bool(run_cfg.get("force_sell_only_on_startup")):
            return False
        resume_state = run_cfg.get("resume_state")
        if isinstance(resume_state, dict) and bool(resume_state.get("skip_buy")):
            return False
        return True

    @staticmethod
    def _resolve_drop_step_and_cap(run_cfg: Dict[str, Any]) -> tuple[float, Optional[float]]:
        enabled = bool(run_cfg.get("enable_incremental_drop_pct", False))
        if not enabled:
            return 0.0, AutoRunManager._normalize_ratio_value(
                run_cfg.get("incremental_drop_pct_cap")
            )
        step = AutoRunManager._normalize_ratio_value(run_cfg.get("incremental_drop_pct_step"))
        if step is None:
            step = 0.0
        cap = AutoRunManager._normalize_ratio_value(run_cfg.get("incremental_drop_pct_cap"))
        return max(0.0, step), cap

    @staticmethod
    def _resolve_profit_step_and_cap(run_cfg: Dict[str, Any]) -> tuple[float, Optional[float]]:
        enabled = bool(run_cfg.get("enable_incremental_profit_pct", False))
        if not enabled:
            return 0.0, AutoRunManager._normalize_ratio_value(
                run_cfg.get("incremental_profit_pct_cap")
            )
        step = AutoRunManager._normalize_ratio_value(run_cfg.get("incremental_profit_pct_step"))
        if step is None:
            step = 0.0
        cap = AutoRunManager._normalize_ratio_value(run_cfg.get("incremental_profit_pct_cap"))
        return max(0.0, step), cap

    def _resolve_current_drop_pct_for_cycle(
        self, token_id: str, run_cfg: Dict[str, Any]
    ) -> Optional[float]:
        if not bool(run_cfg.get("enable_incremental_drop_pct", False)):
            return self._normalize_ratio_value(run_cfg.get("drop_pct"))
        state = self._token_cycle_states.get(token_id) or {}
        state_drop = self._normalize_ratio_value(state.get("next_drop_pct"))
        base_drop = self._normalize_ratio_value(run_cfg.get("drop_pct"))
        resume_drop = self._normalize_ratio_value(run_cfg.get("resume_drop_pct"))
        candidates = [v for v in (state_drop, base_drop, resume_drop) if v is not None]
        if not candidates:
            return None
        return max(candidates)

    def _resolve_current_profit_pct_for_cycle(
        self, token_id: str, run_cfg: Dict[str, Any]
    ) -> Optional[float]:
        if not bool(run_cfg.get("enable_incremental_profit_pct", False)):
            return self._normalize_ratio_value(run_cfg.get("profit_pct"))
        state = self._token_cycle_states.get(token_id) or {}
        state_profit = self._normalize_ratio_value(state.get("next_profit_pct"))
        base_profit = self._normalize_ratio_value(run_cfg.get("profit_pct"))
        resume_profit = self._normalize_ratio_value(run_cfg.get("resume_profit_pct"))
        candidates = [v for v in (state_profit, base_profit, resume_profit) if v is not None]
        if not candidates:
            return None
        return max(candidates)

    def _resolve_market_min_order_size_hint(
        self,
        token_id: str,
        *,
        row: Optional[Dict[str, Any]] = None,
        run_cfg: Optional[Dict[str, Any]] = None,
    ) -> float:
        for payload in (
            row,
            self.topic_details.get(token_id),
            run_cfg,
        ):
            min_order_size = extract_market_min_order_size(
                payload,
                default=None,
            )
            if min_order_size is not None and min_order_size > 0:
                return float(min_order_size)
        return float(POSITION_TRUTH_FALLBACK_MIN_ORDER_SIZE)

    def _classify_position_truth(
        self,
        token_id: str,
        position_size: Any,
        *,
        row: Optional[Dict[str, Any]] = None,
        run_cfg: Optional[Dict[str, Any]] = None,
    ) -> str:
        market_min_order_size = self._resolve_market_min_order_size_hint(
            token_id,
            row=row,
            run_cfg=run_cfg,
        )
        return classify_position_truth(
            position_size,
            market_min_order_size=market_min_order_size,
            fallback_actionable_min_order_size=market_min_order_size,
        )

    def _find_position_row(
        self,
        rows: List[Dict[str, Any]],
        token_id: str,
    ) -> Optional[Dict[str, Any]]:
        for row in rows:
            if _extract_position_token_id(row) == token_id:
                return row
        return None

    def _get_position_truth_from_snapshot(
        self,
        token_id: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[str, float, Optional[Dict[str, Any]], str]:
        rows, snapshot, info, _source = self._refresh_unified_position_snapshot(
            force_refresh=force_refresh
        )
        if info != "ok":
            normalized_size = float(snapshot.get(token_id, 0.0) or 0.0)
            truth = self._classify_position_truth(token_id, normalized_size)
            return truth, normalized_size, None, info
        row = self._find_position_row(rows, token_id)
        normalized_size = float(snapshot.get(token_id, 0.0) or 0.0)
        truth = self._classify_position_truth(
            token_id,
            normalized_size,
            row=row,
        )
        return truth, normalized_size, row, info

    def _is_position_dust(
        self,
        token_id: str,
        size: Any,
        *,
        row: Optional[Dict[str, Any]] = None,
        run_cfg: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return self._classify_position_truth(
            token_id,
            size,
            row=row,
            run_cfg=run_cfg,
        ) == POSITION_TRUTH_DUST

    def _has_actionable_position(
        self,
        token_id: str,
        size: Any,
        *,
        row: Optional[Dict[str, Any]] = None,
        run_cfg: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return self._classify_position_truth(
            token_id,
            size,
            row=row,
            run_cfg=run_cfg,
        ) == POSITION_TRUTH_ACTIONABLE

    def _advance_token_cycle_state_on_round_close(
        self, token_id: str, run_cfg: Dict[str, Any]
    ) -> None:
        if not token_id:
            return
        now = time.time()
        current = self._token_cycle_states.get(token_id) or {}
        current_status = str(current.get("local_cycle_status") or "").strip().lower()
        if current_status == "cycle_closed":
            return
        current_round = max(0, int(current.get("cycle_round", 0) or 0))
        next_round = current_round + 1
        cooldown_minutes = float(2 ** max(next_round - 1, 0))
        next_buy_allowed_ts = now + cooldown_minutes * 60.0
        current_drop = self._resolve_current_drop_pct_for_cycle(token_id, run_cfg)
        step, cap = self._resolve_drop_step_and_cap(run_cfg)
        next_drop: Optional[float] = current_drop
        if current_drop is not None and step > 0:
            next_drop = current_drop + step
            if cap is not None:
                next_drop = min(next_drop, max(cap, current_drop))
        current_profit = self._resolve_current_profit_pct_for_cycle(token_id, run_cfg)
        profit_step, profit_cap = self._resolve_profit_step_and_cap(run_cfg)
        next_profit: Optional[float] = current_profit
        if current_profit is not None and profit_step > 0:
            next_profit = current_profit + profit_step
            if profit_cap is not None:
                next_profit = min(next_profit, max(profit_cap, current_profit))
        record: Dict[str, Any] = {
            "cycle_round": next_round,
            "next_buy_allowed_ts": next_buy_allowed_ts,
            "last_cycle_completed_ts": now,
            "local_cycle_status": "cycle_closed",
            "local_cycle_started_ts": 0.0,
            "local_cycle_invalidated_ts": 0.0,
            "local_cycle_invalidate_reason": "",
            "local_cycle_started_mode": "",
        }
        if next_drop is not None:
            record["next_drop_pct"] = next_drop
        if next_profit is not None:
            record["next_profit_pct"] = next_profit
        self._token_cycle_states[token_id] = record
        self._save_token_cycle_states()
        next_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(next_buy_allowed_ts))
        if next_drop is not None or next_profit is not None:
            drop_text = f"{next_drop:.6f}" if next_drop is not None else "None"
            profit_text = f"{next_profit:.6f}" if next_profit is not None else "None"
            print(
                f"[CYCLE_GATE] token={token_id[:16]}... round={next_round} "
                f"next_buy_after={next_text} next_drop_pct={drop_text} "
                f"next_profit_pct={profit_text}"
            )
        else:
            print(
                f"[CYCLE_GATE] token={token_id[:16]}... round={next_round} "
                f"next_buy_after={next_text}"
            )

    def _advance_token_cycle_state_on_cleanup(
        self, token_id: str, run_cfg: Dict[str, Any]
    ) -> None:
        # Backward-compatible shim for older tests/callers.
        self._advance_token_cycle_state_on_round_close(token_id, run_cfg)

    def _confirm_round_close_from_position_truth(
        self,
        token_id: str,
        *,
        force_refresh: bool = True,
    ) -> tuple[bool, str, float]:
        truth, position_size, _row, info = self._get_position_truth_from_snapshot(
            token_id,
            force_refresh=force_refresh,
        )
        if info != "ok":
            return False, f"position_snapshot_unavailable:{info}", position_size
        if not is_position_truth_terminal(truth):
            return False, f"position_truth={truth}", position_size
        return True, truth, position_size

    def _mark_token_cycle_closed_runtime(
        self,
        token_id: str,
        *,
        task: Optional[TopicTask] = None,
    ) -> None:
        if not token_id:
            return
        detail = self.topic_details.setdefault(token_id, {})
        for stale_key in (
            "resume_state",
            "refill_exit_reason",
            "force_sell_only_on_startup",
            "force_sell_only_reason",
            "stoploss_nofill_note",
            "stoploss_reentry_resume",
        ):
            detail.pop(stale_key, None)
        detail["queue_role"] = "cycle_closed"
        self._refill_retry_counts.pop(token_id, None)
        self._refilled_tokens.discard(token_id)
        self._remove_pending_topic(token_id)
        task_ref = task or self.tasks.get(token_id)
        if task_ref is not None:
            task_ref.status = "exited"
            task_ref.no_restart = True
            if not task_ref.end_reason:
                task_ref.end_reason = "cycle closed"

    def _apply_token_cycle_buy_gate_and_drop_override(
        self, token_id: str, run_cfg: Dict[str, Any]
    ) -> bool:
        state = self._token_cycle_states.get(token_id)
        if not state:
            return True
        if not self._config_requires_buy_stage(run_cfg):
            return True
        now = time.time()
        next_buy_allowed_ts = max(0.0, float(state.get("next_buy_allowed_ts", 0.0) or 0.0))
        if next_buy_allowed_ts > now:
            remaining = int(max(0.0, next_buy_allowed_ts - now))
            target_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(next_buy_allowed_ts))
            self._log_throttled(
                f"cycle_gate_wait_{token_id}",
                20.0,
                f"[CYCLE_GATE] token={token_id[:16]}... buy gate not reached, remaining={remaining}s until {target_text}",
            )
            return False

        drop_incremental_enabled = bool(run_cfg.get("enable_incremental_drop_pct", False))
        if not drop_incremental_enabled:
            run_cfg.pop("resume_drop_pct", None)
        next_drop = self._normalize_ratio_value(state.get("next_drop_pct"))
        if drop_incremental_enabled and next_drop is not None:
            drop_base = self._normalize_ratio_value(run_cfg.get("drop_pct")) or 0.0
            effective_drop = max(next_drop, drop_base)
            _, cap = self._resolve_drop_step_and_cap(run_cfg)
            if cap is not None:
                effective_drop = min(effective_drop, max(cap, drop_base))
            old_resume = self._normalize_ratio_value(run_cfg.get("resume_drop_pct"))
            if old_resume is None or abs(old_resume - effective_drop) > 1e-12:
                run_cfg["resume_drop_pct"] = effective_drop
                print(
                    f"[CYCLE_GATE] token={token_id[:16]}... override next drop threshold: "
                    f"{old_resume if old_resume is not None else 'None'} -> {effective_drop:.6f}"
                )

        profit_incremental_enabled = bool(run_cfg.get("enable_incremental_profit_pct", False))
        if not profit_incremental_enabled:
            run_cfg.pop("resume_profit_pct", None)
        next_profit = self._normalize_ratio_value(state.get("next_profit_pct"))
        if profit_incremental_enabled and next_profit is not None:
            profit_base = self._normalize_ratio_value(run_cfg.get("profit_pct")) or 0.0
            effective_profit = max(next_profit, profit_base)
            _, profit_cap = self._resolve_profit_step_and_cap(run_cfg)
            if profit_cap is not None:
                effective_profit = min(effective_profit, max(profit_cap, profit_base))
            old_resume_profit = self._normalize_ratio_value(run_cfg.get("resume_profit_pct"))
            if old_resume_profit is None or abs(old_resume_profit - effective_profit) > 1e-12:
                run_cfg["resume_profit_pct"] = effective_profit
                print(
                    f"[CYCLE_GATE] token={token_id[:16]}... override next profit threshold: "
                    f"{old_resume_profit if old_resume_profit is not None else 'None'} -> {effective_profit:.6f}"
                )
        return True

    def _mark_token_cycle_local_start(self, token_id: str, *, mode: str) -> None:
        token_id = str(token_id or "").strip()
        if not token_id:
            return
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {
            "started_not_bought",
            "position_resume",
            "position_confirmed",
            "sell_pending",
        }:
            normalized_mode = "started_not_bought"
        state = dict(self._token_cycle_states.get(token_id) or {})
        state["local_cycle_status"] = normalized_mode
        state["local_cycle_started_mode"] = normalized_mode
        state["local_cycle_started_ts"] = float(time.time())
        state["local_cycle_invalidated_ts"] = 0.0
        state["local_cycle_invalidate_reason"] = ""
        self._token_cycle_states[token_id] = self._normalize_cycle_state_record(state) or state
        self._save_token_cycle_states()

    def _mark_token_cycle_invalidated(
        self,
        token_id: str,
        *,
        reason: str,
        source: str,
    ) -> None:
        token_id = str(token_id or "").strip()
        if not token_id:
            return
        state = dict(self._token_cycle_states.get(token_id) or {})
        state["local_cycle_status"] = "invalidated"
        state["local_cycle_invalidated_ts"] = float(time.time())
        state["local_cycle_invalidate_reason"] = str(reason or "").strip() or str(source or "").strip()
        self._token_cycle_states[token_id] = self._normalize_cycle_state_record(state) or state
        self._save_token_cycle_states()

    def _token_cycle_allows_sell_signal(self, token_id: str) -> tuple[bool, str]:
        token_id = str(token_id or "").strip()
        if not token_id:
            return False, "missing_token_id"
        state = self._token_cycle_states.get(token_id) or {}
        status = str(state.get("local_cycle_status") or "idle").strip().lower()
        if status in {"position_resume", "position_confirmed", "sell_pending"}:
            return True, status
        if status == "started_not_bought":
            if self._has_account_position(token_id):
                self._mark_token_cycle_local_start(token_id, mode="position_confirmed")
                return True, "position_confirmed"
            return False, status
        return False, status or "idle"

    def _evaluate_sell_signal_local_gate(
        self,
        token_id: str,
        *,
        signal_entry: Optional[Dict[str, Any]] = None,
        allow_started_not_bought_cleanup: bool = False,
    ) -> tuple[bool, str]:
        allowed, reason = self._token_cycle_allows_sell_signal(token_id)
        if not allowed and not (
            allow_started_not_bought_cleanup and reason == "started_not_bought"
        ):
            return False, reason
        state = self._token_cycle_states.get(str(token_id or "").strip()) or {}
        local_cycle_started_ts = max(
            0.0, float(_coerce_float(state.get("local_cycle_started_ts")) or 0.0)
        )
        signal_ts = self._coerce_sell_signal_ts(signal_entry or {})
        if signal_ts is not None and local_cycle_started_ts > 0 and signal_ts + 1e-6 < local_cycle_started_ts:
            return False, "stale_previous_cycle_signal"
        return True, reason

    def _ensure_local_cycle_for_managed_task(self, token_id: str, task: Optional[TopicTask]) -> None:
        token_id = str(token_id or "").strip()
        if not token_id:
            return
        state = self._token_cycle_states.get(token_id) or {}
        status = str(state.get("local_cycle_status") or "").strip().lower()
        if status and status not in {"idle", "invalidated", "cycle_closed"}:
            return
        if task is None and token_id not in self.pending_topics and token_id not in self.pending_burst_topics:
            return
        started_ts = float(task.start_time) if isinstance(task, TopicTask) else 0.0
        state = dict(state)
        state["local_cycle_status"] = "started_not_bought"
        state["local_cycle_started_mode"] = "started_not_bought"
        state["local_cycle_started_ts"] = max(started_ts, 0.0)
        state["local_cycle_invalidated_ts"] = 0.0
        state["local_cycle_invalidate_reason"] = ""
        self._token_cycle_states[token_id] = self._normalize_cycle_state_record(state) or state
        self._save_token_cycle_states()

    def _estimate_stoploss_sellable_price(
        self, token_id: str, position_row: Dict[str, Any]
    ) -> Optional[float]:
        # Prefer live CLOB quote in the current stoploss cycle.
        quote = self._get_stoploss_cycle_quote(token_id)
        bid = _coerce_float((quote or {}).get("bid"))
        if bid is not None and bid > 0:
            return float(bid)
        ws_bid = 0.0
        ws_age = float("inf")
        try:
            with self._ws_cache_lock:
                cached = dict(self._ws_cache.get(token_id) or {})
            ws_bid = float(cached.get("best_bid") or 0.0)
            updated_at = _coerce_float(cached.get("updated_at"))
            if updated_at is not None and updated_at > 0:
                ws_age = max(0.0, time.time() - updated_at)
        except Exception:
            ws_bid = 0.0
            ws_age = float("inf")
        if ws_bid > 0 and ws_age <= 120.0:
            return ws_bid
        # Stoploss should be based on executable bid. If we do not have a fresh bid,
        # skip this round instead of using non-executable curPrice snapshots.
        return None

    def _confirm_stoploss_ioc_fill_via_data_api(
        self,
        token_id: str,
        *,
        before_size: float,
        observed_after_size: float,
    ) -> tuple[float, float, str]:
        token_id = str(token_id or "").strip()
        before_size = max(0.0, float(before_size or 0.0))
        best_after = max(0.0, float(observed_after_size or 0.0))
        if before_size <= 0.0 or not token_id:
            return best_after, max(0.0, before_size - best_after), "skip:no_before_size"

        address = str(self._position_address or "").strip()
        if not address:
            address, _ = _resolve_position_address_from_env()
            self._position_address = address
        if not address:
            return best_after, max(0.0, before_size - best_after), "skip:no_position_address"

        probe_schedule = (1.0, 2.0, 4.0)
        last_info = "unconfirmed"
        for delay_sec in probe_schedule:
            if delay_sec > 0:
                time.sleep(float(delay_sec))
            size, info = _fetch_position_size_from_data_api(address, token_id)
            info_text = str(info or "")
            last_info = info_text or "unconfirmed"
            if size is None:
                if "未找到持仓记录" in info_text:
                    confirmed_after = 0.0
                else:
                    continue
            else:
                confirmed_after = max(0.0, float(size))
            if confirmed_after < best_after:
                best_after = confirmed_after
            if best_after <= 1e-6 or (before_size - best_after) > 1e-6:
                break
        return best_after, max(0.0, before_size - best_after), last_info

    def _prepare_token_for_stoploss_execution(self, token_id: str) -> None:
        self._remove_pending_topic(token_id)
        self._remove_pending_exit_topic(token_id)
        task = self.tasks.get(token_id)
        if task and task.is_running():
            task.no_restart = True
            task.end_reason = "stoploss liquidation"
            self._terminate_task(task, reason="stoploss liquidation")
        if task:
            self.tasks.pop(token_id, None)

    def _queue_stoploss_nofill_sell_only(
        self,
        token_id: str,
        *,
        position_size: float,
        entry_price: Optional[float],
        note: str,
    ) -> bool:
        token_id = str(token_id or "").strip()
        if not token_id:
            return False
        task = self.tasks.get(token_id)
        if task and task.is_running():
            return False
        already_pending = token_id in self.pending_topics or token_id in self.pending_burst_topics
        detail = self.topic_details.setdefault(token_id, {})
        detail["force_sell_only_on_startup"] = True
        detail["force_sell_only_reason"] = "stoploss_nofill_escalated"
        detail["queue_role"] = "stoploss_nofill_sell_only"
        detail["schedule_lane"] = "base"
        detail["resume_state"] = {
            "has_position": True,
            "position_size": float(max(0.0, position_size)),
            "entry_price": float(entry_price) if entry_price is not None and entry_price > 0 else None,
            "skip_buy": True,
        }
        detail["stoploss_nofill_note"] = str(note or "")
        self._enqueue_pending_topic(token_id)
        return not already_pending

    def _mark_stoploss_nofill_manual_intervention(
        self,
        token_id: str,
        state: Dict[str, Any],
        *,
        retry_count: int,
    ) -> None:
        if not token_id:
            return
        detail = self.topic_details.setdefault(token_id, {})
        detail.pop("force_sell_only_on_startup", None)
        detail.pop("resume_state", None)
        detail["queue_role"] = "manual_intervention"
        detail["schedule_lane"] = "base"
        self._remove_pending_topic(token_id)
        task = self.tasks.get(token_id)
        if task is not None and not task.is_running():
            task.status = "error"
            task.no_restart = True
            task.end_reason = "manual intervention required"
        state["state"] = "STOPLOSS_MANUAL_INTERVENTION_REQUIRED"
        state["market_status_last"] = "manual_intervention_required"
        state["last_error"] = (
            f"stoploss no-fill sell-only exhausted after {int(retry_count)} requeues"
        )
        self._record_manual_intervention_token(
            token_id,
            retry_count=int(retry_count),
            rc=0,
            reason="STOPLOSS_NOFILL_SELL_ONLY_MAX_REQUEUES",
        )
        self._append_exit_token_record(
            token_id,
            "STOPLOSS_NOFILL_MANUAL_INTERVENTION_REQUIRED",
            exit_data={
                "source": "stoploss_v4",
                "retry_count": int(retry_count),
                "action": "manual_intervention",
            },
            refillable=False,
        )

    def _finalize_stoploss_full_clear(
        self,
        token_id: str,
        state: Dict[str, Any],
        *,
        drawdown: float,
        now: float,
        before_size: float,
        after_size: float,
        source: str,
    ) -> None:
        state["stoploss_stage"] = "fully_cleared"
        state["stoploss_confirm_hits"] = 0
        state["stoploss_last_action_ts"] = now
        state["stoploss_cooldown_until_ts"] = 0.0
        self._append_exit_token_record(
            token_id,
            "STOPLOSS_FULL_CLEAR",
            exit_data={
                "source": source,
                "drawdown": drawdown,
                "before_size": before_size,
                "after_size": after_size,
            },
            refillable=False,
        )
        self._remove_pending_topic(token_id)
        self._remove_pending_exit_topic(token_id)
        self._remove_from_handled_topics(token_id)
        self._remove_token_from_copytrade_files(token_id)
        self._position_snapshot_cache.pop(token_id, None)

    def _abandon_stoploss_reentry(
        self,
        token_id: str,
        state: Dict[str, Any],
        *,
        event_name: str = "STOPLOSS_REENTRY_ABANDONED",
        reason: str,
        now: float,
        note: str = "",
    ) -> None:
        stop_exit_ts = max(0.0, float(state.get("stop_exit_ts") or 0.0))
        waited_seconds = max(0.0, now - stop_exit_ts) if stop_exit_ts > 0.0 else 0.0
        state_name = str(state.get("state") or "unknown")
        last_error = str(state.get("last_error") or "")
        self._append_exit_token_record(
            token_id,
            str(event_name),
            exit_data={
                "abandon_reason": str(reason),
                "state_name": state_name,
                "stop_exit_ts": stop_exit_ts,
                "waited_seconds": waited_seconds,
                "last_error": last_error,
                "note": str(note or ""),
            },
            refillable=False,
        )
        print(
            f"[STOPLOSS][ABANDON] token={token_id[:20]}... reason={reason} "
            f"state={state_name} waited={waited_seconds:.0f}s note={note or '-'}"
        )
        self._remove_stoploss_reentry_state(token_id)

    def _finalize_stoploss_waiting_reentry(
        self,
        token_id: str,
        state: Dict[str, Any],
        *,
        now: float,
        before_size: float,
        after_size: float,
        exec_price: float,
        exec_source: str,
        drawdown: float,
        line_ticks: int,
        zone_lower_pct: float,
        probe_break_pct: float,
        drawdown_step: float,
        extra_reentry_cd: float,
        window_cd: float,
        max_daily_full_clears: int,
    ) -> None:
        reentry_line, zone_lower, probe_line = self._build_stoploss_reentry_band(
            token_id=token_id,
            exec_price=float(exec_price),
            line_ticks=int(line_ticks),
            zone_lower_pct=float(zone_lower_pct),
            probe_break_pct=float(probe_break_pct),
        )
        cycle_count = int(state.get("stoploss_cycle_count") or 0) + 1
        extra_wait = extra_reentry_cd if cycle_count >= 5 else 0.0

        state["state"] = "STOPLOSS_EXITED_WAITING_WINDOW"
        state["stoploss_cycle_count"] = cycle_count
        state["next_stoploss_threshold_pct"] = max(0.001, float(self.config.stoploss_base_drawdown_pct))
        state["next_stoploss_trigger_ticks"] = self._stoploss_threshold_ticks(
            anchor_price=float(exec_price),
            threshold_pct=float(self.config.stoploss_base_drawdown_pct),
            tick=float(self._estimate_token_tick_size(token_id, None)),
            cycle_count=cycle_count,
        )
        state["stoploss_confirm_hits"] = 0
        state["stop_exit_price"] = float(exec_price)
        state["stop_exit_price_source"] = str(exec_source or "")
        state["stop_exit_ts"] = float(now)
        state["last_stoploss_size"] = float(max(before_size, after_size))
        state["reentry_line_price"] = float(reentry_line)
        state["reentry_zone_lower_price"] = float(zone_lower)
        state["probe_line_price"] = float(probe_line)
        state["probe_seen"] = False
        state["probe_seen_ts"] = 0.0
        state["probe_confirm_hits"] = 0
        state["rebound_confirm_hits"] = 0
        state["reentry_earliest_ts"] = float(now + window_cd + extra_wait)
        state["next_stoploss_earliest_ts"] = float(now)
        state["market_status_last"] = "stoploss_exited"
        state["last_error"] = ""
        state["reentry_quote_missing_hits"] = 0
        state["pending_cleanup_since_ts"] = 0.0
        state["pending_confirm_started_ts"] = 0.0
        state["pending_confirm_next_retry_ts"] = 0.0
        state["pending_confirm_retry_count"] = 0
        state["pending_confirm_before_size"] = 0.0
        state["pending_confirm_after_size_snapshot"] = 0.0
        state["pending_confirm_filled_size"] = 0.0
        state["pending_confirm_exec_price"] = None
        state["pending_confirm_exec_price_source"] = ""
        state["pending_confirm_drawdown"] = 0.0
        realized_loss = max(0.0, -drawdown)
        state["today_realized_loss_pct"] = float(state.get("today_realized_loss_pct") or 0.0) + realized_loss
        state["daily_stoploss_full_clear_count"] = int(
            max(0, int(state.get("daily_stoploss_full_clear_count") or 0)) + 1
        )
        if int(state["daily_stoploss_full_clear_count"]) >= max(1, int(max_daily_full_clears or 1)):
            state["reentry_paused_for_day"] = True
            state["market_status_last"] = "reentry_paused_for_day"
        self._append_exit_token_record(
            token_id,
            "STOPLOSS_FULL_CLEAR",
            exit_data={
                "source": "stoploss_v4",
                "drawdown": drawdown,
                "before_size": before_size,
                "after_size": after_size,
                "phase": "waiting_reentry",
                "lifecycle_terminal": False,
                "daily_stoploss_full_clear_count": int(state.get("daily_stoploss_full_clear_count") or 0),
                "daily_stoploss_max_full_clears": max(1, int(max_daily_full_clears or 1)),
            },
            refillable=False,
        )
        print(
            f"[STOPLOSS] token={token_id[:20]}... drawdown={drawdown:.2%} "
            f"exit={float(exec_price):.6f} line={reentry_line:.6f} probe={probe_line:.6f} "
            f"state=STOPLOSS_EXITED_WAITING_WINDOW"
        )

    def _finalize_stoploss_position_gone_late(
        self,
        token_id: str,
        state: Dict[str, Any],
        *,
        now: float,
        note: str,
    ) -> bool:
        exec_price = max(
            0.0,
            float(
                state.get("pending_stoploss_exit_price")
                or state.get("pending_confirm_exec_price")
                or state.get("stop_exit_price")
                or 0.0
            ),
        )
        if exec_price <= 0.0:
            return False
        before_size = max(
            0.0,
            float(
                state.get("pending_stoploss_before_size")
                or state.get("pending_confirm_before_size")
                or state.get("last_stoploss_size")
                or 0.0
            ),
        )
        drawdown = float(
            state.get("pending_stoploss_drawdown")
            or state.get("pending_confirm_drawdown")
            or 0.0
        )
        self._append_exit_token_record(
            token_id,
            "STOPLOSS_FULL_CLEAR_LATE",
            exit_data={
                "source": "stoploss_v4",
                "note": str(note or ""),
                "before_size": before_size,
                "after_size": 0.0,
                "executed_avg_price": exec_price,
                "lifecycle_terminal": False,
            },
            refillable=False,
        )
        self._finalize_stoploss_waiting_reentry(
            token_id,
            state,
            now=now,
            before_size=before_size,
            after_size=0.0,
            exec_price=exec_price,
            exec_source=str(state.get("pending_stoploss_exec_source") or "late_position_gone"),
            drawdown=drawdown,
            line_ticks=max(1, int(self.config.stoploss_reentry_line_ticks)),
            zone_lower_pct=max(0.0, float(self.config.stoploss_reentry_zone_lower_pct)),
            probe_break_pct=max(0.0, float(self.config.stoploss_probe_break_pct)),
            drawdown_step=max(0.0, float(self.config.stoploss_drawdown_step_per_cycle_pct)),
            extra_reentry_cd=max(
                0.0,
                float(self.config.stoploss_reentry_extra_delay_after_5_cuts_hours),
            )
            * 3600.0,
            window_cd=max(0.0, float(self.config.stoploss_reentry_window_cooldown_minutes))
            * 60.0,
            max_daily_full_clears=max(
                1,
                int(self.config.stoploss_daily_reentry_max_full_clears),
            ),
        )
        return True

    def _finalize_position_reconcile_exit_if_flat(
        self,
        task: TopicTask,
        *,
        rc: int,
    ) -> bool:
        detail = self.topic_details.get(task.topic_id) or {}
        queue_role = str(detail.get("queue_role") or "").strip()
        if queue_role != "startup_reconcile_position":
            return False
        rows, snapshot, info, _source = self._refresh_unified_position_snapshot(force_refresh=True)
        if info != "ok":
            return False
        if not is_position_truth_terminal(
            self._classify_position_truth(task.topic_id, snapshot.get(task.topic_id, 0.0))
        ):
            return False
        self._append_exit_token_record(
            task.topic_id,
            "POSITION_RECONCILE_LATE_CLEANUP_SUCCESS",
            exit_data={
                "source": "startup_reconcile_position",
                "rc": rc,
                "note": (
                    "process exited after position already cleared"
                    if rc == 0
                    else "process exited nonzero after position already cleared"
                ),
            },
            refillable=False,
        )
        task_run_cfg = self._get_task_run_config(task)
        self._advance_token_cycle_state_on_cleanup(task.topic_id, task_run_cfg)
        self._remove_from_handled_topics(task.topic_id)
        self._mark_token_cycle_closed_runtime(task.topic_id, task=task)
        task.end_reason = "position reconcile cleanup success"
        print(
            "[AUTO][LATE_SUCCESS] 历史仓位接管路径卖出后已无持仓，按成功收口: "
            f"token={task.topic_id} rc={rc}"
        )
        return True

    def _requeue_position_reconcile_after_clean_exit(
        self,
        task: TopicTask,
        *,
        rc: int,
    ) -> bool:
        if task.no_restart:
            return False
        if rc != 0:
            return False
        detail = self.topic_details.setdefault(task.topic_id, {})
        queue_role = str(detail.get("queue_role") or "").strip()
        if queue_role != "startup_reconcile_position":
            return False
        _rows, snapshot, info, _source = self._refresh_unified_position_snapshot(
            force_refresh=True
        )
        if info != "ok":
            return False
        position_size = float(snapshot.get(task.topic_id, 0.0) or 0.0)
        if is_position_truth_terminal(
            self._classify_position_truth(task.topic_id, position_size)
        ):
            return False
        gap_backoff = self._get_active_gap_skip_backoff(task.topic_id)
        if gap_backoff:
            hold_until_ts = float(gap_backoff.get("hold_until_ts", 0.0) or 0.0)
            remaining_sec = max(
                0.0,
                float(gap_backoff.get("remaining_seconds", 0.0) or 0.0),
            )
            detail["gap_hold_reason"] = "POSITION_SYNC_SKIP_GAP"
            detail["gap_hold_until_ts"] = hold_until_ts
            detail["gap_hold_streak"] = int(gap_backoff.get("streak", 0.0) or 0.0)
            detail["gap_hold_latest_exit_ts"] = float(
                gap_backoff.get("latest_exit_ts", 0.0) or 0.0
            )
            task.end_reason = "position sync gap hold"
            task.heartbeat(
                "position reconcile exited with remaining position; "
                f"gap hold active for {remaining_sec:.1f}s"
            )
            print(
                "[AUTO][GAP_HOLD] startup_reconcile_position clean exit respects gap backoff: "
                f"token={task.topic_id} size={position_size:.4f} hold={remaining_sec:.1f}s"
            )
            return True
        detail.pop("gap_hold_reason", None)
        detail.pop("gap_hold_until_ts", None)
        detail.pop("gap_hold_streak", None)
        detail.pop("gap_hold_latest_exit_ts", None)
        detail["queue_role"] = "startup_reconcile_position"
        detail["schedule_lane"] = "base"
        if (
            task.topic_id not in self.pending_topics
            and task.topic_id not in self.pending_burst_topics
            and task.topic_id not in self.pending_exit_topics
        ):
            self._enqueue_pending_topic(task.topic_id)
        backoff_sec = max(10.0, float(self.config.process_retry_delay_sec))
        blocked_until = time.time() + backoff_sec
        prev_blocked_until = float(
            self._active_unmanaged_rearm_blocked_until.get(task.topic_id) or 0.0
        )
        self._active_unmanaged_rearm_blocked_until[task.topic_id] = max(
            prev_blocked_until,
            blocked_until,
        )
        self._append_exit_token_record(
            task.topic_id,
            "POSITION_RECONCILE_EXITED_WITH_POSITION",
            exit_data={
                "source": "startup_reconcile_position",
                "rc": rc,
                "position_size": position_size,
                "action": "requeued_startup_reconcile_position",
                "requeue_backoff_sec": backoff_sec,
            },
            refillable=False,
        )
        task.status = "pending"
        task.heartbeat(
            "position reconcile process exited rc=0 with remaining position; requeued"
        )
        print(
            "[AUTO][REQUEUE] startup_reconcile_position clean exit but position remains, requeue: "
            f"token={task.topic_id} size={position_size:.4f} backoff={backoff_sec:.1f}s"
        )
        return True

    def _enter_stoploss_clear_pending_confirm(
        self,
        token_id: str,
        state: Dict[str, Any],
        *,
        now: float,
        before_size: float,
        after_size: float,
        filled_size: float,
        exec_price: float,
        exec_source: str,
        drawdown: float,
    ) -> None:
        timeout_sec = max(30.0, float(self.config.stoploss_clear_confirm_timeout_sec))
        state["state"] = "STOPLOSS_CLEAR_PENDING_CONFIRM"
        state["stoploss_confirm_hits"] = 0
        state["market_status_last"] = "stoploss_clear_pending_confirm"
        state["last_error"] = (
            f"stoploss clear pending confirm remain={after_size:.4f} filled={filled_size:.4f}"
        )
        state["stop_exit_price"] = float(exec_price)
        state["stop_exit_price_source"] = str(exec_source or "")
        state["stop_exit_ts"] = float(now)
        state["last_stoploss_size"] = float(max(before_size, before_size - filled_size))
        state["pending_confirm_started_ts"] = float(now)
        state["pending_confirm_next_retry_ts"] = float(now + timeout_sec)
        state["pending_confirm_retry_count"] = 0
        state["pending_confirm_before_size"] = float(before_size)
        state["pending_confirm_after_size_snapshot"] = float(after_size)
        state["pending_confirm_filled_size"] = float(filled_size)
        state["pending_confirm_exec_price"] = float(exec_price)
        state["pending_confirm_exec_price_source"] = str(exec_source or "")
        state["pending_confirm_drawdown"] = float(drawdown)
        self._append_exit_token_record(
            token_id,
            "STOPLOSS_CLEAR_PENDING_CONFIRM",
            exit_data={
                "source": "stoploss_v4",
                "drawdown": drawdown,
                "before_size": before_size,
                "after_size_snapshot": after_size,
                "filled_size": filled_size,
                "executed_avg_price": exec_price,
                "pending_reason": "position_api_lag_or_partial_fill",
            },
            refillable=False,
        )
        print(
            f"[STOPLOSS][EXECUTED] token={token_id[:20]}... filled={filled_size:.4f} "
            f"exec={float(exec_price):.6f} after_snapshot={after_size:.4f}"
        )
        print(
            f"[STOPLOSS][PENDING_CONFIRM] token={token_id[:20]}... remain_snapshot={after_size:.4f} "
            f"timeout={timeout_sec:.0f}s"
        )

    def _run_stoploss_check(self, now: float) -> None:
        if not bool(getattr(self.config, "enable_stoploss", False)):
            return
        if (
            bool(getattr(self.config, "stoploss_skip_if_global_liquidation_active", True))
            and getattr(self._total_liquidation, "_running", False)
        ):
            return
        if not self._position_address:
            address, origin = _resolve_position_address_from_env()
            self._position_address = address
            self._position_address_origin = origin
            if not address and not self._position_address_warned:
                self._position_address_warned = True
                print(f"[STOPLOSS][WARN] {origin}")
        if not self._position_address:
            return

        rows, _pos_snapshot, info, source = self._refresh_unified_position_snapshot(
            force_refresh=False,
            now=now,
        )
        if info != "ok":
            self._log_throttled(
                "stoploss_positions_fetch_error",
                60.0,
                f"[STOPLOSS][WARN] 持仓查询失败: {info} source={source}",
            )
            return
        self._stoploss_quote_cycle_cache = {}

        copytrade_token_last_seen_ts: Dict[str, float] = {}
        copytrade_token_scope_ok = False
        copytrade_sell_scope_ok = False
        try:
            token_path_exists = bool(self.config.copytrade_tokens_path.exists())
            loaded_copytrade_tokens = self._load_copytrade_tokens()
            copytrade_token_scope_ok = token_path_exists
            copytrade_token_scope = {
                token_id
                for item in loaded_copytrade_tokens
                if (token_id := _topic_id_from_entry(item))
            }
            for item in loaded_copytrade_tokens:
                token_id = _topic_id_from_entry(item)
                if not token_id:
                    continue
                entry = item if isinstance(item, dict) else {}
                token_last_seen_ts = self._coerce_sell_signal_ts(entry)
                if token_last_seen_ts is not None and token_last_seen_ts > 0.0:
                    copytrade_token_last_seen_ts[token_id] = float(token_last_seen_ts)
        except Exception:
            copytrade_token_scope = set()
            copytrade_token_last_seen_ts = {}
            copytrade_token_scope_ok = False
        try:
            sell_path_exists = bool(self.config.copytrade_sell_signals_path.exists())
            copytrade_sell_signal_scope = set(self._load_copytrade_sell_signals().keys())
            copytrade_sell_scope_ok = sell_path_exists
        except Exception:
            copytrade_sell_signal_scope = set()
            copytrade_sell_scope_ok = False
        copytrade_scope_available = bool(copytrade_token_scope_ok or copytrade_sell_scope_ok)
        copytrade_scope = set(copytrade_token_scope)
        copytrade_scope.update(copytrade_sell_signal_scope)
        managed_scope = set(copytrade_scope)
        managed_scope.update(self.handled_topics)
        managed_scope.update(self.tasks.keys())
        managed_scope.update(self.pending_topics)
        managed_scope.update(self.pending_burst_topics)
        managed_scope.update(self.pending_exit_topics)
        managed_scope.update(self._stoploss_reentry_states.keys())
        if not managed_scope and not self._stoploss_reentry_states:
            return

        max_actions = max(1, int(getattr(self.config, "stoploss_max_tokens_per_cycle", 1)))
        confirm_rounds = max(1, int(getattr(self.config, "stoploss_confirm_rounds", 2)))
        today = self._today_utc_date(now)
        window_cd = max(0.0, float(self.config.stoploss_reentry_window_cooldown_minutes)) * 60.0
        next_stoploss_cd = max(0.0, float(self.config.stoploss_next_stoploss_cooldown_minutes)) * 60.0
        extra_reentry_cd = max(0.0, float(self.config.stoploss_reentry_extra_delay_after_5_cuts_hours)) * 3600.0
        reentry_timeout_sec = max(1.0, float(self.config.stoploss_reentry_timeout_hours)) * 3600.0
        line_ticks = max(1, int(self.config.stoploss_reentry_line_ticks))
        zone_lower_pct = max(0.0, float(self.config.stoploss_reentry_zone_lower_pct))
        probe_break_pct = max(0.0, float(self.config.stoploss_probe_break_pct))
        clear_confirm_timeout_sec = max(30.0, float(self.config.stoploss_clear_confirm_timeout_sec))
        clear_confirm_retry_interval_sec = max(
            60.0, float(self.config.stoploss_clear_confirm_retry_interval_sec)
        )
        clear_confirm_max_retries = max(1, int(self.config.stoploss_clear_confirm_max_retries))
        drawdown_step = max(0.0, float(self.config.stoploss_drawdown_step_per_cycle_pct))
        max_daily_full_clears = max(1, int(self.config.stoploss_daily_reentry_max_full_clears))
        waiting_reentry_states = {
            "STOPLOSS_EXITED_WAITING_WINDOW",
            "STOPLOSS_EXITED_WAITING_PROBE",
            "STOPLOSS_EXITED_WAITING_REBOUND",
        }

        positions: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            token_id = _extract_position_token_id(row)
            if not token_id:
                continue
            pos_size = float(_extract_position_size(row) or 0.0)
            if not self._has_actionable_position(token_id, pos_size, row=row):
                continue
            positions[token_id] = row
            managed_scope.add(token_id)

        state_dirty = False
        action_count = 0
        if self._update_stoploss_source_detached_flags(
            copytrade_scope,
            now=now,
            scope_available=copytrade_scope_available,
        ):
            state_dirty = True

        for token_id, row in positions.items():
            if managed_scope and token_id not in managed_scope:
                continue
            state = self._stoploss_reentry_states.get(token_id)
            if not isinstance(state, dict):
                state = self._default_stoploss_reentry_state(token_id)
                self._stoploss_reentry_states[token_id] = state
                state_dirty = True
            if self._rollover_stoploss_daily_fields(state, today):
                state_dirty = True
            state["last_price_check_ts"] = float(now)
            state["last_position_seen_ts"] = float(now)
            state_name = str(state.get("state") or "NORMAL_MAKER")
            if state_name in waiting_reentry_states:
                state["state"] = "NORMAL_MAKER"
                state["probe_confirm_hits"] = 0
                state["rebound_confirm_hits"] = 0
                state["pending_cleanup_since_ts"] = 0.0
                state["last_error"] = "waiting_reentry_state_with_position_auto_normalized"
                state["market_status_last"] = "waiting_reentry_state_with_position_auto_normalized"
                print(
                    f"[STATE_SYNC] token={token_id[:20]}... waiting reentry state with position -> NORMAL_MAKER"
                )
                state_dirty = True
                state_name = "NORMAL_MAKER"
            if state_name in {"STOPLOSS_CLEAR_PENDING_CONFIRM", "STOPLOSS_CLEAR_PENDING_ESCALATED"}:
                continue
            if state_name == "STOPLOSS_IOC_RETRY_PENDING":
                next_retry_ts = float(state.get("nofill_next_retry_ts") or 0.0)
                if now < next_retry_ts:
                    continue
                state["state"] = "NORMAL_MAKER"
                state["market_status_last"] = "stoploss_ioc_retry_due"
                state["last_error"] = ""
                state_dirty = True
                state_name = "NORMAL_MAKER"
            if state_name == "STOPLOSS_IOC_RETRY_ESCALATED":
                requeue_count = int(state.get("stoploss_nofill_sell_only_requeues") or 0)
                task = self.tasks.get(token_id)
                if (
                    requeue_count >= STOPLOSS_NOFILL_SELL_ONLY_MAX_REQUEUES
                    and token_id not in self.pending_topics
                    and token_id not in self.pending_burst_topics
                    and not (task and task.is_running())
                ):
                    self._mark_stoploss_nofill_manual_intervention(
                        token_id,
                        state,
                        retry_count=requeue_count,
                    )
                    state_dirty = True
                    continue
                queued_new = self._queue_stoploss_nofill_sell_only(
                    token_id,
                    position_size=pos_size,
                    entry_price=_extract_position_avg_price(row),
                    note=str(state.get("last_error") or "stoploss no-fill escalated"),
                )
                if queued_new:
                    state["stoploss_nofill_sell_only_requeues"] = requeue_count + 1
                    state_dirty = True
                continue

            if self._stoploss_is_market_closed(token_id):
                print(f"[STATE_SYNC] remove state token={token_id[:20]}... reason=market_closed_with_position")
                self._remove_stoploss_reentry_state(token_id)
                state_dirty = True
                continue

            if bool(state.get("source_detached", False)):
                if not copytrade_scope_available:
                    state["market_status_last"] = "source_detached_scope_unavailable_hold"
                    state["last_error"] = "copytrade scope unavailable; detached transition frozen"
                    state_dirty = True
                    continue
                detached_since = float(state.get("source_detached_since_ts") or now)
                detached_age = max(0.0, now - detached_since)
                state["pending_cleanup_since_ts"] = 0.0
                state["stoploss_confirm_hits"] = 0
                if detached_age < SOURCE_DETACHED_HOLD_SEC:
                    state["market_status_last"] = "source_detached_guard_hold"
                    state["last_error"] = "source_detached guard hold (within grace)"
                    self._log_throttled(
                        f"stoploss_source_detached_hold_{token_id}",
                        180.0,
                        (
                            f"[RISK_GUARD] token={token_id[:20]}... source_detached hold "
                            f"detached_for={detached_age:.0f}s grace={SOURCE_DETACHED_HOLD_SEC:.0f}s"
                        ),
                    )
                else:
                    # Detached one-time cleanup honors global liquidation value threshold:
                    # skip cleanup for tiny positions below configured minimum notional value.
                    threshold = max(
                        0.0,
                        float(
                            getattr(
                                getattr(self._total_liquidation, "cfg", None),
                                "position_value_threshold",
                                0.0,
                            )
                            or 0.0
                        ),
                    )
                    if threshold > 0.0:
                        value_price = self._estimate_stoploss_sellable_price(token_id, row)
                        if value_price is None or value_price <= 0:
                            value_price = _extract_position_current_price(row)
                        est_value = (
                            float(pos_size) * float(value_price)
                            if value_price is not None and value_price > 0
                            else None
                        )
                        if est_value is not None and est_value < threshold:
                            note = (
                                f"source detached low value parked as orphan "
                                f"value={est_value:.4f} threshold={threshold:.4f}"
                            )
                            self._mark_token_orphaned(
                                token_id,
                                reason="SOURCE_DETACHED_LOW_VALUE",
                                trigger_source="stoploss_source_detached",
                                task=self.tasks.get(token_id),
                                note=note,
                                position_snapshot={
                                    "size": float(pos_size),
                                    "avg_price": _extract_position_avg_price(row),
                                    "cur_price": _extract_position_current_price(row),
                                    "est_value": float(est_value),
                                },
                            )
                            state_dirty = True
                            continue
                    self._mark_token_orphaned(
                        token_id,
                        reason="SOURCE_DETACHED_TIMEOUT",
                        trigger_source="stoploss_source_detached",
                        task=self.tasks.get(token_id),
                        note="source detached timeout; token moved to orphan queue",
                        position_snapshot={
                            "size": float(pos_size),
                            "avg_price": _extract_position_avg_price(row),
                            "cur_price": _extract_position_current_price(row),
                        },
                    )
                    print(
                        f"[RISK_GUARD] token={token_id[:20]}... source_detached timeout -> moved to orphan"
                    )
                    state_dirty = True
                    continue
                state_dirty = True
                continue

            pos_size = float(_extract_position_size(row) or 0.0)
            if not self._has_actionable_position(token_id, pos_size, row=row):
                state["stoploss_confirm_hits"] = 0
                state["position_opened_ts"] = 0.0
                state_dirty = True
                continue
            avg_price = _extract_position_avg_price(row)
            if avg_price is None or avg_price <= 0:
                state["stoploss_confirm_hits"] = 0
                state["last_error"] = "missing avg price"
                state_dirty = True
                continue
            position_opened_ts = float(state.get("position_opened_ts") or 0.0)
            if position_opened_ts <= 0.0:
                state["position_opened_ts"] = float(now)
                state["stoploss_confirm_hits"] = 0
                state["last_error"] = "stoploss age gate initialized"
                state_dirty = True
                continue
            min_age_seconds = max(0.0, float(self.config.stoploss_min_age_minutes)) * 60.0
            if min_age_seconds > 0.0:
                position_age = max(0.0, float(now) - position_opened_ts)
                if position_age < min_age_seconds:
                    state["stoploss_confirm_hits"] = 0
                    state["last_error"] = (
                        f"stoploss age gate active age={position_age:.0f}s "
                        f"required={min_age_seconds:.0f}s"
                    )
                    state_dirty = True
                    continue
            if float(state.get("next_stoploss_earliest_ts") or 0.0) > now:
                state["stoploss_confirm_hits"] = 0
                state_dirty = True
                continue

            sellable_price = self._estimate_stoploss_sellable_price(token_id, row)
            if sellable_price is None or sellable_price <= 0:
                state["stoploss_confirm_hits"] = 0
                state["last_error"] = "sellable bid missing or stale"
                print(f"[RISK_GUARD] token={token_id[:20]}... skip stoploss due to missing/stale executable bid")
                state_dirty = True
                continue
            tick = max(1e-8, float(self._estimate_token_tick_size(token_id, row)))
            drawdown = (float(sellable_price) / float(avg_price)) - 1.0
            threshold = max(0.001, float(state.get("next_stoploss_threshold_pct") or self.config.stoploss_base_drawdown_pct))
            threshold_ticks = self._stoploss_threshold_ticks(
                anchor_price=float(avg_price),
                threshold_pct=float(threshold),
                tick=float(tick),
                cycle_count=int(state.get("stoploss_cycle_count") or 0),
            )
            state["next_stoploss_trigger_ticks"] = int(threshold_ticks)
            stoploss_trigger_price = max(0.0, float(avg_price) - float(threshold_ticks) * float(tick))
            if float(sellable_price) > stoploss_trigger_price + 1e-12:
                if int(state.get("stoploss_confirm_hits") or 0) != 0:
                    state["stoploss_confirm_hits"] = 0
                    state_dirty = True
                continue

            hits = int(state.get("stoploss_confirm_hits") or 0) + 1
            state["stoploss_confirm_hits"] = hits
            state_dirty = True
            if hits < confirm_rounds:
                continue
            if action_count >= max_actions:
                break
            action_count += 1

            self._append_stoploss_event_journal(
                token_id,
                "STOPLOSS_TRIGGERED",
                stage="pre_ioc_liquidation",
                payload={
                    "reason": "STOPLOSS_REENTRY",
                    "drawdown": float(drawdown),
                    "avg_price": float(avg_price),
                    "sellable_price": float(sellable_price),
                    "target_size": float(pos_size),
                    "confirm_hits": int(hits),
                    "threshold_pct": float(threshold),
                    "threshold_ticks": int(threshold_ticks),
                    "trigger_price": float(stoploss_trigger_price),
                },
            )
            self._append_exit_token_record(
                token_id,
                "STOPLOSS_IOC_STARTED",
                exit_data={
                    "source": "stoploss_v4",
                    "drawdown": drawdown,
                    "avg_price": float(avg_price),
                    "sellable_price": float(sellable_price),
                    "target_size": float(pos_size),
                    "confirm_hits": hits,
                    "threshold_pct": threshold,
                    "threshold_ticks": int(threshold_ticks),
                    "trigger_price": float(stoploss_trigger_price),
                },
                refillable=False,
            )
            self._prepare_token_for_stoploss_execution(token_id)
            liq = self._total_liquidation.liquidate_single_token_taker(
                self,
                token_id,
                target_size=pos_size,
                reason="STOPLOSS_REENTRY",
            )
            liq_after = _coerce_float(liq.get("after_size"))
            liq_before = _coerce_float(liq.get("before_size"))
            after_size = max(0.0, liq_after if liq_after is not None else pos_size)
            before_size = max(0.0, liq_before if liq_before is not None else pos_size)
            filled_size = max(0.0, _coerce_float(liq.get("filled_size")) or 0.0)
            if bool(liq.get("blocked")):
                state["stoploss_confirm_hits"] = 0
                state["last_error"] = str(liq.get("error") or "stoploss IOC blocked")
                self._append_exit_token_record(
                    token_id,
                    "STOPLOSS_IOC_BLOCKED",
                    exit_data={
                        "source": "stoploss_v4",
                        "reason": str(liq.get("reason") or "UNSPECIFIED"),
                        "error": str(liq.get("error") or ""),
                    },
                    refillable=False,
                )
                state_dirty = True
                continue
            if filled_size <= 0.0 and before_size > after_size:
                filled_size = max(0.0, before_size - after_size)
            fill_confirmed_postcheck = False
            if not self._has_actionable_position(token_id, filled_size, row=row):
                confirmed_after, confirmed_filled, confirm_info = (
                    self._confirm_stoploss_ioc_fill_via_data_api(
                        token_id,
                        before_size=before_size,
                        observed_after_size=after_size,
                    )
                )
                if self._has_actionable_position(token_id, confirmed_filled, row=row):
                    after_size = confirmed_after
                    filled_size = confirmed_filled
                    fill_confirmed_postcheck = True
                    print(
                        f"[STOPLOSS][POSTCHECK] token={token_id[:20]}... "
                        f"confirmed_fill={filled_size:.4f} after={after_size:.4f} info={confirm_info}"
                    )
                else:
                    state["stoploss_confirm_hits"] = 0
                    retry_limit = max(1, int(self.config.stoploss_clear_confirm_max_retries))
                    retry_count = max(0, int(state.get("nofill_retry_count") or 0)) + 1
                    state["nofill_retry_count"] = retry_count
                    state["pending_stoploss_before_size"] = float(before_size)
                    state["pending_stoploss_after_size"] = float(after_size)
                    state["pending_stoploss_exit_price"] = float(
                        min(
                            sellable_price if sellable_price > 0 else stoploss_trigger_price,
                            stoploss_trigger_price if stoploss_trigger_price > 0 else sellable_price,
                        )
                    )
                    state["pending_stoploss_exec_source"] = "stoploss_trigger_price"
                    state["pending_stoploss_drawdown"] = float(drawdown)
                    state["last_error"] = (
                        f"stoploss IOC no_fill remain={after_size:.4f} "
                        f"retry={retry_count}/{retry_limit}"
                    )
                    self._append_exit_token_record(
                        token_id,
                        "STOPLOSS_IOC_NO_FILL",
                        exit_data={
                            "source": "stoploss_v4",
                            "before_size": before_size,
                            "after_size": after_size,
                            "filled_size": filled_size,
                            "error": str(liq.get("error") or ""),
                            "post_check": confirm_info,
                            "retry_count": retry_count,
                            "max_retries": retry_limit,
                        },
                        refillable=False,
                    )
                    if retry_count >= retry_limit:
                        state["state"] = "STOPLOSS_IOC_RETRY_ESCALATED"
                        state["nofill_next_retry_ts"] = 0.0
                        state["market_status_last"] = "stoploss_ioc_retry_escalated"
                        queued_new = self._queue_stoploss_nofill_sell_only(
                            token_id,
                            position_size=before_size,
                            entry_price=float(avg_price),
                            note=state["last_error"],
                        )
                        if queued_new:
                            state["stoploss_nofill_sell_only_requeues"] = (
                                int(state.get("stoploss_nofill_sell_only_requeues") or 0) + 1
                            )
                        self._append_exit_token_record(
                            token_id,
                            "STOPLOSS_IOC_NO_FILL_ESCALATED",
                            exit_data={
                                "source": "stoploss_v4",
                                "remaining_size": after_size,
                                "retry_count": retry_count,
                                "max_retries": retry_limit,
                                "action": "force_sell_only_on_startup",
                            },
                            refillable=False,
                        )
                    else:
                        retry_delay_sec = max(60.0, float(self.config.stoploss_check_interval_sec))
                        state["state"] = "STOPLOSS_IOC_RETRY_PENDING"
                        state["nofill_next_retry_ts"] = float(now + retry_delay_sec)
                        state["market_status_last"] = "stoploss_ioc_retry_pending"
                    state_dirty = True
                    continue

            exec_price = _coerce_float(liq.get("executed_avg_price"))
            if exec_price is None or exec_price <= 0:
                exec_price = _coerce_float(liq.get("executed_price"))
            if exec_price is None or exec_price <= 0:
                if fill_confirmed_postcheck:
                    exec_price = max(0.0, float(sellable_price))
                    exec_source = "postcheck_sellable_price"
                else:
                    exec_price = max(0.0, float(sellable_price) * (1.0 - 0.003))
                    exec_source = "bid_buffer_fallback"
            else:
                exec_source = str(liq.get("executed_price_source") or "response_fill")
            state["old_entry_price"] = float(avg_price)
            state["old_last_buy_price"] = float(avg_price)
            state["old_maker_floor_price"] = _coerce_float((self.topic_details.get(token_id) or {}).get("floor_price"))
            state["old_maker_profit_pct"] = float(self._resolve_profit_pct_for_token(token_id))
            if self._has_actionable_position(token_id, after_size, row=row):
                self._enter_stoploss_clear_pending_confirm(
                    token_id,
                    state,
                    now=now,
                    before_size=before_size,
                    after_size=after_size,
                    filled_size=filled_size,
                    exec_price=float(exec_price),
                    exec_source=exec_source,
                    drawdown=drawdown,
                )
            else:
                self._finalize_stoploss_waiting_reentry(
                    token_id,
                    state,
                    now=now,
                    before_size=before_size,
                    after_size=after_size,
                    exec_price=float(exec_price),
                    exec_source=exec_source,
                    drawdown=drawdown,
                    line_ticks=line_ticks,
                    zone_lower_pct=zone_lower_pct,
                    probe_break_pct=probe_break_pct,
                    drawdown_step=drawdown_step,
                    extra_reentry_cd=extra_reentry_cd,
                    window_cd=window_cd,
                    max_daily_full_clears=max_daily_full_clears,
                )
            state_dirty = True

        for token_id in list(self._stoploss_reentry_states.keys()):
            if token_id in positions:
                continue
            state = self._stoploss_reentry_states.get(token_id)
            if not isinstance(state, dict):
                continue
            if self._rollover_stoploss_daily_fields(state, today):
                state_dirty = True
            state_name = str(state.get("state") or "NORMAL_MAKER")
            if state_name in {"STOPLOSS_IOC_RETRY_PENDING", "STOPLOSS_IOC_RETRY_ESCALATED"}:
                self._append_exit_token_record(
                    token_id,
                    "STOPLOSS_IOC_NO_FILL_POSITION_GONE",
                    exit_data={
                        "state_name": state_name,
                        "reason": "position_missing_after_nofill_state",
                    },
                    refillable=False,
                )
                if not self._finalize_stoploss_position_gone_late(
                    token_id,
                    state,
                    now=now,
                    note="position_missing_after_nofill_state",
                ):
                    self._remove_stoploss_reentry_state(token_id)
                state_dirty = True
                continue
            if state_name == "STOPLOSS_CLEAR_PENDING_CONFIRM":
                exec_price = max(
                    0.0,
                    float(state.get("pending_confirm_exec_price") or state.get("stop_exit_price") or 0.0),
                )
                exec_source = str(
                    state.get("pending_confirm_exec_price_source")
                    or state.get("stop_exit_price_source")
                    or "pending_confirm"
                )
                drawdown = float(state.get("pending_confirm_drawdown") or 0.0)
                before_size = max(0.0, float(state.get("pending_confirm_before_size") or 0.0))
                self._finalize_stoploss_waiting_reentry(
                    token_id,
                    state,
                    now=now,
                    before_size=before_size,
                    after_size=0.0,
                    exec_price=exec_price,
                    exec_source=exec_source,
                    drawdown=drawdown,
                    line_ticks=line_ticks,
                    zone_lower_pct=zone_lower_pct,
                    probe_break_pct=probe_break_pct,
                    drawdown_step=drawdown_step,
                    extra_reentry_cd=extra_reentry_cd,
                    window_cd=window_cd,
                    max_daily_full_clears=max_daily_full_clears,
                )
                state_dirty = True
                continue
            if state_name == "STOPLOSS_CLEAR_PENDING_ESCALATED":
                continue
            if self._stoploss_is_market_closed(token_id):
                if state_name in waiting_reentry_states:
                    self._abandon_stoploss_reentry(
                        token_id,
                        state,
                        reason="market_closed",
                        now=now,
                    )
                else:
                    print(f"[STATE_SYNC] remove state token={token_id[:20]}... reason=market_closed_no_position")
                    self._remove_stoploss_reentry_state(token_id)
                state_dirty = True
                continue

            if state_name in waiting_reentry_states:
                has_sell_signal = token_id in copytrade_sell_signal_scope
                not_in_follow_list = token_id not in copytrade_token_scope
                stop_exit_ts = float(state.get("stop_exit_ts") or 0.0)
                if has_sell_signal or not_in_follow_list:
                    reason = "target_sell_signal_while_waiting_reentry" if has_sell_signal else "target_not_following_while_waiting_reentry"
                    self._abandon_stoploss_reentry(
                        token_id,
                        state,
                        event_name="STOPLOSS_REENTRY_CANCELED",
                        reason=reason,
                        now=now,
                    )
                    state_dirty = True
                    if has_sell_signal:
                        self._remove_token_from_copytrade_files(token_id)
                        self._handled_sell_signals.discard(token_id)
                        self._update_sell_signal_event(
                            token_id,
                            status="processed",
                            note="skip_reentry_after_target_sell",
                        )
                    continue
                # Target BUY must not bypass stoploss reentry gating. Once a token
                # enters stoploss waiting, only explicit cancel paths or the stoploss
                # reentry state machine may bring it back.
                if stop_exit_ts > 0.0 and (now - stop_exit_ts) >= reentry_timeout_sec:
                    self._abandon_stoploss_reentry(
                        token_id,
                        state,
                        reason="reentry_timeout",
                        now=now,
                    )
                    state_dirty = True
                    continue

            if bool(state.get("source_detached", False)):
                first_seen = float(state.get("pending_cleanup_since_ts") or 0.0)
                if first_seen <= 0.0:
                    state["pending_cleanup_since_ts"] = float(now)
                    state_dirty = True
                    continue
                if (now - first_seen) >= max(1.0, float(self.config.stoploss_check_interval_sec)):
                    print(f"[STATE_SYNC] remove state token={token_id[:20]}... reason=detached_no_position_delayed_cleanup")
                    self._remove_stoploss_reentry_state(token_id)
                    state_dirty = True
                continue
            if state_name == "REENTRY_HOLD":
                first_seen = float(state.get("pending_cleanup_since_ts") or 0.0)
                if first_seen <= 0.0:
                    state["pending_cleanup_since_ts"] = float(now)
                    state["last_error"] = "reentry hold no-position pending cleanup"
                    state_dirty = True
                    continue
                if (now - first_seen) >= max(1.0, float(self.config.stoploss_check_interval_sec)):
                    self._append_exit_token_record(
                        token_id,
                        "STOPLOSS_REENTRY_HOLD_CLOSED",
                        exit_data={
                            "reason": "reentry_hold_no_position",
                            "state_name": state_name,
                            "waited_seconds": max(0.0, now - first_seen),
                            "last_error": str(state.get("last_error") or ""),
                        },
                        refillable=False,
                    )
                    print(
                        f"[STATE_SYNC] remove state token={token_id[:20]}... "
                        "reason=reentry_hold_no_position"
                    )
                    self._remove_stoploss_reentry_state(token_id)
                    state_dirty = True
                continue

            state["pending_cleanup_since_ts"] = 0.0

            if bool(state.get("reentry_paused_for_day", False)):
                state["market_status_last"] = "reentry_paused_for_day"
                print(f"[RISK_GUARD] token={token_id[:20]}... reentry paused for day")
                state_dirty = True
                continue

            recent_seen = float(state.get("last_position_seen_ts") or 0.0)
            jitter_grace = max(1.0, float(self.config.stoploss_check_interval_sec)) * 2.0
            if recent_seen > 0.0 and (now - recent_seen) <= jitter_grace:
                state["last_error"] = "position snapshot jitter guard"
                print(
                    f"[RISK_GUARD] token={token_id[:20]}... hold no-position transition "
                    f"(recent_position_seen={now - recent_seen:.1f}s <= grace={jitter_grace:.1f}s)"
                )
                state_dirty = True
                continue

            if state_name == "STOPLOSS_EXITED_WAITING_WINDOW":
                if float(state.get("reentry_earliest_ts") or 0.0) <= now:
                    state["state"] = "STOPLOSS_EXITED_WAITING_PROBE"
                    state["probe_confirm_hits"] = 0
                    state["rebound_confirm_hits"] = 0
                    state["last_error"] = ""
                    print(f"[STATE_SYNC] token={token_id[:20]}... WAITING_WINDOW -> WAITING_PROBE")
                    state_dirty = True
                    state_name = "STOPLOSS_EXITED_WAITING_PROBE"
                else:
                    continue

            if state_name not in {"STOPLOSS_EXITED_WAITING_PROBE", "STOPLOSS_EXITED_WAITING_REBOUND"}:
                continue

            quote = self._estimate_reentry_buyable_price(token_id, None)
            if quote is None or quote <= 0:
                state["reentry_quote_missing_hits"] = int(state.get("reentry_quote_missing_hits") or 0) + 1
                state["probe_confirm_hits"] = 0
                state["rebound_confirm_hits"] = 0
                state["last_error"] = "ask missing or stale"
                print(f"[RISK_GUARD] token={token_id[:20]}... skip reentry due to ask missing/stale")
                state_dirty = True
                continue
            state["last_price_check_ts"] = float(now)
            state["reentry_quote_missing_hits"] = 0
            state["last_error"] = ""
            probe_line = float(state.get("probe_line_price") or 0.0)
            zone_lower = float(state.get("reentry_zone_lower_price") or 0.0)
            zone_upper = float(state.get("reentry_line_price") or 0.0)

            if state_name == "STOPLOSS_EXITED_WAITING_PROBE":
                if probe_line > 0 and quote <= probe_line + 1e-12:
                    hits = int(state.get("probe_confirm_hits") or 0) + 1
                    state["probe_confirm_hits"] = hits
                    print(f"[REENTRY_PROBE] token={token_id[:20]}... hit={hits}/{confirm_rounds} price={quote:.6f} line={probe_line:.6f}")
                    if hits >= confirm_rounds:
                        state["probe_seen"] = True
                        state["probe_seen_ts"] = float(now)
                        state["state"] = "STOPLOSS_EXITED_WAITING_REBOUND"
                        state["probe_confirm_hits"] = 0
                        state["rebound_confirm_hits"] = 0
                        print(f"[STATE_SYNC] token={token_id[:20]}... WAITING_PROBE -> WAITING_REBOUND")
                    state_dirty = True
                else:
                    if int(state.get("probe_confirm_hits") or 0) != 0:
                        state["probe_confirm_hits"] = 0
                        state_dirty = True
                continue

            in_zone = (zone_lower > 0 and zone_upper > 0 and zone_lower - 1e-12 <= quote <= zone_upper + 1e-12)
            if not in_zone:
                if int(state.get("rebound_confirm_hits") or 0) != 0:
                    state["rebound_confirm_hits"] = 0
                    state_dirty = True
                continue
            hits = int(state.get("rebound_confirm_hits") or 0) + 1
            state["rebound_confirm_hits"] = hits
            print(f"[REENTRY_REBOUND] token={token_id[:20]}... hit={hits}/{confirm_rounds} price={quote:.6f} zone=[{zone_lower:.6f},{zone_upper:.6f}]")
            state_dirty = True
            if hits < confirm_rounds:
                continue

            target_size = max(0.0, float(state.get("last_stoploss_size") or 0.0))
            if not self._has_actionable_position(token_id, target_size):
                state["rebound_confirm_hits"] = 0
                state["last_error"] = "invalid reentry target size"
                print(f"[RISK_GUARD] token={token_id[:20]}... invalid reentry target size={target_size:.6f}")
                state_dirty = True
                continue
            reentry_line = float(state.get("reentry_line_price") or 0.0)
            reenter = self._total_liquidation.reenter_single_token_taker(
                self,
                token_id,
                target_size=target_size,
                reference_buy_price=reentry_line if reentry_line > 0 else None,
                reason="stoploss_v4_reentry",
            )
            after_size = max(0.0, float(_coerce_float(reenter.get("after_size")) or 0.0))
            if (not bool(reenter.get("ok"))) or (not self._has_actionable_position(token_id, after_size)):
                state["rebound_confirm_hits"] = 0
                state["last_error"] = str(reenter.get("error") or "reentry failed")
                print(f"[REENTRY_EXEC] token={token_id[:20]}... failed error={state['last_error']}")
                state_dirty = True
                continue

            exec_buy = _coerce_float(reenter.get("executed_avg_price"))
            if exec_buy is None or exec_buy <= 0:
                exec_buy = quote
            recovered_above_line = False
            if reentry_line > 0 and float(exec_buy) > reentry_line + 1e-12:
                recovered_above_line = True
                state["last_error"] = f"reentry price above line buy={float(exec_buy):.6f} line={reentry_line:.6f}"
                print(
                    f"[RISK_GUARD] token={token_id[:20]}... {state['last_error']} -> "
                    "recovering into managed reentry hold"
                )

            hold_floor = _coerce_float(state.get("old_maker_floor_price"))
            hold_profit = _coerce_float(state.get("old_maker_profit_pct"))
            if hold_profit is None or hold_profit < 0:
                hold_profit = self._resolve_profit_pct_for_token(token_id)
            self._queue_reentry_task_after_buy(
                token_id,
                position_size=after_size,
                entry_price=float(exec_buy),
                hold_floor_price=hold_floor if hold_floor is not None and hold_floor > 0 else None,
                hold_profit_pct=hold_profit,
            )
            state["state"] = "REENTRY_HOLD"
            state["market_status_last"] = "reentry_done"
            state["probe_seen"] = False
            state["probe_confirm_hits"] = 0
            state["rebound_confirm_hits"] = 0
            state["next_stoploss_earliest_ts"] = float(now + next_stoploss_cd)
            state["last_reentry_above_line"] = bool(recovered_above_line)
            state["last_reentry_above_line_price"] = (
                float(exec_buy) if recovered_above_line else None
            )
            if not recovered_above_line:
                state["last_error"] = ""
            print(
                f"[REENTRY_EXEC] token={token_id[:20]}... success buy={float(exec_buy):.6f} "
                f"size={after_size:.4f} state=REENTRY_HOLD next_stoploss_in={next_stoploss_cd:.0f}s"
            )
            state_dirty = True

        for token_id, row in positions.items():
            state = self._stoploss_reentry_states.get(token_id)
            if not isinstance(state, dict):
                continue
            if str(state.get("state") or "") != "STOPLOSS_CLEAR_PENDING_CONFIRM":
                continue
            current_size = float(_extract_position_size(row) or 0.0)
            pending_started = float(state.get("pending_confirm_started_ts") or 0.0)
            next_retry_ts = float(state.get("pending_confirm_next_retry_ts") or 0.0)
            retry_count = max(0, int(state.get("pending_confirm_retry_count") or 0))
            if pending_started > 0.0 and (now - pending_started) < clear_confirm_timeout_sec:
                continue
            if now < next_retry_ts:
                continue
            if retry_count < clear_confirm_max_retries:
                retry_count += 1
                state["pending_confirm_retry_count"] = retry_count
                state["pending_confirm_started_ts"] = float(now)
                state["pending_confirm_next_retry_ts"] = float(now + clear_confirm_retry_interval_sec)
                state["pending_confirm_after_size_snapshot"] = float(current_size)
                state["market_status_last"] = "stoploss_clear_confirm_retry_scheduled"
                state["last_error"] = (
                    f"stoploss clear confirm retry scheduled remain={current_size:.4f} "
                    f"retry={retry_count}/{clear_confirm_max_retries}"
                )
                self._append_exit_token_record(
                    token_id,
                    "STOPLOSS_CLEAR_CONFIRM_RETRY_SCHEDULED",
                    exit_data={
                        "retry_count": retry_count,
                        "max_retries": clear_confirm_max_retries,
                        "remaining_size": current_size,
                        "next_retry_ts": float(state["pending_confirm_next_retry_ts"]),
                    },
                    refillable=False,
                )
            else:
                state["pending_confirm_after_size_snapshot"] = float(current_size)
                state["pending_confirm_next_retry_ts"] = 0.0
                state["state"] = "STOPLOSS_CLEAR_PENDING_ESCALATED"
                state["market_status_last"] = "stoploss_clear_confirm_escalated"
                state["last_error"] = (
                    f"stoploss clear confirm escalated remain={current_size:.4f} "
                    f"retry={retry_count}/{clear_confirm_max_retries}"
                )
                self._append_exit_token_record(
                    token_id,
                    "STOPLOSS_CLEAR_CONFIRM_ESCALATED",
                    exit_data={
                        "retry_count": retry_count,
                        "max_retries": clear_confirm_max_retries,
                        "remaining_size": current_size,
                        "note": "manual_intervention_required",
                    },
                    refillable=False,
                )
            state_dirty = True

        if state_dirty:
            self._save_stoploss_reentry_states()
            print("[STATE_SYNC] stoploss/reentry states persisted")
    @staticmethod
    def _parse_title_blacklist_settings(config_payload: Dict[str, Any]) -> tuple[bool, List[str], bool, str]:
        scheduler = (
            config_payload.get("scheduler")
            if isinstance(config_payload, dict)
            else {}
        )
        scheduler = scheduler if isinstance(scheduler, dict) else {}
        title_cfg = scheduler.get("title_blacklist")
        if not isinstance(title_cfg, dict):
            title_cfg = config_payload.get("title_blacklist") if isinstance(config_payload, dict) else {}
        if not isinstance(title_cfg, dict):
            title_cfg = {}

        enabled = bool(
            title_cfg.get(
                "enabled",
                (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get("enabled", False),
            )
        )
        keywords = [
            str(k).strip()
            for k in (title_cfg.get("keywords") or [])
            if str(k).strip()
        ]
        match_on_slug = bool(
            title_cfg.get(
                "match_on_slug",
                (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get("match_on_slug", True),
            )
        )
        action = str(
            title_cfg.get(
                "action_with_position",
                (DEFAULT_GLOBAL_CONFIG.get("title_blacklist") or {}).get(
                    "action_with_position", "sell_only_maker"
                ),
            )
            or "sell_only_maker"
        ).strip().lower()
        return enabled, keywords, match_on_slug, action

    def _hot_reload_runtime_config(self) -> None:
        config_path = self.config.global_config_path
        if not config_path:
            return
        now = time.time()
        if now < self._next_config_reload_check:
            return
        self._next_config_reload_check = now + max(2.0, float(self.config.command_poll_sec))
        try:
            mtime_ns = config_path.stat().st_mtime_ns
        except OSError:
            return
        if self._config_mtime_ns is not None and mtime_ns == self._config_mtime_ns:
            return
        try:
            payload = _load_json_file(config_path)
            enabled, keywords, match_on_slug, action = self._parse_title_blacklist_settings(payload)
        except Exception as exc:
            print(f"[CONFIG][HOT_RELOAD][WARN] reload failed: {exc}")
            return
        self._config_mtime_ns = mtime_ns
        old_state = (
            bool(self.config.title_blacklist_enabled),
            list(self.config.title_blacklist_keywords),
            bool(self.config.title_blacklist_match_on_slug),
            str(self.config.title_blacklist_action_with_position or "").strip().lower(),
        )
        new_state = (enabled, keywords, match_on_slug, action)
        if old_state == new_state:
            return
        self.config.title_blacklist_enabled = enabled
        self.config.title_blacklist_keywords = keywords
        self.config.title_blacklist_match_on_slug = match_on_slug
        self.config.title_blacklist_action_with_position = action
        print(
            "[CONFIG][HOT_RELOAD] title_blacklist updated: "
            f"enabled={enabled} keywords={len(keywords)} "
            f"match_on_slug={match_on_slug} action={action}"
        )

    def _manual_intervention_path(self) -> Path:
        return self.config.copytrade_sell_signals_path.parent / "manual_intervention_tokens.json"

    def _record_manual_intervention_token(
        self,
        token_id: str,
        *,
        retry_count: int,
        rc: int,
        reason: str = "EXIT_CLEANUP_MAX_RETRIES",
    ) -> None:
        if not token_id:
            return
        path = self._manual_intervention_path()
        payload = _load_json_file(path)
        rows = payload.get("tokens") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        out: List[Dict[str, Any]] = []
        matched = False
        for item in rows:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("token_id") or item.get("tokenId") or "").strip()
            if tid == token_id:
                item = dict(item)
                item.update(
                    {
                        "token_id": token_id,
                        "retry_count": int(retry_count),
                        "last_rc": int(rc),
                        "updated_at": now_iso,
                        "reason": str(reason or "EXIT_CLEANUP_MAX_RETRIES"),
                    }
                )
                matched = True
            out.append(item)
        if not matched:
            out.append(
                {
                    "token_id": token_id,
                    "retry_count": int(retry_count),
                    "last_rc": int(rc),
                    "updated_at": now_iso,
                    "reason": str(reason or "EXIT_CLEANUP_MAX_RETRIES"),
                }
            )
        _atomic_json_write(path, {"updated_at": now_iso, "tokens": out})

    def _set_follow_cooldown_token(self, token_id: str, *, cooldown_seconds: float = 24.0 * 3600.0) -> None:
        if not token_id:
            return
        path = self._manual_intervention_path()
        payload = _load_json_file(path)
        rows = payload.get("tokens") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []
        now_ts = time.time()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))
        until_ts = float(now_ts + max(0.0, float(cooldown_seconds)))
        out: List[Dict[str, Any]] = []
        matched = False
        for item in rows:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("token_id") or item.get("tokenId") or "").strip()
            if tid == token_id:
                item = dict(item)
                item["token_id"] = token_id
                item["follow_cooldown_until_ts"] = until_ts
                item["updated_at"] = now_iso
                matched = True
            out.append(item)
        if not matched:
            out.append(
                {
                    "token_id": token_id,
                    "follow_cooldown_until_ts": until_ts,
                    "updated_at": now_iso,
                    "reason": "FOLLOW_SELL_COOLDOWN",
                }
            )
        _atomic_json_write(path, {"updated_at": now_iso, "tokens": out})

    def _load_active_follow_cooldown_map(self, *, now: Optional[float] = None) -> Dict[str, float]:
        path = self._manual_intervention_path()
        if not path.exists():
            return {}
        ts_now = float(now if now is not None else time.time())
        payload = _load_json_file(path)
        rows = payload.get("tokens") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return {}
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_now))
        out: List[Dict[str, Any]] = []
        active: Dict[str, float] = {}
        changed = False
        for item in rows:
            if not isinstance(item, dict):
                continue
            item_copy = dict(item)
            token_id = str(item_copy.get("token_id") or item_copy.get("tokenId") or "").strip()
            if token_id:
                cooldown_until_ts = _coerce_float(item_copy.get("follow_cooldown_until_ts"))
                if cooldown_until_ts is not None and cooldown_until_ts > ts_now:
                    active[token_id] = float(cooldown_until_ts)
                elif "follow_cooldown_until_ts" in item_copy:
                    item_copy.pop("follow_cooldown_until_ts", None)
                    item_copy["updated_at"] = now_iso
                    changed = True
            out.append(item_copy)
        if changed:
            _atomic_json_write(path, {"updated_at": now_iso, "tokens": out})
        return active

    def _clear_manual_intervention_token(self, token_id: str) -> None:
        if not token_id:
            return
        path = self._manual_intervention_path()
        if not path.exists():
            return
        payload = _load_json_file(path)
        rows = payload.get("tokens") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return
        kept: List[Dict[str, Any]] = []
        changed = False
        for item in rows:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("token_id") or item.get("tokenId") or "").strip()
            if tid != token_id:
                kept.append(item)
                continue
            item_copy = dict(item)
            had_retry_fields = any(
                key in item_copy for key in ("retry_count", "last_rc", "reason")
            )
            item_copy.pop("retry_count", None)
            item_copy.pop("last_rc", None)
            if str(item_copy.get("reason") or "").strip().upper() == "EXIT_CLEANUP_MAX_RETRIES":
                item_copy.pop("reason", None)
            cooldown_until_ts = _coerce_float(item_copy.get("follow_cooldown_until_ts"))
            if cooldown_until_ts is not None and cooldown_until_ts > time.time():
                if had_retry_fields:
                    item_copy["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    changed = True
                kept.append(item_copy)
            else:
                changed = True
        if not changed:
            return
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _atomic_json_write(path, {"updated_at": now_iso, "tokens": kept})

    def _update_sell_signal_event(
        self,
        token_id: str,
        *,
        status: str,
        attempts: Optional[int] = None,
        note: Optional[str] = None,
    ) -> None:
        if not token_id:
            return
        path = self.config.copytrade_sell_signals_path
        if not path.exists():
            return
        payload = _load_json_file(path)
        rows = payload.get("sell_tokens") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return
        changed = False
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for item in rows:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("token_id") or item.get("tokenId") or "").strip()
            if tid != token_id:
                continue
            if item.get("status") != status:
                item["status"] = status
                changed = True
            terminal = status in {"done", "stale_ignored", "canceled", "superseded"}
            if terminal and item.get("active", True):
                item["active"] = False
                changed = True
            if terminal and not item.get("consumed_at"):
                item["consumed_at"] = now_iso
                changed = True
            if attempts is not None:
                try:
                    current_attempts = int(item.get("attempts", 0) or 0)
                except (TypeError, ValueError):
                    current_attempts = 0
                if current_attempts != int(attempts):
                    item["attempts"] = int(attempts)
                    changed = True
            if note is not None and item.get("note") != note:
                item["note"] = note
                changed = True
            if changed:
                item["updated_at"] = now_iso
            break
        if not changed:
            return
        payload["updated_at"] = now_iso
        payload["sell_tokens"] = rows
        _atomic_json_write(path, payload)

    def _is_buy_paused_by_balance(self) -> bool:
        min_balance = float(getattr(self.config, "buy_pause_min_free_balance", 0.0) or 0.0)
        if min_balance <= 0:
            if self._buy_paused_due_to_balance:
                print("[BUY_GATE] 低余额买入暂停已关闭（buy_pause_min_free_balance<=0），恢复正常调度")
            self._buy_paused_due_to_balance = False
            return False

        balance = self._total_liquidation._query_free_balance_usdc(
            self,
            ignore_enabled=True,
            poll_interval_sec=float(
                getattr(self.config, "buy_pause_balance_poll_interval_sec", 120.0) or 120.0
            ),
        )
        if balance is None:
            if self._buy_pause_last_balance is None:
                # 首次无法获取余额时不强制暂停，避免误伤；后续沿用上一次状态
                return self._buy_paused_due_to_balance
            balance = self._buy_pause_last_balance

        self._buy_pause_last_balance = float(balance)
        paused = float(balance) < min_balance
        if paused != self._buy_paused_due_to_balance:
            state = "暂停买入" if paused else "恢复买入"
            print(
                "[BUY_GATE] 余额门禁状态切换: "
                f"{state} | free_balance={float(balance):.4f} M={min_balance:.4f}"
            )
            # 状态切换时，通知所有运行中的子进程
            if paused:
                self._notify_all_tasks_low_balance()
            else:
                self._cleanup_all_low_balance_signals()
        self._buy_paused_due_to_balance = paused
        return paused

    def _defer_pending_topics_due_to_low_balance(self) -> None:
        """低余额时：只阻止需要买入的token，有持仓的可卖出token保留在队列中
        
        解决死锁问题：当余额不足时，允许有持仓的token启动卖出，
        以便快速回笼资金，避免持仓卖不掉、余额涨不了的死锁。
        """
        # 处理burst队列（新token）：全部defer，因为这些token还没有持仓
        burst_to_defer = list(self.pending_burst_topics)
        self.pending_burst_topics.clear()
        
        # 处理base队列：只defer无持仓的token，保留有持仓的
        base_to_keep = []
        base_to_defer = []
        for token_id in self.pending_topics:
            detail = self.topic_details.get(token_id) or {}
            # 判断是否有持仓：通过resume_state或refill_exit_reason（SELL_ABANDONED等）
            has_position = False
            resume_state = detail.get("resume_state")
            if resume_state and resume_state.get("has_position"):
                has_position = True
            else:
                # 检查是否是因为有持仓而退出的refill token
                # 这些token虽然没有resume_state（启动后被清除），但有refill_exit_reason标记
                exit_reason = detail.get("refill_exit_reason", "")
                if exit_reason in ("SELL_ABANDONED", "BUY_BLOCK_ENTRY_SYNC_FAILED", 
                                   "BUY_BLOCK_TRIGGER_UNAVAILABLE", "exit_cleanup"):
                    has_position = True
            
            if has_position:
                base_to_keep.append(token_id)
            else:
                base_to_defer.append(token_id)
        
        # 清空pending_topics，稍后只把有持仓的加回来
        self.pending_topics.clear()
        
        # 保留有持仓的token在pending_topics中（允许启动卖出）
        keep_set = set(base_to_keep)
        new_keep = [token_id for token_id in base_to_keep if token_id not in self._buy_pause_keep_snapshot]
        removed_keep = self._buy_pause_keep_snapshot - keep_set

        for token_id in base_to_keep:
            self.pending_topics.append(token_id)
        for token_id in new_keep:
            print(f"[BUY_GATE] keep token with position: {token_id[:20]}...")
        
        # defer所有需要买入的token
        to_defer = burst_to_defer + base_to_defer
        new_defer_count = 0
        for token_id in to_defer:
            self._pending_first_seen.pop(token_id, None)
            self._shared_ws_wait_failures.pop(token_id, None)
            self._shared_ws_paused_until.pop(token_id, None)
            self._shared_ws_wait_timeout_events.pop(token_id, None)
            self._shared_ws_pending_since.pop(token_id, None)
            if token_id in self._buy_pause_deferred_tokens:
                continue
            self._buy_pause_deferred_tokens.add(token_id)
            new_defer_count += 1
            detail = self.topic_details.setdefault(token_id, {})
            detail["queue_role"] = "deferred_token"
            self._append_exit_token_record(
                token_id,
                "LOW_BALANCE_PAUSE",
                exit_data={
                    "has_position": False,
                    "deferred_by_low_balance": True,
                },
                refillable=True,
            )
        
        if new_defer_count > 0:
            print(f"[BUY_GATE] low balance: deferred {new_defer_count} tokens without position")
        if base_to_keep and (new_keep or removed_keep):
            print(f"[BUY_GATE] low balance: keep {len(base_to_keep)} tokens with position")

        self._buy_pause_keep_snapshot = keep_set

    def _log_throttled(self, key: str, interval_sec: float, msg: str) -> None:
        now = time.time()
        last = self._log_throttle_ts.get(key, 0.0)
        if now - last < max(0.0, float(interval_sec)):
            return
        self._log_throttle_ts[key] = now
        print(msg)

    def _notify_all_tasks_low_balance(self) -> None:
        """向所有运行中的子进程发送低余额暂停买入信号"""
        for topic_id, task in self.tasks.items():
            if not task.is_running():
                continue
            self._issue_low_balance_signal(topic_id)
        running_count = sum(1 for t in self.tasks.values() if t.is_running())
        if running_count > 0:
            print(f"[BUY_GATE] 已向 {running_count} 个运行中的子进程发送低余额暂停买入信号")

    def _issue_low_balance_signal(self, token_id: str) -> None:
        """发送低余额暂停买入信号文件"""
        path = self.config.data_dir / f"low_balance_signal_{_safe_topic_filename(token_id)}.json"
        payload = {
            "token_id": token_id,
            "signal": "PAUSE_BUY",
            "min_balance": float(self.config.buy_pause_min_free_balance),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _dump_json_file(path, payload)

    def _cleanup_all_low_balance_signals(self) -> None:
        """清理所有低余额信号文件（余额恢复时调用）"""
        cleaned = 0
        for f in self.config.data_dir.glob("low_balance_signal_*.json"):
            try:
                f.unlink(missing_ok=True)
                cleaned += 1
            except Exception:
                pass
        if cleaned > 0:
            print(f"[BUY_GATE] 余额恢复，已清理 {cleaned} 个低余额暂停买入信号文件")

    def _is_aggressive_mode(self) -> bool:
        mode = (self.config.strategy_mode or "classic").strip().lower()
        return mode == "aggressive"

    @staticmethod
    def _queue_role(detail: Dict[str, Any]) -> str:
        role = str((detail or {}).get("queue_role") or "unknown").strip().lower()
        return role if role else "unknown"

    def _burst_slots(self) -> int:
        """Burst槽位数：经典模式和激进模式都启用，区别只在进程退出行为"""
        # 【重构】使用新的 burst_slots 参数，同时兼容旧的 aggressive_burst_slots。
        if not self.config.enable_burst_queue:
            return 0

        slots = int(getattr(self.config, "burst_slots", DEFAULT_GLOBAL_CONFIG["burst_slots"]))
        legacy_slots = int(
            getattr(
                self.config,
                "aggressive_burst_slots",
                DEFAULT_GLOBAL_CONFIG["aggressive_burst_slots"],
            )
        )

        # 兼容旧配置：当未显式设置 burst_slots（仍为默认值）但设置了 aggressive_burst_slots 时，
        # 应以旧字段为准，避免升级后槽位被错误回退到默认值。
        default_slots = int(DEFAULT_GLOBAL_CONFIG["burst_slots"])
        if slots == default_slots and legacy_slots != default_slots:
            slots = legacy_slots

        return max(0, slots)

    def _max_total_task_slots(self) -> int:
        """总并发硬上限（base + burst）。"""
        base_limit = max(1, int(self.config.max_concurrent_tasks))
        burst_limit = self._burst_slots()
        return max(1, base_limit + burst_limit)

    def _running_burst_count(self) -> int:
        count = 0
        for task in self.tasks.values():
            if not task.is_running():
                continue
            # 【修复】读取 topic_details 使用锁保护
            with self._topic_details_lock:
                lane = (self.topic_details.get(task.topic_id) or {}).get("schedule_lane")
            if lane == "burst":
                count += 1
        return count

    def _demote_oldest_burst_token(self) -> bool:
        """
        【新增】将运行时间最长的 burst token 降级为 base token。
        
        当 burst 槽位满但 base 槽位有空位时调用，释放一个 burst 槽位给新token。
        
        Returns:
            bool: 是否成功降级
        """
        oldest_task = None
        oldest_start_time = float('inf')
        
        # 【修复】先找到最老的 burst token（读取不需要锁）
        for task in self.tasks.values():
            if not task.is_running():
                continue
            # 读取时使用锁保护
            with self._topic_details_lock:
                detail = self.topic_details.get(task.topic_id) or {}
            if detail.get("schedule_lane") == "burst":
                if task.start_time < oldest_start_time:
                    oldest_start_time = task.start_time
                    oldest_task = task
        
        if oldest_task:
            # 【修复】降级操作使用锁保护
            with self._topic_details_lock:
                detail = self.topic_details.setdefault(oldest_task.topic_id, {})
                detail["schedule_lane"] = "base"
            runtime_hours = (time.time() - oldest_task.start_time) / 3600
            print(
                f"[SCHED][DEMOTE] burst token 降级为 base: {oldest_task.topic_id[:20]}... "
                f"已运行 {runtime_hours:.1f} 小时"
            )
            # 【修复】立即同步状态到文件，供子进程读取
            self._dump_runtime_status()
            return True
        return False

    def _normalize_pending_queues_for_mode(self) -> None:
        """模式切换保护：已废弃，经典模式和激进模式都支持burst队列。
        
        经典模式与激进模式的区别只在进程退出行为：
        - 经典模式：进程结束撤卖单（由 Volatility_arbitrage_run.py 中的 keep_sell_orders_on_timeout 控制）
        - 激进模式：进程结束保留卖单
        两者都支持burst队列加速新token和reentry token。
        """
        # 【修复】此函数不再强制将burst队列挪到base队列
        # 保留函数作为兼容性空实现
        pass

    # ========== 核心循环 ==========
    def run_loop(self) -> None:
        self.config.ensure_dirs()
        self._load_handled_topics()
        self._restore_runtime_status()
        self._sync_handled_topics_on_startup()
        self._normalize_pending_queues_for_mode()
        mode = "aggressive" if self._is_aggressive_mode() else "classic"
        print(
            f"[INIT] autorun start | copytrade_poll={self.config.copytrade_poll_sec}s "
            f"strategy_mode={mode} burst_slots={self._burst_slots()}"
        )
        self._start_ws_aggregator()
        try:
            while not self.stop_event.is_set():
                try:
                    now = time.time()
                    self._hot_reload_runtime_config()
                    self._normalize_pending_queues_for_mode()
                    self._process_commands()
                    self._poll_tasks()
                    self._enforce_sell_exit_deadlines()
                    if now >= self._next_exit_signal_reconcile_check:
                        self._reconcile_exit_signals()
                        self._next_exit_signal_reconcile_check = now + max(
                            30.0, float(self.config.copytrade_poll_sec)
                        )
                    self._schedule_pending_exit_cleanup()
                    self._schedule_pending_topics()
                    self._purge_inactive_tasks()
                    if now >= self._next_topics_refresh:
                        self._refresh_topics()
                        # 清理 MARKET_CLOSED 的 token（从 copytrade 文件中移除）
                        self._cleanup_closed_market_tokens()
                        self._next_topics_refresh = now + self.config.copytrade_poll_sec
                    if now >= self._next_inactive_token_reconcile_check:
                        self._reconcile_inactive_copytrade_tokens()
                        self._next_inactive_token_reconcile_check = now + float(
                            self.config.inactive_token_reconcile_interval_sec
                        )
                    if (
                        self.config.orphan_recovery_enabled
                        and now >= self._next_orphan_recovery_probe_check
                    ):
                        self._run_orphan_recovery_probe(now)
                        self._next_orphan_recovery_probe_check = now + float(
                            self.config.orphan_recovery_probe_interval_sec
                        )
                    if self.config.enable_stoploss and now >= self._next_stoploss_check:
                        self._run_stoploss_check(now)
                        self._next_stoploss_check = now + max(
                            10.0, float(self.config.stoploss_check_interval_sec)
                        )
                    # Slot回填检查
                    if self.config.enable_slot_refill and now >= self._next_refill_check:
                        self._schedule_refill()
                        self._next_refill_check = now + self.config.refill_check_interval_sec
                    # 【重构】enable_reentry与mode解耦，classic模式下也可启用
                    if (
                        self.config.enable_reentry
                        and now - self._last_sell_fill_poll_at
                        >= float(self.config.aggressive_sell_fill_poll_sec)
                    ):
                        self._poll_aggressive_self_sell_reentry()
                        self._last_sell_fill_poll_at = now
                    if (
                        self.config.enable_pending_soft_eviction
                        and now >= self._next_pending_eviction
                    ):
                        self._evict_stale_pending_topics()
                        self._next_pending_eviction = now + float(
                            self.config.pending_soft_eviction_check_interval_sec
                        )
                    if now >= self._next_status_dump:
                        self._print_status()
                        self._dump_runtime_status()
                        self._next_status_dump = now + max(
                            5.0, self.config.command_poll_sec
                        )
                    # 日志清理检查（每天清理一次7天前的日志）
                    if now >= self._next_log_cleanup:
                        self._cleanup_old_logs()
                        self._next_log_cleanup = now + self._log_cleanup_interval_sec

                    metrics = self._total_liquidation.update_metrics(self)
                    should_liq, reasons = self._total_liquidation.should_trigger(metrics)
                    if should_liq:
                        self._total_liquidation.execute(self, reasons)
                        continue

                    time.sleep(self.config.command_poll_sec)
                except Exception as exc:  # pragma: no cover - 防御性保护
                    print(f"[ERROR] 主循环异常已捕获，将继续运行: {exc}")
                    traceback.print_exc()
                    _log_error("MAIN_LOOP_ERROR", {
                        "message": "主循环异常",
                        "error": str(exc),
                        "traceback": traceback.format_exc()
                    })
                    time.sleep(max(1.0, self.config.command_poll_sec))
        finally:
            self._stop_ws_aggregator()
            self._cleanup_all_tasks()
            self._dump_runtime_status()
            print("[DONE] autorun stopped")

    def _start_ws_aggregator(self) -> None:
        if self._ws_aggregator_thread and self._ws_aggregator_thread.is_alive():
            return
        self._ws_aggregator_thread = threading.Thread(
            target=self._ws_aggregator_loop,
            daemon=True,
        )
        self._ws_aggregator_thread.start()

    def _stop_ws_aggregator(self) -> None:
        self._stop_ws_subscription()
        if self._ws_aggregator_thread and self._ws_aggregator_thread.is_alive():
            self._ws_aggregator_thread.join(timeout=3)

    def _suspend_ws_updates(self, reason: str = "") -> None:
        self._ws_updates_suspended = True
        if reason:
            print(f"[WS][AGGREGATOR] 暂停订阅更新: {reason}")

    def _resume_ws_updates(self, reason: str = "") -> None:
        self._ws_updates_suspended = False
        if reason:
            print(f"[WS][AGGREGATOR] 恢复订阅更新: {reason}")

    def _desired_ws_token_ids(self) -> List[str]:
        """获取需要订阅的token列表（包括运行中的和待启动的）"""
        token_ids: List[str] = []

        # 主线程会并发增删 self.tasks / self.pending_topics。
        # 使用 copy()+list() 先做快照，避免直接迭代共享容器导致并发迭代异常。
        task_items = list(self.tasks.copy().items())
        pending_snapshot = list(self.pending_topics)
        burst_pending_snapshot = list(self.pending_burst_topics)

        # 1. 运行中的任务
        for topic_id, task in task_items:
            if task.is_running():
                token_ids.append(topic_id)

        # 2. 待启动的pending tokens（提前订阅，避免启动后等待）
        for topic_id in pending_snapshot:
            if topic_id not in token_ids:
                token_ids.append(topic_id)

        for topic_id in burst_pending_snapshot:
            if topic_id not in token_ids:
                token_ids.append(topic_id)

        # 3. 正在启动窗口中的topic（防止 pending->running 过渡期被误取消订阅）
        for topic_id in list(self._ws_starting_topics):
            if topic_id not in token_ids:
                token_ids.append(topic_id)

        now = time.time()
        grace_sec = max(0.0, float(getattr(self.config, "ws_unsubscribe_grace_sec", 0.0) or 0.0))
        active_set = {tid for tid in token_ids if tid}
        if grace_sec <= 0:
            self._ws_recently_active_until = {tid: now for tid in active_set}
            return sorted(active_set)

        # 为当前活跃 token 刷新保活窗口；短期抖动时不立刻退订，减少重订阅和断连风险。
        for tid in active_set:
            self._ws_recently_active_until[tid] = now + grace_sec

        keep_set = set(active_set)
        expired: List[str] = []
        for tid, expire_at in self._ws_recently_active_until.items():
            if tid in active_set:
                continue
            if expire_at > now:
                keep_set.add(tid)
            else:
                expired.append(tid)
        for tid in expired:
            self._ws_recently_active_until.pop(tid, None)

        return sorted(keep_set)

    def _ws_aggregator_loop(self) -> None:
        last_health_check = 0.0
        last_event_count = 0  # 跟踪上次检查时的事件数
        _consecutive_errors = 0

        while not self.stop_event.is_set():
            try:
                desired = self._desired_ws_token_ids()
                if desired != self._ws_token_ids:
                    if self._ws_updates_suspended:
                        # 总清仓等场景下仅暂停“订阅变更”，不应覆盖当前 token 集。
                        # 若此时把 _ws_token_ids 改成空集合，会导致 on_event 过滤掉全部行情。
                        pass
                    else:
                        self._restart_ws_subscription(desired)
                self._flush_ws_cache_if_needed()

                # 定期健康检查（默认30秒，降低低活跃市场的误告警）
                now = time.time()
                health_check_interval = max(10.0, float(self.config.ws_no_event_warn_interval_sec))
                if now - last_health_check >= health_check_interval:
                    current_count = getattr(self, '_ws_event_count', 0)

                    # 检查数据流是否停滞（区分“连接异常”与“仅无事件”）
                    with self._ws_client_lock:
                        client = self._ws_client
                    ws_connected = bool(client and client.is_connected())
                    if current_count == last_event_count and self._ws_token_ids:
                        level = "INFO" if ws_connected else "WARN"
                        reason = "连接正常但全局无新增事件" if ws_connected else "连接异常或数据流停滞"
                        print(
                            f"[{level}] WS 聚合器{int(health_check_interval)}秒内未收到任何新事件"
                            f"（订阅了 {len(self._ws_token_ids)} 个token，{reason}）"
                        )
                    elif current_count > last_event_count:
                        # 数据流正常，每小时打印一次统计（避免刷屏）
                        if not hasattr(self, '_last_flow_log'):
                            self._last_flow_log = 0.0
                        if now - self._last_flow_log >= 3600.0:
                            print(
                                f"[WS][FLOW] 数据流正常，{int(health_check_interval)}秒内收到 "
                                f"{current_count - last_event_count} 个事件"
                            )
                            self._last_flow_log = now

                    last_event_count = current_count
                    self._health_check()
                    last_health_check = now

                _consecutive_errors = 0  # 本轮正常，重置连续错误计数
            except Exception as exc:
                _consecutive_errors += 1
                backoff = min(30.0, 1.0 * (2 ** min(_consecutive_errors, 5)))
                print(
                    f"[ERROR] WS 聚合器线程异常（连续第{_consecutive_errors}次），"
                    f"{backoff:.0f}秒后重试: {exc}"
                )
                traceback.print_exc()
                _log_error("WS_AGGREGATOR_LOOP_ERROR", {
                    "message": "WS聚合器线程异常",
                    "error": str(exc),
                    "consecutive_errors": _consecutive_errors,
                    "traceback": traceback.format_exc(),
                })
                time.sleep(backoff)
                continue

            time.sleep(0.1)  # 从1.0秒改为0.1秒，提高缓存写入频率

    def _update_ws_subscription(self, token_ids: List[str]) -> None:
        """
        使用增量订阅更新WS订阅列表（避免完全重启WS连接）

        优化点：
        - 计算新增/移除的token差异
        - 通过增量消息添加/移除订阅，而不是重启整个WS连接
        - 保持数据流连续性，消除时序竞态问题
        """
        if self._ws_updates_suspended:
            self._ws_token_ids = list(token_ids)
            return

        old_ids = set(self._ws_token_ids)
        new_ids = set(token_ids)
        added = new_ids - old_ids
        removed = old_ids - new_ids

        # 无变化时跳过
        if not added and not removed:
            return

        if added:
            print(f"[WS][AGGREGATOR] ✓ 新增订阅 {len(added)} 个token:")
            for tid in list(added)[:5]:
                print(f"    {tid[:8]}...{tid[-8:]}")
            if len(added) > 5:
                print(f"    ... (还有 {len(added) - 5} 个)")
        if removed:
            print(f"[WS][AGGREGATOR] ✗ 移除订阅 {len(removed)} 个token:")
            for tid in list(removed)[:5]:
                print(f"    {tid[:8]}...{tid[-8:]}")
            if len(removed) > 5:
                print(f"    ... (还有 {len(removed) - 5} 个)")

        # 调试：首次打印完整订阅列表
        if not hasattr(self, '_subscription_list_printed'):
            self._subscription_list_printed = True
            print(f"[WS][AGGREGATOR][DEBUG] 完整订阅列表 ({len(token_ids)} 个):")
            for idx, tid in enumerate(token_ids, 1):
                print(f"    [{idx}] {tid[:8]}...{tid[-8:]}")
            print()

        # 更新本地记录
        self._ws_token_ids = token_ids

        # 使用增量订阅客户端
        with self._ws_client_lock:
            client = self._ws_client
            if client is not None:
                # 先取消订阅移除的token
                if removed:
                    client.unsubscribe(list(removed))
                # 再订阅新增的token
                if added:
                    client.subscribe(list(added))
            else:
                # 客户端未初始化，需要启动
                if token_ids:
                    self._start_ws_subscription(token_ids)
                else:
                    print("[WS][AGGREGATOR] 无token需要订阅")

    # 保留旧方法名作为别名，保持兼容性
    def _restart_ws_subscription(self, token_ids: List[str]) -> None:
        """兼容性别名，内部调用 _update_ws_subscription"""
        self._update_ws_subscription(token_ids)

    def _start_ws_subscription(self, token_ids: List[str]) -> None:
        """
        启动WS订阅（使用增量订阅客户端）

        如果客户端已存在且连接正常，只添加新的订阅；
        否则创建新的客户端实例。
        """
        # 验证 WS 模块导入
        try:
            from Volatility_arbitrage_main_ws import WSAggregatorClient
        except Exception as exc:
            print(f"[ERROR] 无法导入 WS 模块: {exc}")
            print("[ERROR] WS 聚合器启动失败，子进程将使用独立 WS 连接")
            _log_error("WS_AGGREGATOR_IMPORT_ERROR", {
                "message": "无法导入 WS 模块",
                "error": str(exc),
                "tokens_count": len(token_ids)
            })
            return

        # 验证 websocket-client 依赖
        try:
            import websocket
        except ImportError:
            print("[ERROR] 缺少依赖 websocket-client")
            print("[ERROR] 请运行: pip install websocket-client")
            print("[ERROR] WS 聚合器启动失败，子进程将使用独立 WS 连接")
            _log_error("WS_AGGREGATOR_DEPENDENCY_ERROR", {
                "message": "缺少依赖 websocket-client",
                "tokens_count": len(token_ids)
            })
            return

        with self._ws_client_lock:
            # 如果客户端已存在且运行正常，只添加订阅
            if self._ws_client is not None and self._ws_client.is_connected():
                self._ws_client.subscribe(token_ids)
                print(f"[WS][AGGREGATOR] 增量订阅 {len(token_ids)} 个token（连接保持）")
                return

            # 创建新的增量订阅客户端
            print(f"[WS][AGGREGATOR] 初始化增量订阅客户端...")
            self._ws_client = WSAggregatorClient(
                on_event=self._on_ws_event,
                on_state=self._on_ws_state,
                auth=self._load_ws_auth(),
                verbose=self._ws_debug_raw,
                label="autorun-aggregator",
                ping_interval_sec=float(self.config.ws_ping_interval_sec),
                ping_timeout_sec=float(self.config.ws_ping_timeout_sec),
                enable_text_ping=bool(self.config.ws_enable_text_ping),
                silence_timeout_sec=self.config.ws_silence_timeout_sec,
                custom_feature_enabled=self.config.ws_custom_feature_enabled,
                subscribe_chunk_size=self.config.ws_subscribe_chunk_size,
                subscribe_chunk_interval_sec=self.config.ws_subscribe_chunk_interval_ms / 1000.0,
            )
            self._ws_client.start()

            # 订阅初始token列表
            if token_ids:
                self._ws_client.subscribe(token_ids)

        print(f"[WS][AGGREGATOR] 聚合订阅启动，tokens={len(token_ids)}")
        print(f"[WS][AGGREGATOR] 缓存文件: {self._ws_cache_path}")
        print(f"[WS][AGGREGATOR] ✓ 使用增量订阅模式（避免WS重启导致的数据中断）")

        # 验证客户端是否成功启动
        time.sleep(2)
        with self._ws_client_lock:
            client = self._ws_client
        if client is None or not client.is_connected():
            print("[WS][AGGREGATOR] ⚠ WS连接尚未建立，可能正在重试...")
            # 不立即报错，让客户端自动重连
        else:
            stats = client.get_stats()
            print(f"[WS][AGGREGATOR] ✓ WS连接正常 (已订阅: {stats.get('subscribed_tokens', 0)} 个token)")

    def _load_ws_auth(self) -> Optional[Dict[str, str]]:
        api_key = os.getenv("POLY_API_KEY")
        api_secret = os.getenv("POLY_API_SECRET")
        api_passphrase = os.getenv("POLY_API_PASSPHRASE") or os.getenv("POLY_API_PASS_PHRASE")
        if api_key and api_secret and api_passphrase:
            return {
                "apiKey": api_key,
                "secret": api_secret,
                "passphrase": api_passphrase,
            }
        return None

    def _on_ws_state(self, state: str, info: Dict[str, Any]) -> None:
        if state in self._ws_reconnect_reason_counts:
            self._ws_reconnect_reason_counts[state] = (
                int(self._ws_reconnect_reason_counts.get(state, 0)) + 1
            )
            self._ws_last_reconnect_reason = state
            detail: Dict[str, Any] = {
                "state": state,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if isinstance(info, dict):
                for key in (
                    "connect_count",
                    "connected_for_sec",
                    "status_code",
                    "message",
                    "error",
                    "subscribed_tokens",
                    "desired_tokens",
                    "pending_subscribe",
                    "pending_unsubscribe",
                    "timeout",
                ):
                    value = info.get(key)
                    if value not in (None, ""):
                        detail[key] = value
            self._ws_last_reconnect_detail = detail
            if state == "closed":
                print(
                    "[WS][AGGREGATOR] reconnect trigger=closed "
                    f"code={detail.get('status_code', '-')} "
                    f"uptime={detail.get('connected_for_sec', '-')}s "
                    f"subs={detail.get('subscribed_tokens', '-')}"
                )
            elif state == "error":
                print(
                    "[WS][AGGREGATOR] reconnect trigger=error "
                    f"uptime={detail.get('connected_for_sec', '-')}s "
                    f"error={str(detail.get('error') or '-')[:160]}"
                )
            elif state == "silence":
                print(
                    "[WS][AGGREGATOR] reconnect trigger=silence "
                    f"timeout={detail.get('timeout', '-')}s "
                    f"subs={detail.get('subscribed_tokens', '-')}"
                )
            return
        if state != "open":
            return
        connect_count = int(info.get("connect_count", 0) or 0)
        if connect_count <= 1:
            return
        self._refresh_ws_cache_after_reconnect(connect_count)

    def _is_reentry_eligible_exit(
        self,
        exit_reason: str,
        exit_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        reason = str(exit_reason or "").strip().upper()
        data = exit_data or {}
        if reason == "SELL_ABANDONED":
            return True
        if reason == "REFILL_SKIP_GAP":
            return True
        if reason == "POSITION_SYNC_SKIP_GAP":
            return True
        if reason == "LOW_BALANCE_PAUSE":
            return bool(data.get("has_position"))
        return False

    def _mark_reentry_eligible_token(self, token_id: str, *, source: str) -> None:
        token_id = str(token_id or "").strip()
        if not token_id:
            return
        self._reentry_eligible_tokens.add(token_id)
        src = str(source or "").strip().upper() or "UNKNOWN"
        self._reentry_eligible_reasons[token_id] = src
        print(
            "[AGGRESSIVE][REENTRY] 标记可回补 token="
            f"{token_id} source={src}"
        )

    def _clear_reentry_eligible_token(self, token_id: str, *, reason: str) -> None:
        token_id = str(token_id or "").strip()
        if not token_id:
            return
        had_token = token_id in self._reentry_eligible_tokens
        src = self._reentry_eligible_reasons.pop(token_id, "unknown")
        self._reentry_eligible_tokens.discard(token_id)
        if had_token or src != "unknown":
            clear_reason = str(reason or "").strip() or "unspecified"
            print(
                "[AGGRESSIVE][REENTRY] 清理回补标记 token="
                f"{token_id} source={src} reason={clear_reason}"
            )

    def _cleanup_active_unmanaged_rearm_blocks(self, now: Optional[float] = None) -> None:
        current = time.time() if now is None else float(now)
        expired = [
            token_id
            for token_id, until_ts in self._active_unmanaged_rearm_blocked_until.items()
            if float(until_ts or 0.0) <= current
        ]
        for token_id in expired:
            self._active_unmanaged_rearm_blocked_until.pop(token_id, None)
            self._active_unmanaged_rearm_block_reasons.pop(token_id, None)

    def _set_active_unmanaged_rearm_block(
        self,
        token_id: str,
        *,
        reason: str,
        now: Optional[float] = None,
    ) -> None:
        token_id = str(token_id or "").strip()
        if not token_id:
            return
        current = time.time() if now is None else float(now)
        guard_sec = max(
            900.0,
            float(self.config.refill_cooldown_minutes_with_position) * 60.0,
        )
        self._active_unmanaged_rearm_blocked_until[token_id] = current + guard_sec
        self._active_unmanaged_rearm_block_reasons[token_id] = (
            str(reason or "").strip().upper() or "UNKNOWN"
        )

    def _clear_active_unmanaged_rearm_block(self, token_id: str) -> None:
        token_id = str(token_id or "").strip()
        if not token_id:
            return
        self._active_unmanaged_rearm_blocked_until.pop(token_id, None)
        self._active_unmanaged_rearm_block_reasons.pop(token_id, None)

    def _refresh_ws_cache_after_reconnect(self, connect_count: int) -> None:
        now = time.time()
        refreshed = 0
        with self._ws_cache_lock:
            for token_id in self._ws_token_ids:
                entry = self._ws_cache.get(token_id)
                if not entry:
                    continue
                entry["updated_at"] = now
                refreshed += 1
            if refreshed:
                self._ws_cache_dirty = True
        if refreshed:
            self._flush_ws_cache_if_needed()
            print(
                f"[WS][AGGREGATOR] 重连后缓存回填 {refreshed} 个token "
                f"(connect={connect_count})"
            )

    def _stop_ws_subscription(self) -> None:
        """停止WS订阅"""
        # 停止增量订阅客户端
        with self._ws_client_lock:
            client = self._ws_client
            self._ws_client = None
        if client is not None:
            client.stop()

        # 兼容旧的线程方式（如果有）
        if self._ws_thread_stop is not None:
            self._ws_thread_stop.set()
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=3)
        self._ws_thread = None
        self._ws_thread_stop = None

    def _update_token_timestamp_from_trade(self, ev: Dict[str, Any]) -> None:
        """
        处理 last_trade_price 事件，仅更新时间戳以避免假僵尸token。
        这类事件表明市场有交易活动，即使价格未显著变化。
        """
        # 获取asset_id（可能在不同字段）
        asset_id = ev.get("asset_id") or ev.get("token_id") or ev.get("tokenId")
        if not asset_id:
            return

        token_id = str(asset_id)
        if token_id not in self._ws_token_ids:
            return

        # 仅更新缓存中的时间戳，保留其他字段
        with self._ws_cache_lock:
            now = time.time()
            cache = self._ws_cache.get(token_id)
            if cache is None:
                cache = {
                    "seq": 1,
                    "updated_at": now,
                    "source": "last_trade_price_bootstrap",
                }
                self._ws_cache[token_id] = cache
            else:
                cache["updated_at"] = now
                cache["seq"] = int(cache.get("seq", 0)) + 1
            self._ws_cache_dirty = True

            # 可选：更新last_trade_price字段
            trade_price = _coerce_float(ev.get("price") or ev.get("last_trade_price"))
            if trade_price is not None and trade_price > 0:
                cache["price"] = trade_price

    def _on_ws_event(self, ev: Dict[str, Any]) -> None:
        # 统计事件接收和过滤情况
        if not hasattr(self, '_ws_event_count'):
            self._ws_event_count = 0
            self._ws_filtered_count = 0
            self._ws_filtered_types: Dict[str, int] = {}
            self._ws_last_stats_log = 0.0

        self._ws_event_count += 1

        if self._ws_debug_raw:
            try:
                print(f"[WS][RAW] {json.dumps(ev, ensure_ascii=False)}")
            except Exception:
                print(f"[WS][RAW] {ev}")

        if not isinstance(ev, dict):
            self._ws_filtered_count += 1
            return

        event_type = ev.get("event_type")

        # 处理 price_change 事件（完整更新）
        if event_type == "price_change":
            pcs = ev.get("price_changes", [])
        elif "price_changes" in ev:
            pcs = ev.get("price_changes", [])
        # 处理 book 事件（兼容官方 buys/sells 与示例 bids/asks）
        elif event_type == "book":
            asset_id = ev.get("asset_id") or ev.get("token_id")
            bid, ask = _extract_best_bid_ask_from_book_event(ev)
            if asset_id and (bid is not None or ask is not None):
                mid = (bid + ask) / 2.0 if bid and ask else (bid or ask)
                pcs = [{
                    "asset_id": asset_id,
                    "best_bid": bid,
                    "best_ask": ask,
                    "last_trade_price": _coerce_float(ev.get("last_trade_price")) or mid,
                }]
            else:
                pcs = []
        # best_bid_ask / tick 是高价值行情，直接纳入处理
        elif event_type in ("best_bid_ask", "tick"):
            asset_id = ev.get("asset_id") or ev.get("token_id")
            bid = _coerce_float(ev.get("best_bid") or ev.get("bid"))
            ask = _coerce_float(ev.get("best_ask") or ev.get("ask"))
            if asset_id and (bid is not None or ask is not None):
                mid = (bid + ask) / 2.0 if bid and ask else (bid or ask)
                pcs = [{
                    "asset_id": asset_id,
                    "best_bid": bid,
                    "best_ask": ask,
                    "last_trade_price": _coerce_float(ev.get("last_trade_price")) or mid,
                }]
            else:
                pcs = []
        # tick_size_change 不一定携带盘口，尽量用已有字段刷新时间戳与状态
        elif event_type == "tick_size_change":
            asset_id = ev.get("asset_id") or ev.get("token_id")
            tick_size = self._extract_tick_size_value(ev)
            if asset_id and tick_size is not None and tick_size > 0:
                self._cache_token_tick_size(str(asset_id), tick_size)
            bid = _coerce_float(ev.get("best_bid") or ev.get("bid"))
            ask = _coerce_float(ev.get("best_ask") or ev.get("ask"))
            if asset_id and (bid is not None or ask is not None):
                mid = (bid + ask) / 2.0 if bid and ask else (bid or ask)
                pcs = [{
                    "asset_id": asset_id,
                    "best_bid": bid,
                    "best_ask": ask,
                    "last_trade_price": _coerce_float(ev.get("last_trade_price")) or mid,
                    "tick_size": tick_size,
                }]
            else:
                return
        # 处理 last_trade_price 事件（仅更新时间戳，避免假僵尸）
        elif event_type == "last_trade_price":
            self._update_token_timestamp_from_trade(ev)
            return
        else:
            # 记录被过滤的事件类型
            self._ws_filtered_count += 1
            evt_type = event_type or "unknown"
            self._ws_filtered_types[evt_type] = self._ws_filtered_types.get(evt_type, 0) + 1

            # 每60秒打印一次统计
            now = time.time()
            if now - self._ws_last_stats_log >= 60.0:
                print(
                    f"[WS][STATS] 总事件: {self._ws_event_count}, "
                    f"已处理: {self._ws_event_count - self._ws_filtered_count}, "
                    f"已过滤: {self._ws_filtered_count}"
                )
                if self._ws_filtered_types:
                    print(f"[WS][STATS] 过滤事件类型: {self._ws_filtered_types}")
                self._ws_last_stats_log = now
            return
        ts = _normalize_ws_event_timestamp(
            ev.get("timestamp") or ev.get("ts") or ev.get("time")
        )

        status_keys = (
            "status",
            "market_status",
            "marketStatus",
            "is_closed",
            "market_closed",
            "closed",
            "isMarketClosed",
        )
        event_status = {k: ev.get(k) for k in status_keys if k in ev}
        for pc in pcs:
            token_id = str(pc.get("asset_id") or "")
            if not token_id:
                continue

            # ✅ P0修复：只缓存订阅列表中的token
            # Polymarket的market订阅会返回整个市场（YES+NO），需要过滤
            if token_id not in self._ws_token_ids:
                # 静默跳过未订阅的token（可能是配对token）
                if not hasattr(self, '_ws_unsubscribed_tokens'):
                    self._ws_unsubscribed_tokens = set()
                    self._ws_unsubscribed_log_ts = 0.0
                    self._ws_filter_detail_logged = False

                # 只在第一次遇到时记录
                if token_id not in self._ws_unsubscribed_tokens:
                    self._ws_unsubscribed_tokens.add(token_id)

                    # 第一次过滤时，打印详细信息（调试用）
                    if not self._ws_filter_detail_logged:
                        print(f"[WS][FILTER][DEBUG] 发现未订阅的token: {token_id[:8]}...{token_id[-8:]}")
                        print(f"[WS][FILTER][DEBUG] 当前订阅列表 ({len(self._ws_token_ids)} 个):")
                        for sub_tid in list(self._ws_token_ids)[:5]:
                            print(f"    {sub_tid[:8]}...{sub_tid[-8:]}")
                        if len(self._ws_token_ids) > 5:
                            print(f"    ... (还有 {len(self._ws_token_ids) - 5} 个)")
                        self._ws_filter_detail_logged = True

                    # 每5分钟打印一次汇总（避免刷屏）
                    now = time.time()
                    if now - self._ws_unsubscribed_log_ts >= 300:
                        print(f"[WS][FILTER] 过滤未订阅的token（可能是配对token）: {len(self._ws_unsubscribed_tokens)} 个")
                        if len(self._ws_unsubscribed_tokens) <= 5:
                            for utid in self._ws_unsubscribed_tokens:
                                print(f"  - {utid[:8]}...{utid[-8:]}")
                        self._ws_unsubscribed_log_ts = now
                continue

            bid = _coerce_float(pc.get("best_bid")) or 0.0
            ask = _coerce_float(pc.get("best_ask")) or 0.0

            # ✅ 只使用last_trade_price作为真实成交价，不使用price字段（price含义不明确）
            # price字段可能是历史价格或订单簿深度价格，不适合用作当前价格
            last = _coerce_float(pc.get("last_trade_price"))

            # 获取当前序列号并递增（用于去重）
            with self._ws_cache_lock:
                old_data = self._ws_cache.get(token_id, {})
                seq = old_data.get("seq", 0) + 1

            suspicious_reason = _detect_suspicious_quote(
                bid,
                ask,
                last_trade_price=last,
            )

            if suspicious_reason:
                prev_bid = _coerce_float(old_data.get("best_bid")) or 0.0
                prev_ask = _coerce_float(old_data.get("best_ask")) or 0.0
                prev_price = _coerce_float(old_data.get("price")) or 0.0
                if prev_bid > 0 and prev_ask > 0:
                    bid, ask = prev_bid, prev_ask
                    # 回退盘口时，优先沿用上一笔缓存价格，避免异常事件携带的 price 污染缓存。
                    if prev_price > 0:
                        last = prev_price
                    if not hasattr(self, "_ws_quote_sanity_stats"):
                        self._ws_quote_sanity_stats = {
                            "fallback_used": 0,
                            "skip_new_token": 0,
                            "last_log_ts": 0.0,
                        }
                    self._ws_quote_sanity_stats["fallback_used"] += 1
                else:
                    # 新 token 仅收到异常盘口时，宁可跳过该条，避免污染 shock 风控输入。
                    if not hasattr(self, "_ws_suspicious_quote_last_log"):
                        self._ws_suspicious_quote_last_log = {}
                    if not hasattr(self, "_ws_quote_sanity_stats"):
                        self._ws_quote_sanity_stats = {
                            "fallback_used": 0,
                            "skip_new_token": 0,
                            "last_log_ts": 0.0,
                        }
                    self._ws_quote_sanity_stats["skip_new_token"] += 1
                    now_ts = time.time()
                    last_log_ts = self._ws_suspicious_quote_last_log.get(token_id, 0.0)
                    if now_ts - last_log_ts >= 60.0:
                        print(
                            "[WS][QUOTE_SANITY] 跳过异常盘口 "
                            f"token={token_id[:8]}... reason={suspicious_reason} "
                            f"bid={bid} ask={ask} last={last}"
                        )
                        self._ws_suspicious_quote_last_log[token_id] = now_ts
                    continue

                # 定期打印净化统计，便于评估脏盘口污染程度。
                stats = getattr(self, "_ws_quote_sanity_stats", None)
                if stats is not None:
                    now_ts = time.time()
                    if now_ts - float(stats.get("last_log_ts", 0.0)) >= 300.0:
                        print(
                            "[WS][QUOTE_SANITY] 统计: "
                            f"fallback_used={int(stats.get('fallback_used', 0))} "
                            f"skip_new_token={int(stats.get('skip_new_token', 0))}"
                        )
                        stats["last_log_ts"] = now_ts

            # ✅ 计算mid价格（最可靠的当前市场价格）
            mid = (bid + ask) / 2.0 if bid and ask else (bid or ask or 0.0)

            # 如果没有last_trade_price，直接使用mid
            if last is None or last == 0.0:
                last = mid

            payload = {
                "price": last,
                "best_bid": bid,
                "best_ask": ask,
                "ts": ts,
                "updated_at": time.time(),
                "event_type": ev.get("event_type"),
                "seq": seq,  # 单调递增的序列号
            }
            for key in status_keys:
                val = pc.get(key)
                if val is None:
                    val = event_status.get(key)
                if val is not None:
                    payload[key] = val
            tick_size = self._extract_tick_size_value(pc)
            if tick_size is not None and tick_size > 0:
                payload["tick_size"] = float(tick_size)
            with self._ws_cache_lock:
                self._ws_cache[token_id] = payload
                self._ws_cache_dirty = True

                # 定期打印缓存更新统计（每分钟）
                if not hasattr(self, '_cache_update_log_ts'):
                    self._cache_update_log_ts = 0
                    self._cache_update_count = 0
                self._cache_update_count += 1
                now = time.time()
                if now - self._cache_update_log_ts >= 60:
                    print(f"[WS][AGGREGATOR] 缓存更新统计: {self._cache_update_count} 次/分钟, "
                          f"tokens={len(self._ws_cache)}, last_seq={seq}")
                    self._cache_update_log_ts = now
                    self._cache_update_count = 0

    def _flush_ws_cache_if_needed(self) -> None:
        now = time.time()
        heartbeat_interval = 30.0
        min_flush_interval = max(0.1, float(self.config.ws_cache_flush_min_interval_sec))
        # 脏缓存刷盘限频：避免高事件频率下每0.1秒刷盘导致 CPU/IO 峰值
        if self._ws_cache_dirty and now - self._ws_cache_last_flush < min_flush_interval:
            return
        if not self._ws_cache_dirty and now - self._ws_cache_last_flush < heartbeat_interval:
            return
        with self._ws_cache_lock:
            if self._ws_cache_dirty and now - self._ws_cache_last_flush < min_flush_interval:
                return
            if not self._ws_cache_dirty and now - self._ws_cache_last_flush < heartbeat_interval:
                return
            if (
                not self._ws_cache_dirty
                and self._ws_cache
                and now - self._ws_cache_last_flush >= heartbeat_interval
            ):
                self._ws_cache_dirty = True
            # ✅ 使用深拷贝避免多线程并发修改导致 "dictionary changed size during iteration" 错误
            data = {
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "tokens": copy.deepcopy(self._ws_cache),
            }
            self._ws_cache_dirty = False
            self._ws_cache_last_flush = now
        try:
            self._ws_cache_path.parent.mkdir(parents=True, exist_ok=True)

            lock_path = self._ws_cache_path.with_suffix('.lock')
            with lock_path.open("w", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                # 使用原子写入：先写临时文件，再重命名
                tmp_path = self._ws_cache_path.with_suffix('.tmp')
                with tmp_path.open("w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                # 原子操作：重命名（在 Unix 系统上是原子的）
                tmp_path.replace(self._ws_cache_path)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

        except OSError as exc:
            print(f"[ERROR] 写入 WS 聚合缓存失败: {exc}")
            _log_error("WS_CACHE_WRITE_ERROR", {
                "message": "写入 WS 聚合缓存失败",
                "error": str(exc),
                "cache_path": str(self._ws_cache_path),
                "tokens_count": len(self._ws_cache)
            })
            # 清理临时文件
            try:
                tmp_path = self._ws_cache_path.with_suffix('.tmp')
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    def _health_check(self) -> None:
        """
        WS 聚合器健康检查（增强版）
        - 分级阈值：正常(10min) / 警告(30min) / 清理(60min)
        - 市场状态检测：自动清理已关闭市场
        - 日志优化：减少刷屏频率
        """
        # 周期性打印缓存状态（每5分钟）
        if not hasattr(self, '_last_cache_status_log'):
            self._last_cache_status_log = 0.0

        now = time.time()
        if now - self._last_cache_status_log >= 300:  # 5分钟
            with self._ws_cache_lock:
                if self._ws_cache:
                    print(f"\n[WS][CACHE_STATUS] 缓存中的token状态 ({len(self._ws_cache)} 个):")
                    for idx, (tid, data) in enumerate(list(self._ws_cache.items())[:10], 1):
                        seq = data.get("seq", 0)
                        bid = data.get("best_bid", 0)
                        ask = data.get("best_ask", 0)
                        updated = data.get("updated_at", 0)
                        age = now - updated if updated > 0 else 0
                        tid_short = f"{tid[:8]}...{tid[-8:]}"
                        print(f"  [{idx}] {tid_short}: seq={seq}, bid={bid}, ask={ask}, age={age:.0f}s")
                    if len(self._ws_cache) > 10:
                        print(f"  ... (还有 {len(self._ws_cache) - 10} 个token)")
                    print()
            self._last_cache_status_log = now

        # 检查 WS 客户端是否运行（优先检查增量订阅客户端）
        if self._ws_token_ids:
            ws_healthy = False
            with self._ws_client_lock:
                client = self._ws_client
            if client is not None:
                ws_healthy = client.is_connected()
                # 打印客户端统计信息（每5分钟）
                if now - self._last_cache_status_log < 1:  # 刚打印完缓存状态
                    stats = client.get_stats()
                    print(f"[WS][CLIENT] 连接状态: {'✓' if ws_healthy else '✗'}, "
                          f"已订阅: {stats.get('subscribed_tokens', 0)}, "
                          f"已确认: {stats.get('confirmed_tokens', 0)}, "
                          f"待订阅: {stats.get('pending_subscribe', 0)}, "
                          f"连接次数: {stats.get('connect_count', 0)}")
            elif self._ws_thread and self._ws_thread.is_alive():
                ws_healthy = True

            if not ws_healthy:
                print("[WARN] WS 聚合器连接异常，尝试恢复...")
                if not self._ws_updates_suspended:
                    self._restart_ws_subscription(self._ws_token_ids)

        # 检查缓存数据
        with self._ws_cache_lock:
            token_count = len(self._ws_cache)
            subscribed_count = len(self._ws_token_ids)

            if subscribed_count > 0 and token_count == 0:
                print(f"[WARN] WS 聚合器订阅了 {subscribed_count} 个token，但缓存为空")
                print("[WARN] 可能尚未接收到数据，或连接异常")

            # 分级阈值检查（秒）
            THRESHOLD_WARNING = 1800  # 30分钟 - 警告
            THRESHOLD_CLEANUP_DEFAULT = 1800  # 默认30分钟清理（仅对已退订token生效）
            THRESHOLD_CLEANUP_EXTENDED = 3600  # 白名单续命后60分钟清理（仅对已退订token生效）
            RECOVERY_GRACE_WINDOW = 1800  # 最近30分钟内有恢复信号可续命

            now = time.time()
            warning_tokens = []  # 30分钟未更新
            cleanup_tokens = []  # 达到清理阈值（默认30分钟，恢复白名单可至60分钟）或市场已关闭
            closed_market_tokens = []  # 市场已关闭

            for token_id, data in list(self._ws_cache.items()):
                updated_at = data.get("updated_at", 0)
                age = now - updated_at

                # 检查市场状态
                is_closed = (
                    data.get("market_closed")
                    or data.get("is_closed")
                    or data.get("closed")
                )

                was_stale = self._ws_prev_stale_state.get(token_id, False)
                is_stale_now = age > THRESHOLD_WARNING
                if was_stale and not is_stale_now:
                    self._ws_recent_recovery_ts[token_id] = now
                self._ws_prev_stale_state[token_id] = is_stale_now

                recent_recovery_ts = self._ws_recent_recovery_ts.get(token_id, 0.0)
                has_recent_recovery = recent_recovery_ts > 0 and (now - recent_recovery_ts) <= RECOVERY_GRACE_WINDOW
                cleanup_threshold = THRESHOLD_CLEANUP_EXTENDED if has_recent_recovery else THRESHOLD_CLEANUP_DEFAULT

                if is_closed:
                    closed_market_tokens.append((token_id, age))
                    cleanup_tokens.append((token_id, age, "市场已关闭"))
                elif age > cleanup_threshold:
                    # WS 市场频道是事件驱动模型，长时间无事件并不代表 token 无效；
                    # 若 token 仍在订阅集合中，仅告警，不应从缓存删除，否则策略将持续拿到 None。
                    if token_id in self._ws_token_ids:
                        warning_tokens.append((token_id, age))
                    elif has_recent_recovery:
                        cleanup_tokens.append((token_id, age, f"{age/60:.0f}分钟无更新（恢复白名单已续命至60分钟）"))
                    else:
                        cleanup_tokens.append((token_id, age, f"{age/60:.0f}分钟无更新"))
                elif age > THRESHOLD_WARNING:
                    warning_tokens.append((token_id, age))

            # 清理过期/关闭的token
            if cleanup_tokens:
                for token_id, age, reason in cleanup_tokens:
                    del self._ws_cache[token_id]
                    self._ws_prev_stale_state.pop(token_id, None)
                    self._ws_recent_recovery_ts.pop(token_id, None)
                    # 记录到日志
                    _log_error("TOKEN_CLEANUP", {
                        "token_id": token_id,
                        "age_seconds": age,
                        "reason": reason,
                        "message": "从缓存中清理token"
                    })
                print(f"[CLEANUP] 清理 {len(cleanup_tokens)} 个过期/关闭的token:")
                for token_id, age, reason in cleanup_tokens[:3]:
                    print(f"  - {token_id[:20]}...: {reason}")
                self._ws_cache_dirty = True

            # 警告日志（降低频率：每60秒才打印一次）
            if not hasattr(self, '_last_warning_log'):
                self._last_warning_log = 0

            if warning_tokens and (now - self._last_warning_log >= 60):
                print(f"[HEALTH] {len(warning_tokens)} 个token数据超过30分钟未更新（仍在订阅则仅告警，不清理）")
                # 只显示前2个
                for token_id, age in warning_tokens[:2]:
                    print(f"  - {token_id[:20]}...: {age/60:.0f}分钟前")
                self._last_warning_log = now

        # 检查文件状态
        if self._ws_cache_path.exists():
            try:
                stat = self._ws_cache_path.stat()
                age = time.time() - stat.st_mtime
                if age > 120:  # 2分钟没更新
                    # 降低日志频率
                    if not hasattr(self, '_last_file_warn'):
                        self._last_file_warn = 0
                    if time.time() - self._last_file_warn >= 60:
                        print(f"[WARN] ws_cache.json 文件过期，最后修改: {age:.0f}秒前")
                        self._last_file_warn = time.time()
            except OSError:
                pass
        else:
            if self._ws_token_ids:
                print(f"[WARN] ws_cache.json 文件不存在: {self._ws_cache_path}")

    def _poll_tasks(self) -> None:
        for task in list(self.tasks.values()):
            proc = task.process
            if not proc:
                continue
            rc = proc.poll()
            if rc is None:
                task.status = "running"
                now = time.time()
                task.last_heartbeat = now
                self._update_log_excerpt(task)
                if self._check_stuck_and_refill(task, now):
                    continue
                if self._check_flat_price_cap_stuck_and_refill(task, now):
                    continue
                if self._log_indicates_market_end(task):
                    task.status = "ended"
                    task.no_restart = True
                    task.end_reason = "market closed"
                    task.heartbeat("market end detected from log")
                    print(
                        f"[AUTO] topic={task.topic_id} 日志显示市场已结束，自动结束该话题。"
                    )
                    self._mark_token_orphaned(
                        task.topic_id,
                        reason="MARKET_TERMINAL_DETECTED",
                        trigger_source="market_closed_auto",
                        task=task,
                        note="market closed/resolved detected from child log",
                    )
                    # 从 copytrade 文件中移除该 token，避免重启后再次加入
                    self._remove_token_from_copytrade_files(task.topic_id)
                continue
            self._handle_process_exit(task, rc)

        self._purge_inactive_tasks()

    def _enforce_sell_exit_deadlines(self) -> None:
        if not self._sell_exit_deadlines:
            return
        now = time.time()
        for token_id, deadline in list(self._sell_exit_deadlines.items()):
            if now < float(deadline):
                continue
            task = self.tasks.get(token_id)
            if not (task and task.is_running() and task.end_reason == "sell signal"):
                self._sell_exit_deadlines.pop(token_id, None)
                continue
            detail = self.topic_details.setdefault(token_id, {})
            exit_reason = self._normalize_exit_reason(detail.get("sell_exit_reason"))
            if not self._is_ioc_exit_reason_allowed(exit_reason):
                self._sell_exit_deadlines.pop(token_id, None)
                self._mark_token_orphaned(
                    token_id,
                    reason=f"DEADLINE_BLOCKED_{exit_reason or 'UNSPECIFIED'}",
                    trigger_source="sell_signal_grace_timeout",
                    task=task,
                    note="deadline reached but IOC reason not allowed",
                )
                continue

            print(
                "[COPYTRADE][WARN] sell signal grace timeout, force terminate and "
                f"fallback to exit-only cleanup: token_id={token_id}"
            )
            detail["sell_exit_phase"] = "forced_cleanup"
            self._terminate_task(task, reason="sell signal grace timeout -> force cleanup")
            self._sell_exit_deadlines.pop(token_id, None)
            if (
                token_id not in self.pending_exit_topics
                and token_id not in self.pending_topics
                and token_id not in self.pending_burst_topics
            ):
                self.pending_exit_topics.append(token_id)

    def _check_stuck_and_refill(self, task: TopicTask, now: float) -> bool:
        """检测长期卡顿话题：触发后终止并立即回填。"""
        last_line = (task.log_excerpt.splitlines() or [""])[-1].strip()
        lowered = last_line.lower()
        topic_id = task.topic_id

        price_cap_hit = (
            "[maker][buy]" in lowered
            and "已达到上限" in lowered
            and "等待回落" in lowered
        )
        shock_stuck_hit = "[shock][buy][defer]" in lowered or "[shock][buy][reject]" in lowered

        if not price_cap_hit:
            self._price_cap_stuck_since.pop(topic_id, None)
        if not shock_stuck_hit:
            self._shock_stuck_since.pop(topic_id, None)

        cap_limit_sec = max(0.0, float(self.config.price_cap_refill_stuck_minutes)) * 60.0
        if price_cap_hit and cap_limit_sec > 0:
            since = self._price_cap_stuck_since.setdefault(topic_id, now)
            if now - since >= cap_limit_sec:
                self._force_stuck_refill(task, "PRICE_CAP_STUCK", last_line, now - since)
                return True

        shock_limit_sec = max(0.0, float(self.config.shock_refill_stuck_minutes)) * 60.0
        if shock_stuck_hit and shock_limit_sec > 0:
            since = self._shock_stuck_since.setdefault(topic_id, now)
            if now - since >= shock_limit_sec:
                self._force_stuck_refill(task, "SHOCK_GUARD_STUCK", last_line, now - since)
                return True
        return False

    def _get_task_run_config(self, task: TopicTask) -> Dict[str, Any]:
        path = task.config_path
        if not path or not path.exists():
            return {}
        try:
            mtime = float(path.stat().st_mtime)
        except OSError:
            return {}
        cache = self._task_run_config_cache.get(task.topic_id)
        if cache and cache[0] == str(path) and abs(cache[1] - mtime) < 1e-9:
            return cache[2]
        data = _load_json_file(path)
        cfg = data if isinstance(data, dict) else {}
        self._task_run_config_cache[task.topic_id] = (str(path), mtime, cfg)
        return cfg

    def _task_buy_price_cap(self, task: TopicTask) -> float:
        cfg = self._get_task_run_config(task)
        cap = _coerce_float(cfg.get("max_buy_price"))
        if cap is None:
            cap = 0.98
        return float(cap)

    @staticmethod
    def _extract_flat_bid_from_excerpt(log_excerpt: str) -> Optional[float]:
        if not log_excerpt:
            return None
        for raw_line in reversed(log_excerpt.splitlines()):
            line = str(raw_line or "").strip()
            if "state=FLAT" not in line or "bid=" not in line:
                continue
            match = re.search(r"bid=([0-9]+(?:\.[0-9]+)?)", line)
            if not match:
                continue
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                continue
        return None

    def _check_flat_price_cap_stuck_and_refill(self, task: TopicTask, now: float) -> bool:
        topic_id = task.topic_id
        cfg = self._get_task_run_config(task)
        if bool(cfg.get("exit_only")) or bool(cfg.get("force_sell_only_on_startup")):
            self._price_cap_flat_stuck_since.pop(topic_id, None)
            return False
        bid = self._extract_flat_bid_from_excerpt(task.log_excerpt)
        if bid is None:
            self._price_cap_flat_stuck_since.pop(topic_id, None)
            return False
        cap = self._task_buy_price_cap(task)
        if bid < cap - 1e-12:
            self._price_cap_flat_stuck_since.pop(topic_id, None)
            return False
        since = self._price_cap_flat_stuck_since.setdefault(topic_id, now)
        stuck_sec = now - since
        if stuck_sec < PRICE_CAP_FLAT_STUCK_LIMIT_SEC:
            return False
        last_line = (task.log_excerpt.splitlines() or [""])[-1].strip()
        self._force_stuck_refill(task, "PRICE_CAP_FLAT_STUCK", last_line, stuck_sec)
        return True

    def _force_stuck_refill(
        self,
        task: TopicTask,
        exit_reason: str,
        last_line: str,
        stuck_seconds: float,
    ) -> None:
        """复用现有回填机制：记录退出原因 + 终止进程 + 立即入队。"""
        token_id = task.topic_id
        self._append_exit_token_record(
            token_id,
            exit_reason,
            exit_data={
                "stuck_seconds": stuck_seconds,
                "stuck_minutes": round(stuck_seconds / 60.0, 2),
                "last_line": last_line,
                "source": "autorun_stall_watchdog",
            },
            refillable=True,
        )

        # 立即回填（避免等待 refill_cooldown）
        self._refill_retry_counts[token_id] = self._refill_retry_counts.get(token_id, 0) + 1
        retry_count = self._refill_retry_counts[token_id]
        self._refilled_tokens.add(token_id)
        detail = self.topic_details.setdefault(token_id, {})
        detail["refill_retry_count"] = retry_count
        detail["refill_exit_reason"] = exit_reason
        detail.pop("resume_state", None)
        detail.pop("resume_drop_pct", None)
        self._enqueue_pending_topic(token_id)

        task.no_restart = True
        task.end_reason = exit_reason.lower()
        self._terminate_task(task, reason=f"{exit_reason} -> refill")
        self._price_cap_stuck_since.pop(token_id, None)
        self._price_cap_flat_stuck_since.pop(token_id, None)
        self._shock_stuck_since.pop(token_id, None)
        print(
            f"[REFILL][STUCK] token={token_id[:20]}... reason={exit_reason} "
            f"stuck={stuck_seconds/60.0:.1f}m retry={retry_count}/{self.config.max_refill_retries}"
        )

    def _handle_process_exit(self, task: TopicTask, rc: int) -> None:
        self._sell_exit_deadlines.pop(task.topic_id, None)
        if task.topic_id in self.topic_details:
            self.topic_details[task.topic_id].pop("sell_exit_requested_at", None)
            self.topic_details[task.topic_id].pop("sell_exit_deadline_at", None)
            self.topic_details[task.topic_id].pop("sell_exit_phase", None)
        self._price_cap_stuck_since.pop(task.topic_id, None)
        self._price_cap_flat_stuck_since.pop(task.topic_id, None)
        self._shock_stuck_since.pop(task.topic_id, None)
        task.process = None
        if task.status not in {"stopped", "exited", "error", "ended"}:
            task.status = "exited" if rc == 0 else "error"
        task.heartbeat(f"process finished rc={rc}")
        self._update_log_excerpt(task)
        # Force one immediate refresh to avoid interval gating hiding the latest REENTRY_HOLD marker.
        task.last_log_excerpt_ts = 0.0
        self._update_log_excerpt(task, max_bytes=8192)
        self._sync_reentry_hold_recovered_from_log(task)

        # 如果是因 sell signal 退出（包括运行中收到信号和 exit-only cleanup），
        # 仅在子进程退出码为 0 时标记“清仓完成”；
        # 非 0 视作异常，自动补排一次 exit-only cleanup，避免漏清仓。
        if task.end_reason in ("sell signal", "sell signal cleanup"):
            if rc == 0:
                round_closed, close_reason, position_size = self._confirm_round_close_from_position_truth(
                    task.topic_id,
                    force_refresh=True,
                )
                if (
                    not round_closed
                    and close_reason.startswith("position_snapshot_unavailable:")
                    and not self._position_address
                ):
                    round_closed = True
                    close_reason = "position_snapshot_unavailable_missing_address_assumed_closed"
                    position_size = 0.0
                if round_closed:
                    self._append_exit_token_record(
                        task.topic_id,
                        "EXIT_CLEANUP_SUCCESS",
                        exit_data={
                            "source": "exit_only_process",
                            "rc": rc,
                            "position_truth": close_reason,
                            "position_size": position_size,
                        },
                        refillable=False,
                    )
                    self._exit_cleanup_suppressed_until.pop(task.topic_id, None)
                    self._completed_exit_cleanup_tokens.add(task.topic_id)
                    self._exit_cleanup_retry_counts.pop(task.topic_id, None)
                    self._update_sell_signal_event(
                        task.topic_id,
                        status="done",
                        attempts=0,
                        note="exit_cleanup_success",
                    )
                    self._set_follow_cooldown_token(task.topic_id, cooldown_seconds=24.0 * 3600.0)
                    self._clear_manual_intervention_token(task.topic_id)
                    task_run_cfg = self._get_task_run_config(task)
                    self._advance_token_cycle_state_on_cleanup(task.topic_id, task_run_cfg)
                    # 仅在清仓成功后才释放 token 周期锁并清理 copytrade 文件记录：
                    # - handled_topics: 允许后续新一轮买入
                    # - tokens/sell_signals: 完成本轮生命周期闭环
                    self._remove_from_handled_topics(task.topic_id)
                    self._remove_token_from_copytrade_files(task.topic_id)
                    self._remove_exit_token_records(task.topic_id)
                    self._mark_token_cycle_closed_runtime(task.topic_id, task=task)
                else:
                    self._append_exit_token_record(
                        task.topic_id,
                        "EXIT_CLEANUP_INCOMPLETE_POSITION_REMAINS",
                        exit_data={
                            "source": "exit_only_process",
                            "rc": rc,
                            "position_size": position_size,
                            "close_reason": close_reason,
                        },
                        refillable=False,
                    )
                    rc = 75
                    print(
                        "[COPYTRADE][WARN] sell 清仓进程返回 rc=0 但仓位未真正闭合，按未完成处理: "
                        f"token={task.topic_id} reason={close_reason} position_size={position_size:.6f}"
                    )
            if rc != 0:
                self._append_exit_token_record(
                    task.topic_id,
                    "EXIT_CLEANUP_FAILED",
                    exit_data={
                        "source": "exit_only_process",
                        "rc": rc,
                    },
                    refillable=False,
                )
                self._completed_exit_cleanup_tokens.discard(task.topic_id)
                retry_count = self._exit_cleanup_retry_counts.get(task.topic_id, 0) + 1
                self._exit_cleanup_retry_counts[task.topic_id] = retry_count
                self._update_sell_signal_event(
                    task.topic_id,
                    status="failed",
                    attempts=retry_count,
                    note=f"exit_cleanup_rc_{rc}",
                )
                if retry_count <= EXIT_CLEANUP_MAX_RETRIES:
                    if (
                        task.topic_id not in self.pending_exit_topics
                        and task.topic_id not in self.pending_topics
                        and task.topic_id not in self.pending_burst_topics
                    ):
                        self.pending_exit_topics.append(task.topic_id)
                    print(
                        "[COPYTRADE][WARN] sell 清仓进程异常退出，已补排 exit-only cleanup: "
                        f"token_id={task.topic_id} rc={rc} "
                        f"retry={retry_count}/{EXIT_CLEANUP_MAX_RETRIES}"
                    )
                else:
                    # 达到最大重试次数，标记为"已完成"阻止 _apply_sell_signals 再次触发，
                    # 避免通过 handled_topics 移除 → 重新检测 → 再重试的无限循环。
                    self._completed_exit_cleanup_tokens.add(task.topic_id)
                    self._record_manual_intervention_token(
                        task.topic_id,
                        retry_count=retry_count,
                        rc=rc,
                    )
                    print(
                        "[COPYTRADE][ERROR] sell 清仓连续失败，已停止自动重试避免循环卡死: "
                        f"token_id={task.topic_id} rc={rc} "
                        f"retry={retry_count}/{EXIT_CLEANUP_MAX_RETRIES}"
                    )

        if self._finalize_position_reconcile_exit_if_flat(task, rc=rc):
            return
        if self._requeue_position_reconcile_after_clean_exit(task, rc=rc):
            return

        if task.no_restart:
            return

        if rc != 0:
            max_retries = max(0, int(self.config.process_start_retries))
            if task.restart_attempts < max_retries:
                running = sum(1 for t in self.tasks.values() if t.is_running())
                max_total = self._max_total_task_slots()
                if running >= max_total:
                    self._enqueue_pending_topic(task.topic_id)
                    task.status = "pending"
                    task.heartbeat(
                        f"restart deferred due to total concurrency cap ({running}/{max_total})"
                    )
                    return
                task.restart_attempts += 1
                task.status = "restarting"
                task.heartbeat(
                    f"restart attempt {task.restart_attempts}/{max_retries} after rc={rc}"
                )
                time.sleep(self.config.process_retry_delay_sec)
                if self._start_topic_process(task.topic_id):
                    return
                if task.restart_attempts < max_retries:
                    self._enqueue_pending_topic(task.topic_id)
            task.status = "error"

    def _update_log_excerpt(self, task: TopicTask, max_bytes: int = 2000) -> None:
        now = time.time()
        interval = max(0.0, float(self.config.log_excerpt_interval_sec))
        if interval and now - task.last_log_excerpt_ts < interval:
            return

        if not task.log_path or not task.log_path.exists():
            task.log_excerpt = ""
            return
        try:
            with task.log_path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                data = f.read().decode("utf-8", errors="ignore")
            lines = data.strip().splitlines()
            task.log_excerpt = "\n".join(lines[-5:])
            task.last_log_excerpt_ts = now
        except OSError as exc:  # pragma: no cover - 文件访问异常
            task.log_excerpt = f"<log read error: {exc}>"

    def _log_indicates_market_end(self, task: TopicTask) -> bool:
        excerpt = (task.log_excerpt or "").lower()
        if not excerpt:
            return False
        patterns = (
            "[market] 已确认市场结束",
            "[market] 市场结束",
            "[market] 达到市场截止时间",
            "[market] 收到市场关闭事件",
            "[exit] 最终状态",
        )
        return any(p.lower() in excerpt for p in patterns)

    def _check_market_closed_before_start(self, topic_id: str) -> bool:
        """
        在启动子进程前检查市场状态。

        返回 True 表示市场已关闭（应跳过），False 表示可以继续启动。
        只有在 WS 缓存中明确标记为 closed 时才返回 True，避免误判。
        """
        cache_data = self._ws_cache.get(topic_id)
        if not cache_data:
            # 缓存中无数据，不能确定市场状态，允许继续（由子进程判断）
            return False

        # 检查市场关闭标记
        is_closed = (
            cache_data.get("market_closed")
            or cache_data.get("is_closed")
            or cache_data.get("closed")
        )

        if is_closed:
            print(
                f"[SCHEDULE] {topic_id[:8]}... 市场已关闭（缓存标记），"
                f"跳过启动并清理"
            )
            # 记录到 exit_tokens，供后续清理
            self._append_exit_token_record(
                topic_id,
                "MARKET_CLOSED",
                exit_data={"detected_at": "schedule_before_start"},
                refillable=False,
            )
            # 从 copytrade 文件中移除
            self._remove_token_from_copytrade_files(topic_id)
            # 从 pending 相关状态中清理
            self._remove_pending_topic(topic_id)
            # 清理可能残留的 task 对象（从恢复阶段创建的）
            self.tasks.pop(topic_id, None)
            return True

        return False

    def _is_ws_confirmed(self, token_id: str) -> bool:
        with self._ws_client_lock:
            client = self._ws_client
        if client is None:
            return False
        checker = getattr(client, "is_token_confirmed", None)
        if callable(checker):
            try:
                return bool(checker(token_id))
            except Exception:
                return False
        return False

    def _probe_clob_book_available(self, token_id: str) -> tuple[bool, str]:
        if not token_id:
            return False, "empty_token"
        now = time.time()
        cached = self._clob_book_probe_cache.get(token_id)
        if cached and (now - float(cached.get("ts", 0.0))) <= CLOB_BOOK_PROBE_CACHE_TTL_SEC:
            return bool(cached.get("ok", False)), str(cached.get("reason", "cache"))

        url = f"{CLOB_API_ROOT}/book"
        try:
            resp = requests.get(
                url,
                params={"token_id": token_id},
                timeout=CLOB_BOOK_PROBE_TIMEOUT_SEC,
            )
            ok = resp.status_code == 200
            reason = f"status={resp.status_code}"
        except Exception as exc:
            ok = False
            reason = f"request_error:{exc.__class__.__name__}"

        self._clob_book_probe_cache[token_id] = {
            "ts": now,
            "ok": ok,
            "reason": reason,
        }
        return ok, reason

    def _schedule_pending_topics(self) -> None:
        if self._is_buy_paused_by_balance():
            # 低余额时：只defer无持仓的token，保留有持仓的可卖出
            # 不直接return，继续执行让有持仓的token可以启动
            self._defer_pending_topics_due_to_low_balance()

        self._rebalance_burst_to_base_queue()

        base_limit = max(1, int(self.config.max_concurrent_tasks))
        burst_limit = self._burst_slots()
        
        # 【修复】Burst/Base 运行时隔离：
        # - Burst token 最多 burst_limit 个（高优先级新token）
        # - Base token 最多 base_limit 个（普通refill token）
        # - 当 burst 满且 base 有空位时，降级最老的 burst token
        
        checks_remaining = len(self.pending_topics) + len(self.pending_burst_topics)

        while checks_remaining > 0:
            running_total = sum(1 for t in self.tasks.values() if t.is_running())
            max_total = self._max_total_task_slots()
            if running_total >= max_total:
                self._log_throttled(
                    "sched_cap_total",
                    30.0,
                    f"[SCHED][CAP] max concurrency reached: running={running_total}/{max_total} (base={base_limit}, burst={burst_limit})",
                )
                break
            running_burst = self._running_burst_count()
            running_base = max(0, running_total - running_burst)
            
            # 【修复】独立槽位检查：burst 和 base 分别限制
            burst_full = running_burst >= burst_limit
            base_full = running_base >= base_limit
            
            # 【修复】当 base 不满且 burst 满时，降级一个 burst 来补位
            # 这样能实现：burst=10满负荷运转，base通过降级逐步填满
            # 同时 pending_burst 快速消耗
            if not base_full and burst_full and self.pending_burst_topics:
                # base有空位但burst已满，降级最老的burst到base
                if self._demote_oldest_burst_token():
                    # 降级成功，重新计算计数
                    running_burst = self._running_burst_count()
                    running_base = running_total - running_burst
                    burst_full = running_burst >= burst_limit
                    base_full = running_base >= base_limit
            
            if burst_full and base_full:
                break  # 两者都满，停止调度

            # 【修复】优先级逻辑：
            # 1. 如果 pending_burst 有token，优先启动（填满burst到10）
            # 2. 只有当 pending_burst 为空时，才启动 pending_topics
            
            if self.pending_burst_topics and not burst_full:
                use_burst = True
                use_base = False
            elif self.pending_topics and not base_full:
                # 只有pending_burst为空时才启动pending_base
                use_burst = False
                use_base = True
            else:
                # 两者都无法启动（burst满且无法降级，或base满，或两者都空）
                break

            now = time.time()
            if now < self._next_topic_start_at:
                break

            lane = "burst" if use_burst else "base"
            if lane == "burst":
                topic_id = self.pending_burst_topics.pop(0)
            else:
                topic_id = self.pending_topics.pop(0)
            if self._block_topic_start_for_active_sell(
                topic_id,
                source=f"schedule_{lane}",
            ):
                checks_remaining -= 1
                continue
            # 启动前检查市场状态，若已关闭则跳过（不重新入队）
            if self._check_market_closed_before_start(topic_id):
                checks_remaining -= 1
                continue
            if topic_id in self.tasks and self.tasks[topic_id].is_running():
                checks_remaining -= 1
                continue

            paused_until = self._shared_ws_paused_until.get(topic_id)
            if paused_until and now < paused_until:
                if lane == "burst":
                    self._enqueue_burst_topic(topic_id)
                else:
                    self._enqueue_pending_topic(topic_id)
                checks_remaining -= 1
                continue

            with self._ws_cache_lock:
                cached = self._ws_cache.get(topic_id)
            if not cached:
                if self.config.ws_ready_use_confirmed and self._is_ws_confirmed(topic_id):
                    grace_sec = max(0.0, float(self.config.ws_ready_confirm_grace_sec))
                    pending_since = self._shared_ws_pending_since.setdefault(topic_id, now)
                    waited = now - pending_since
                    if waited >= grace_sec:
                        with self._ws_cache_lock:
                            prev = self._ws_cache.get(topic_id) or {}
                            seq = int(prev.get("seq", 0)) + 1
                            self._ws_cache[topic_id] = {
                                **prev,
                                "seq": seq,
                                "updated_at": now,
                                "source": "ws_confirmed_bootstrap",
                            }
                            self._ws_cache_dirty = True
                        cached = self._ws_cache.get(topic_id)
                        self._shared_ws_wait_failures[topic_id] = 0
                        self._shared_ws_paused_until.pop(topic_id, None)
                        self._shared_ws_wait_timeout_events[topic_id] = []
                        print(
                            f"[WS][READY] topic={topic_id[:8]}... confirmed已就绪"
                            f" (等待了 {waited:.1f}s, source=confirmed)"
                        )
                if cached:
                    pass
                else:
                    pending_since = self._shared_ws_pending_since.setdefault(topic_id, now)
                    waited = now - pending_since
                    max_wait = max(1.0, float(self.config.shared_ws_max_pending_wait_sec))
                    if waited >= max_wait:
                        failures = self._shared_ws_wait_failures.get(topic_id, 0) + 1
                        self._shared_ws_wait_failures[topic_id] = failures
                        self._shared_ws_pending_since[topic_id] = now
                        max_failures = max(
                            1, int(self.config.shared_ws_wait_failures_before_pause)
                        )
                        print(
                            f"[WS][WAIT] topic={topic_id[:8]}... 等待WS缓存"
                            f" {max_wait:.0f}s超时 ({failures}/{max_failures})"
                        )

                        escalation_window = max(
                            max_wait,
                            float(
                                getattr(
                                    self.config,
                                    "shared_ws_wait_escalation_window_sec",
                                    SHARED_WS_WAIT_ESCALATION_WINDOW_SEC,
                                )
                            ),
                        )
                        min_escalation_failures = max(
                            1,
                            int(
                                getattr(
                                    self.config,
                                    "shared_ws_wait_escalation_min_failures",
                                    SHARED_WS_WAIT_ESCALATION_MIN_FAILURES,
                                )
                            ),
                        )
                        timeout_events = self._shared_ws_wait_timeout_events.get(topic_id, [])
                        timeout_events = [
                            ts for ts in timeout_events if (now - ts) <= escalation_window
                        ]
                        timeout_events.append(now)
                        self._shared_ws_wait_timeout_events[topic_id] = timeout_events

                        should_pause = (
                            failures >= max_failures
                            and len(timeout_events) >= min_escalation_failures
                        )
                        if should_pause:
                            pause_seconds = max(
                                60.0, float(self.config.shared_ws_wait_pause_minutes) * 60.0
                            )
                            self._shared_ws_paused_until[topic_id] = now + pause_seconds
                            self._shared_ws_wait_failures[topic_id] = 0
                            self._shared_ws_wait_timeout_events[topic_id] = []
                            print(
                                f"[WS][PAUSE] topic={topic_id[:8]}... 暂停 {pause_seconds:.0f}s"
                            )
                    if lane == "burst":
                        self._enqueue_burst_topic(topic_id)
                    else:
                        self._enqueue_pending_topic(topic_id)
                    checks_remaining -= 1
                    continue
            if topic_id in self._shared_ws_pending_since:
                waited = now - self._shared_ws_pending_since.pop(topic_id)
                self._shared_ws_wait_failures[topic_id] = 0
                self._shared_ws_paused_until.pop(topic_id, None)
                self._shared_ws_wait_timeout_events[topic_id] = []
                print(
                    f"[WS][READY] topic={topic_id[:8]}... 缓存就绪"
                    f" (等待了 {waited:.1f}s)"
                )

            detail = self.topic_details.setdefault(topic_id, {})
            detail["schedule_lane"] = lane
            self._ws_starting_topics.add(topic_id)
            try:
                started = self._start_topic_process(topic_id)
            except Exception as exc:  # pragma: no cover - 防御性保护
                print(f"[ERROR] 调度话题 {topic_id} 时异常: {exc}")
                traceback.print_exc()
                _log_error("TASK_SCHEDULE_ERROR", {
                    "message": "调度话题时异常",
                    "topic_id": topic_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc()
                })
                started = False
            finally:
                # 启动结束（成功或失败）后释放启动窗口标记
                self._ws_starting_topics.discard(topic_id)
            if not started:
                # 启动失败时重新入队，避免话题被遗忘
                if lane == "burst":
                    self._enqueue_burst_topic(topic_id)
                else:
                    self._enqueue_pending_topic(topic_id)
            elif started:
                self._buy_pause_deferred_tokens.discard(topic_id)
                self._shared_ws_pending_since.pop(topic_id, None)
                self._next_topic_start_at = now + max(
                    0.0, float(self.config.topic_start_cooldown_sec)
                )
            checks_remaining -= 1

    def _schedule_pending_exit_cleanup(self) -> None:
        # 清仓任务使用独立的槽位限制，不受 max_concurrent_tasks 约束
        # 这确保了当普通任务槽位已满时，清仓任务仍能及时执行
        exit_running = sum(
            1 for t in self.tasks.values()
            if t.is_running() and t.end_reason == "sell signal cleanup"
        )
        max_exit_slots = max(1, int(self.config.max_exit_cleanup_tasks))
        deferred: List[str] = []
        while self.pending_exit_topics and exit_running < max_exit_slots:
            token_id = self.pending_exit_topics.pop(0)
            if token_id in self.tasks and self.tasks[token_id].is_running():
                # maker 进程仍在运行，暂时跳过但保留在队列中等下次调度
                deferred.append(token_id)
                continue
            self._start_exit_cleanup(token_id)
            exit_running += 1
            stagger = max(0.0, float(self.config.exit_start_stagger_sec))
            if stagger > 0 and self.pending_exit_topics and exit_running < max_exit_slots:
                time.sleep(stagger)
        # 将被跳过的 token 放回队列尾部，避免丢失
        if deferred:
            self.pending_exit_topics.extend(deferred)

    def _enqueue_pending_topic(self, topic_id: str) -> bool:
        if not topic_id:
            return False
        if self._block_topic_start_for_active_sell(topic_id, source="enqueue_base"):
            return False
        if topic_id not in self.pending_topics:
            self.pending_topics.append(topic_id)
        self._pending_first_seen.setdefault(topic_id, time.time())
        return True

    def _rebalance_burst_to_base_queue(self) -> None:
        """【已废弃】在新 Burst/Base 隔离逻辑下，不再将 pending burst token 移到 base。"""
        # 【修复】直接返回，不再执行回挪，保持 pending_burst_topics 不变
        # 让 _schedule_pending_topics 中的优先级逻辑决定启动顺序
        return
        # 以下代码不再执行：
        if not self.pending_burst_topics:
            return

        base_limit = max(1, int(self.config.max_concurrent_tasks))
        running_total = sum(1 for t in self.tasks.values() if t.is_running())
        running_burst = self._running_burst_count()
        running_base = max(0, running_total - running_burst)
        base_slots_available = max(0, base_limit - running_base)
        if base_slots_available <= 0:
            return

        moved: List[str] = []
        kept: List[str] = []
        for topic_id in self.pending_burst_topics:
            detail = self.topic_details.get(topic_id) or {}
            role = self._queue_role(detail)
            # reentry token 保持在 burst，确保其优先级高于新 token
            if role == "reentry_token":
                kept.append(topic_id)
                continue
            if base_slots_available > 0:
                moved.append(topic_id)
                base_slots_available -= 1
            else:
                kept.append(topic_id)

        if not moved:
            return

        self.pending_burst_topics = kept
        # 头插 base 队列，确保“有空位即挪入”并尽快被调度
        for topic_id in reversed(moved):
            if topic_id in self.pending_topics:
                continue
            self.pending_topics.insert(0, topic_id)
            self._pending_first_seen.setdefault(topic_id, time.time())
            detail = self.topic_details.setdefault(topic_id, {})
            detail["schedule_lane"] = "base"

        print(
            f"[SCHED][REBALANCE] base空位回挪 {len(moved)} 个 token 到pending_base（burst_wait={len(self.pending_burst_topics)}）"
        )

    def _enqueue_burst_topic(self, topic_id: str, *, promote: bool = False) -> bool:
        if not topic_id:
            return False
        if self._block_topic_start_for_active_sell(topic_id, source="enqueue_burst"):
            return False
        if topic_id in self.pending_topics or topic_id in self.pending_burst_topics:
            self._remove_pending_topic(topic_id)
        if topic_id in self.pending_burst_topics:
            if promote:
                try:
                    self.pending_burst_topics.remove(topic_id)
                except ValueError:
                    pass
                self.pending_burst_topics.insert(0, topic_id)
            return True
        if promote:
            self.pending_burst_topics.insert(0, topic_id)
        else:
            self.pending_burst_topics.append(topic_id)
        self._pending_first_seen.setdefault(topic_id, time.time())
        # 【优化】为没有角色的token设置默认角色，便于日志识别
        detail = self.topic_details.setdefault(topic_id, {})
        if not detail.get("queue_role"):
            detail["queue_role"] = "burst_candidate"
        return True

    def _remove_burst_topic(self, topic_id: str) -> None:
        if topic_id in self.pending_burst_topics:
            try:
                self.pending_burst_topics.remove(topic_id)
            except ValueError:
                pass
        if topic_id not in self.pending_topics:
            self._pending_first_seen.pop(topic_id, None)
            self._shared_ws_wait_failures.pop(topic_id, None)
            self._shared_ws_paused_until.pop(topic_id, None)
            self._shared_ws_wait_timeout_events.pop(topic_id, None)
            self._shared_ws_pending_since.pop(topic_id, None)

    def _remove_pending_topic(self, topic_id: str) -> None:
        if topic_id in self.pending_topics:
            try:
                self.pending_topics.remove(topic_id)
            except ValueError:
                pass
        if topic_id in self.pending_burst_topics:
            try:
                self.pending_burst_topics.remove(topic_id)
            except ValueError:
                pass
        self._pending_first_seen.pop(topic_id, None)
        self._shared_ws_wait_failures.pop(topic_id, None)
        self._shared_ws_paused_until.pop(topic_id, None)
        self._shared_ws_wait_timeout_events.pop(topic_id, None)
        self._shared_ws_pending_since.pop(topic_id, None)

    def _get_condition_id_for_token(self, token_id: str) -> Optional[str]:
        """
        根据 token_id 获取 condition_id
        
        从 topic_details 或 latest_topics 中查找
        """
        # 1. 从 topic_details 中查找
        with self._topic_details_lock:
            detail = self.topic_details.get(token_id, {})
            condition_id = (
                detail.get("condition_id") 
                or detail.get("conditionId")
                or detail.get("market_id")
            )
            if condition_id:
                return condition_id
        
        # 2. 从 latest_topics 中查找
        for entry in self.latest_topics:
            entry_token_id = _topic_id_from_entry(entry)
            if entry_token_id == token_id:
                # 【修复】确保 entry 是字典类型才调用 .get()
                if isinstance(entry, dict):
                    condition_id = (
                        entry.get("condition_id")
                        or entry.get("conditionId")
                        or entry.get("market_id")
                    )
                    if condition_id:
                        return condition_id
                # 如果 entry 是字符串或其他类型，无法获取 condition_id
                break
        
        return None

    def _poll_aggressive_self_sell_reentry(self) -> None:
        """检测自有账户卖出成交，将token重新加入burst队列。
        
        【重构】此功能与mode解耦，classic模式下也可启用。
        区别：classic模式进程结束撤卖单，但卖出成交后仍可reentry。
        """
        if self.config.aggressive_reentry_source != "self_account_fills_only":
            return
        # 【重构】使用新的enable_reentry参数（兼容旧的aggressive_enable_self_sell_reentry）
        enable_reentry = getattr(self.config, 'enable_reentry', None) or getattr(self.config, 'aggressive_enable_self_sell_reentry', False)
        if not enable_reentry:
            return

        if not self._position_address:
            address, origin = _resolve_position_address_from_env()
            self._position_address = address
            self._position_address_origin = origin
            if not address and not self._position_address_warned:
                self._position_address_warned = True
                print(f"[AGGRESSIVE][WARN] {origin}")
        if not self._position_address:
            return

        trades, info = _fetch_recent_trades_from_data_api(
            self._position_address,
            limit=200,
        )
        if info != "ok":
            print(f"[AGGRESSIVE][INFO] 自有账户成交查询失败 info={info}")
            return

        processed = 0
        max_seen = self._last_self_sell_trade_ts
        for row in trades:
            side = str(row.get("side") or "").upper()
            if side != "SELL":
                continue

            token_id = str(row.get("asset") or row.get("token_id") or "").strip()
            if not token_id:
                continue
            if self.config.aggressive_first_sell_fill_only and token_id in self._seen_self_sell_tokens:
                continue
            ts = _normalize_unix_ts_seconds(row.get("timestamp"))
            if ts > max_seen:
                max_seen = ts

            # 仅接受本次 autorun 启动之后的卖出成交，避免历史成交把 burst 长期顶满。
            if ts < int(self._process_started_at):
                continue

            trade_key = (
                f"{token_id}:{side}:{ts}:"
                f"{row.get('size')}:{row.get('price')}:{row.get('conditionId')}"
            )
            if ts < self._last_self_sell_trade_ts:
                continue
            if trade_key in self._seen_self_sell_trade_keys:
                continue

            task = self.tasks.get(token_id)
            if task and task.is_running():
                self._seen_self_sell_trade_keys.add(trade_key)
                continue
            if token_id in self.pending_burst_topics:
                self._seen_self_sell_trade_keys.add(trade_key)
                continue
            if token_id not in self._reentry_eligible_tokens:
                self._seen_self_sell_trade_keys.add(trade_key)
                continue

            # reentry token 在 burst 等待队列中始终优先于 new token
            promote = True
            detail = self.topic_details.setdefault(token_id, {})
            detail["queue_role"] = "reentry_token"
            self._enqueue_burst_topic(token_id, promote=promote)
            self._seen_self_sell_trade_keys.add(trade_key)
            self._seen_self_sell_tokens.add(token_id)
            reentry_source = self._reentry_eligible_reasons.get(token_id, "unknown")
            processed += 1
            print(
                "[AGGRESSIVE][REENTRY] 检测到自有账户卖出成交，已加入burst队列: "
                f"token={token_id} ts={ts} size={row.get('size')} price={row.get('price')} source={reentry_source}"
            )

        if max_seen > self._last_self_sell_trade_ts:
            self._last_self_sell_trade_ts = max_seen
        if len(self._seen_self_sell_trade_keys) > 5000:
            self._seen_self_sell_trade_keys.clear()
        if processed:
            print(f"[AGGRESSIVE][REENTRY] 本轮新增回拉 token={processed}")

    def _append_exit_token_record(
        self,
        token_id: str,
        exit_reason: str,
        *,
        exit_data: Optional[Dict[str, Any]] = None,
        refillable: bool = False,
    ) -> None:
        if not token_id:
            return
        reason_upper = str(exit_reason or "").strip().upper()
        if reason_upper == "SELL_ABANDONED":
            self._set_active_unmanaged_rearm_block(
                token_id,
                reason=reason_upper,
            )
        if self._is_reentry_eligible_exit(exit_reason, exit_data):
            self._mark_reentry_eligible_token(
                token_id,
                source=reason_upper,
            )
        merged_exit_data = dict(exit_data or {})
        trigger_source = str(
            merged_exit_data.get("trigger_source")
            or merged_exit_data.get("source")
            or merged_exit_data.get("requested_source")
            or ""
        ).strip()
        trigger_reason = str(
            merged_exit_data.get("trigger_reason")
            or merged_exit_data.get("requested_reason")
            or str(exit_reason or "").strip().upper()
        ).strip()
        trigger_path = str(
            merged_exit_data.get("trigger_path")
            or merged_exit_data.get("path")
            or ""
        ).strip()

        # 【修改】使用文件锁保护，与清理器保持一致
        with self._file_io_lock:
            records = self._load_exit_tokens()
            if not isinstance(records, list):
                records = []
            records.append(
                {
                    "token_id": token_id,
                    "exit_ts": time.time(),
                    "exit_reason": exit_reason,
                    "exit_data": merged_exit_data,
                    "trigger_source": trigger_source,
                    "trigger_reason": trigger_reason,
                    "trigger_path": trigger_path,
                    "refillable": refillable,
                }
            )
            try:
                self._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
                with self._exit_tokens_path.open("w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
            except OSError as exc:  # pragma: no cover - 文件系统异常
                print(f"[WARN] 写入 exit_tokens.json 失败: {exc}")

    def _remove_exit_token_records(self, token_id: str) -> None:
        # Keep full audit history for postmortem analysis.
        return

    def _evict_stale_pending_topics(self) -> None:
        if not self.config.enable_pending_soft_eviction:
            return
        if not self.pending_topics and not self.pending_burst_topics:
            return

        now = time.time()
        cutoff_sec = max(0.0, float(self.config.pending_soft_eviction_minutes)) * 60.0
        if cutoff_sec <= 0:
            return

        evicted: List[str] = []
        pending_all = list(dict.fromkeys(self.pending_topics + self.pending_burst_topics))
        for topic_id in pending_all:
            first_seen = self._pending_first_seen.get(topic_id, now)
            if now - first_seen < cutoff_sec:
                continue
            with self._ws_cache_lock:
                cached = self._ws_cache.get(topic_id)
            if cached:
                updated_at = cached.get("updated_at", 0)
                if updated_at and (now - float(updated_at)) < cutoff_sec:
                    continue
                cache_age = now - float(updated_at) if updated_at else None
            else:
                cache_age = None

            # 多信号一致性判定，避免将“事件驱动无更新”误判为不可交易。
            # 至少同时满足：
            # 1) WS 未确认；
            # 2) CLOB /book 探针不可用；
            # 才会进入 NO_DATA_TIMEOUT 软淘汰。
            ws_confirmed = self._is_ws_confirmed(topic_id)
            if ws_confirmed:
                continue

            book_available, book_reason = self._probe_clob_book_available(topic_id)
            if book_available:
                continue

            # 【新增】当 Book 返回 404 时，主动查询市场状态，避免误判
            exit_reason = "NO_DATA_TIMEOUT"
            refillable = True
            exit_data_extra = {}
            
            if "404" in book_reason and self._market_state_checker:
                condition_id = self._get_condition_id_for_token(topic_id)
                if condition_id:
                    try:
                        market_state = self._market_state_checker.check_market_state(
                            condition_id, topic_id, use_cache=False
                        )
                        exit_data_extra["market_state"] = market_state.to_dict()
                        
                        if market_state.is_permanently_closed:
                            # 市场已关闭 - 永久退出，不可回填
                            exit_reason = "MARKET_CLOSED"
                            refillable = False
                            print(
                                f"[PENDING] 市场已关闭，永久移除: {topic_id[:16]}... "
                                f"status={market_state.status.value}"
                            )
                            # 清理文件
                            if self._market_closed_cleaner:
                                copytrade_state_path = self.config.copytrade_tokens_path.parent / "copytrade_state.json"
                                self._market_closed_cleaner.clean_closed_market(
                                    token_id=topic_id,
                                    condition_id=condition_id,
                                    exit_reason=exit_reason,
                                    copytrade_file=str(self.config.copytrade_tokens_path),
                                    copytrade_state_file=str(copytrade_state_path) if copytrade_state_path.exists() else None,
                                    exit_tokens_file=str(self._exit_tokens_path),
                                )
                        elif market_state.status == MarketStatus.LOW_LIQUIDITY:
                            # 低流动性市场 - 正常退出，可回填
                            exit_reason = "LOW_LIQUIDITY_TIMEOUT"
                            refillable = True
                            print(
                                f"[PENDING] 低流动性市场超时: {topic_id[:16]}... "
                                f"(bids={market_state.data.get('bids_count', 0)}, "
                                f"asks={market_state.data.get('asks_count', 0)})"
                            )
                    except Exception as e:
                        print(f"[WARNING] 市场状态检测失败: {topic_id[:16]}..., error={e}")

            exit_data = {
                "pending_age_sec": round(now - first_seen, 1),
                "cache_age_sec": round(cache_age, 1) if cache_age is not None else None,
                "ws_confirmed": ws_confirmed,
                "book_probe_reason": book_reason,
                **exit_data_extra,
            }
            self._remove_pending_topic(topic_id)
            self._append_exit_token_record(
                topic_id,
                exit_reason,
                exit_data=exit_data,
                refillable=refillable,
            )
            evicted.append(topic_id)

        if evicted:
            print(
                f"[PENDING] 软淘汰 {len(evicted)} 个token（无缓存/长期未更新）"
            )

    def _get_order_base_volume(self) -> Optional[float]:
        return None

    def _build_run_config(self, topic_id: str) -> Dict[str, Any]:
        base_template_raw = json.loads(json.dumps(self.run_params_template or {}))
        base_template = {k: v for k, v in base_template_raw.items() if v is not None}

        base_raw = self.strategy_defaults.get("default", {}) or {}
        base = {k: v for k, v in base_raw.items() if v is not None}

        topic_overrides_raw = (self.strategy_defaults.get("topics") or {}).get(
            topic_id, {}
        )
        topic_overrides = {
            k: v for k, v in topic_overrides_raw.items() if v is not None
        }

        merged = {**base_template, **base, **topic_overrides}

        topic_info = self.topic_details.get(topic_id, {})
        queue_role = str(topic_info.get("queue_role") or "").strip().lower()
        if queue_role in {
            "restored_token",
            "startup_reconcile_position",
            "refill_with_position",
        } and not isinstance(topic_info.get("resume_state"), dict):
            self._ensure_resume_state_from_live_position(topic_id)
            topic_info = self.topic_details.get(topic_id, {})
        slug = topic_info.get("slug")
        if slug:
            merged["market_url"] = f"https://polymarket.com/market/{slug}"
        merged["topic_id"] = topic_id

        if topic_info.get("title"):
            merged["topic_name"] = topic_info.get("title")
        if topic_info.get("token_id"):
            merged["token_id"] = topic_info.get("token_id")
        if not merged.get("token_id"):
            merged["token_id"] = topic_id
        include_exit_signal = bool(topic_info.get("sell_exit_reason"))
        if not include_exit_signal:
            resume_state = topic_info.get("resume_state")
            include_exit_signal = bool(
                isinstance(resume_state, dict) and resume_state.get("has_position")
            )
        if not include_exit_signal:
            include_exit_signal = self._has_account_position(topic_id)
        if include_exit_signal:
            merged["exit_signal_path"] = str(self._exit_signal_path(topic_id))
        else:
            merged.pop("exit_signal_path", None)
        if topic_info.get("yes_token"):
            merged["yes_token"] = topic_info.get("yes_token")
        if topic_info.get("no_token"):
            merged["no_token"] = topic_info.get("no_token")
        if topic_info.get("end_time"):
            merged["end_time"] = topic_info.get("end_time")

        base_order_size = _coerce_float(merged.get("order_size"))
        total_volume = _coerce_float(topic_info.get("total_volume"))
        volume_growth_factor = _coerce_float(merged.get("volume_growth_factor"))
        if base_order_size is not None and total_volume is not None:
            scaled_size = _scale_order_size_by_volume(
                base_order_size,
                total_volume,
                base_volume=self._get_order_base_volume(),
                growth_factor=volume_growth_factor
                if volume_growth_factor is not None and volume_growth_factor > 0
                else 0.5,
            )
            merged["order_size"] = scaled_size

        # Slot refill (回填) 恢复状态
        resume_state = topic_info.get("resume_state")
        if resume_state:
            merged["resume_state"] = resume_state
            refill_retry_count = topic_info.get("refill_retry_count", 0)
            merged["refill_retry_count"] = refill_retry_count
        if topic_info.get("force_sell_only_on_startup"):
            merged["force_sell_only_on_startup"] = True
        if topic_info.get("force_sell_only_reason"):
            merged["force_sell_only_reason"] = str(topic_info.get("force_sell_only_reason"))
        if topic_info.get("startup_skip_if_open_sell"):
            merged["startup_skip_if_open_sell"] = True
        elif isinstance(resume_state, dict) and bool(resume_state.get("skip_buy")):
            merged["startup_skip_if_open_sell"] = True
        if topic_info.get("sell_exit_reason"):
            merged["exit_cleanup_reason"] = str(topic_info.get("sell_exit_reason"))
        if topic_info.get("sell_trigger_source"):
            merged["exit_cleanup_source"] = str(topic_info.get("sell_trigger_source"))
        merged["allow_ioc_exit_reasons"] = sorted(ALLOWED_EXIT_SIGNAL_REASONS)

        resume_drop_pct = topic_info.get("resume_drop_pct")
        if resume_drop_pct is not None:
            merged["resume_drop_pct"] = resume_drop_pct
        resume_profit_pct = topic_info.get("resume_profit_pct")
        if resume_profit_pct is not None:
            merged["resume_profit_pct"] = resume_profit_pct

        # Maker 子进程配置（从 global_config 传递）
        merged["maker_poll_sec"] = self.config.maker_poll_sec
        merged["maker_position_sync_interval"] = self.config.maker_position_sync_interval
        merged["startup_double_zero_confirm_hits"] = int(
            max(1, self.config.startup_double_zero_confirm_hits)
        )
        merged["startup_double_zero_probe_interval_sec"] = float(
            max(0.0, self.config.startup_double_zero_probe_interval_sec)
        )
        merged["strategy_mode"] = "aggressive" if self._is_aggressive_mode() else "classic"
        
        # 【重构】处理exhausted状态（3次refill后）
        retry_count = topic_info.get("refill_retry_count", 0)
        max_retries = self.config.max_refill_retries
        is_exhausted = retry_count >= max_retries and max_retries > 0
        
        # 默认：aggressive模式保留卖单，classic模式撤卖单。
        # classic模式可按价格阈值切换为“保留卖单”。
        base_keep_orders = bool(self._is_aggressive_mode())
        if not base_keep_orders:
            threshold = float(
                max(
                    0.0,
                    min(
                        1.0,
                        float(
                            getattr(
                                self.config,
                                "classic_keep_orders_price_threshold",
                                0.8,
                            )
                            or 0.8
                        ),
                    ),
                )
            )
            price_hint = self._topic_price_hint(topic_id, topic_info)
            if price_hint is not None and price_hint >= threshold:
                base_keep_orders = True
                merged["classic_keep_orders_by_price"] = True
                merged["classic_keep_orders_price_hint"] = round(float(price_hint), 6)
                merged["classic_keep_orders_price_threshold"] = threshold
        merged["keep_sell_orders_on_timeout"] = base_keep_orders
        
        # 传递exhausted_enable_reentry参数（用于Volatility_arbitrage_run.py）
        merged["exhausted_enable_reentry"] = self.config.exhausted_enable_reentry
        merged["is_exhausted_token"] = is_exhausted

        return merged

    def _topic_price_hint(
        self, topic_id: str, topic_info: Optional[Dict[str, Any]] = None
    ) -> Optional[float]:
        info = topic_info or {}
        candidates: List[Any] = []

        with self._ws_cache_lock:
            ws_snap = dict(self._ws_cache.get(topic_id) or {})
        candidates.extend(
            [
                ws_snap.get("best_ask"),
                ws_snap.get("price"),
                ws_snap.get("best_bid"),
            ]
        )
        candidates.extend(
            [
                info.get("best_ask"),
                info.get("last_ask"),
                info.get("ask"),
                info.get("price"),
                info.get("last_price"),
                info.get("entry_price"),
            ]
        )
        resume_state = info.get("resume_state") if isinstance(info.get("resume_state"), dict) else {}
        if resume_state:
            candidates.append(resume_state.get("entry_price"))

        for raw in candidates:
            px = _coerce_float(raw)
            if px is None:
                continue
            if px > 0:
                return float(px)
        return None

    @staticmethod
    def _normalize_market_token_ids(raw_value: Any) -> List[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, (list, tuple)):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        if isinstance(raw_value, str):
            trimmed = raw_value.strip()
            if not trimmed:
                return []
            if trimmed.startswith("[") and trimmed.endswith("]"):
                try:
                    parsed = json.loads(trimmed)
                except Exception:
                    parsed = None
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            return [trimmed]
        return [str(raw_value).strip()]

    def _fetch_market_metadata_from_gamma_by_token_id(
        self, token_id: str
    ) -> tuple[Optional[Dict[str, Any]], str]:
        if not token_id:
            return None, "empty_token_id"
        now = time.time()
        cached = self._gamma_market_probe_cache.get(token_id)
        if cached and (now - float(cached.get("ts", 0.0))) <= GAMMA_MARKET_CACHE_TTL_SEC:
            cached_market = cached.get("market")
            if isinstance(cached_market, dict):
                return dict(cached_market), "cache"
            return None, str(cached.get("reason") or "cache_miss")

        last_reason = "gamma_not_found"
        for param_name in ("clob_token_ids", "clobTokenIds", "clob_token_id", "clobTokenId"):
            try:
                resp = requests.get(
                    f"{GAMMA_API_ROOT}/markets",
                    params={param_name: str(token_id), "limit": 50},
                    timeout=GAMMA_MARKET_PROBE_TIMEOUT_SEC,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                last_reason = f"{param_name}_request_error:{exc.__class__.__name__}"
                continue

            rows: List[Any]
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                rows = payload.get("data") or []
            elif isinstance(payload, list):
                rows = payload
            else:
                rows = []

            token_str = str(token_id)
            matches: List[Dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                token_ids = self._normalize_market_token_ids(
                    row.get("clobTokenIds")
                    or row.get("clob_token_ids")
                    or row.get("clobTokenId")
                    or row.get("clob_token_id")
                )
                if token_str in token_ids:
                    matches.append(row)
            if not matches:
                last_reason = f"{param_name}_no_match"
                continue

            selected = None
            for row in matches:
                if row.get("active") is True and row.get("closed") is False:
                    selected = row
                    break
            if selected is None:
                selected = matches[0]
            if isinstance(selected, dict):
                self._gamma_market_probe_cache[token_id] = {
                    "ts": now,
                    "market": dict(selected),
                    "reason": f"{param_name}_ok",
                }
                return dict(selected), param_name

        self._gamma_market_probe_cache[token_id] = {
            "ts": now,
            "market": None,
            "reason": last_reason,
        }
        return None, last_reason

    def _hydrate_topic_metadata_for_blacklist(
        self, token_id: str, *, force_official_check: bool = False
    ) -> None:
        if not token_id:
            return
        detail = self.topic_details.setdefault(token_id, {})
        title = str(detail.get("title") or "").strip()
        slug = str(detail.get("slug") or "").strip()
        if title and slug and not force_official_check:
            detail["blacklist_metadata_verified"] = bool(
                detail.get("blacklist_metadata_verified", True)
            )
            return

        for entry in self.latest_topics:
            if _topic_id_from_entry(entry) != token_id or not isinstance(entry, dict):
                continue
            if not title:
                candidate_title = str(entry.get("title") or "").strip()
                if candidate_title:
                    detail["title"] = candidate_title
                    title = candidate_title
            if not slug:
                candidate_slug = str(entry.get("slug") or "").strip()
                if candidate_slug:
                    detail["slug"] = candidate_slug
                    slug = candidate_slug
            if title and slug and not force_official_check:
                return

        for entry in self._load_copytrade_tokens():
            if _topic_id_from_entry(entry) != token_id or not isinstance(entry, dict):
                continue
            if not title:
                candidate_title = str(entry.get("title") or "").strip()
                if candidate_title:
                    detail["title"] = candidate_title
                    title = candidate_title
            if not slug:
                candidate_slug = str(entry.get("slug") or "").strip()
                if candidate_slug:
                    detail["slug"] = candidate_slug
                    slug = candidate_slug
            if (title or slug) and not force_official_check:
                return
            break

        # Fallback: positions cache from data-api includes title/slug for held tokens.
        positions_cache_path = self.config.data_dir / "positions_cache.json"
        if positions_cache_path.exists():
            try:
                payload = _load_json_file(positions_cache_path)
                rows = payload.get("positions") if isinstance(payload, dict) else []
            except Exception:
                rows = []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    asset = str(row.get("asset") or "").strip()
                    if asset != token_id:
                        continue
                    if not title:
                        candidate_title = str(
                            row.get("title") or row.get("question") or ""
                        ).strip()
                        if candidate_title:
                            detail["title"] = candidate_title
                            title = candidate_title
                    if not slug:
                        candidate_slug = str(row.get("slug") or "").strip()
                        if candidate_slug:
                            detail["slug"] = candidate_slug
                            slug = candidate_slug
                    break

        if (title or slug) and not force_official_check:
            detail["blacklist_metadata_verified"] = bool(
                detail.get("blacklist_metadata_verified", True)
            )
            return

        market, source = self._fetch_market_metadata_from_gamma_by_token_id(token_id)
        if not isinstance(market, dict):
            detail["blacklist_metadata_verified"] = False
            detail["blacklist_metadata_source"] = source
            detail["blacklist_metadata_unverified_reason"] = source
            print(
                "[BLACKLIST][META] official metadata fetch failed "
                f"token={token_id[:20]}... source={source}"
            )
            return

        official_title = str(market.get("question") or market.get("title") or "").strip()
        official_slug = str(market.get("slug") or "").strip()
        if title and official_title and title != official_title:
            print(
                "[BLACKLIST][META] title mismatch corrected "
                f"token={token_id[:20]}... local={title[:60]} official={official_title[:60]}"
            )
        if slug and official_slug and slug != official_slug:
            print(
                "[BLACKLIST][META] slug mismatch corrected "
                f"token={token_id[:20]}... local={slug} official={official_slug}"
            )
        if official_title:
            detail["title"] = official_title
        if official_slug:
            detail["slug"] = official_slug
        detail["blacklist_metadata_verified"] = bool(official_title or official_slug)
        detail["blacklist_metadata_source"] = f"gamma:{source}"
        detail.pop("blacklist_metadata_unverified_reason", None)
        if not detail["blacklist_metadata_verified"]:
            detail["blacklist_metadata_unverified_reason"] = "official_empty_title_and_slug"

    def _apply_metadata_unverified_guard(self, token_id: str, *, source: str) -> str:
        detail = self.topic_details.setdefault(token_id, {})
        if bool(detail.get("blacklist_metadata_verified", True)):
            return "verified"
        reason = str(detail.get("blacklist_metadata_unverified_reason") or "unknown")
        has_position = self._has_account_position(token_id)
        if not has_position:
            self._remove_pending_topic(token_id)
            self._remove_pending_exit_topic(token_id)
            self._append_exit_token_record(
                token_id,
                "TITLE_BLACKLIST_METADATA_UNVERIFIED_NO_POSITION",
                exit_data={
                    "source": source,
                    "reason": reason,
                    "has_position": False,
                },
                refillable=False,
            )
            self._update_handled_topics([token_id])
            print(
                "[BLACKLIST][META] metadata unverified, blocked start(no position) "
                f"token={token_id[:20]}... source={source} reason={reason}"
            )
            return "blocked_no_position"
        detail["force_sell_only_on_startup"] = True
        detail["force_sell_only_reason"] = "metadata unverified"
        detail["queue_role"] = "title_blacklist_sell_only"
        detail["schedule_lane"] = "base"
        print(
            "[BLACKLIST][META] metadata unverified, force sell_only guard "
            f"token={token_id[:20]}... source={source} reason={reason}"
        )
        return "force_sell_only"

    def _start_topic_process(self, topic_id: str) -> bool:
        self._hydrate_topic_metadata_for_blacklist(topic_id, force_official_check=True)
        metadata_guard = self._apply_metadata_unverified_guard(
            topic_id, source="before_start"
        )
        if metadata_guard == "blocked_no_position":
            return False
        blacklist_action = self._enforce_title_blacklist_policy(
            topic_id, source="before_start"
        )
        if blacklist_action in {"blocked_no_position", "force_liquidate"}:
            return False

        running = sum(1 for t in self.tasks.values() if t.is_running())
        max_total = self._max_total_task_slots()
        if running >= max_total:
            print(
                f"[SCHED][CAP] 拒绝启动 topic={topic_id[:16]}... "
                f"running={running}/{max_total}"
            )
            return False

        if self._is_sell_cleanup_in_flight(topic_id):
            print(
                f"[SCHED][GUARD] topic={topic_id[:16]}... 清仓进行中，跳过普通启动"
            )
            return False
        if self._block_topic_start_for_active_sell(topic_id, source="before_start"):
            return False

        restore_state = self._reconcile_position_restore_before_start(topic_id)
        if restore_state == "dropped_restored_token":
            return False

        config_data = self._build_run_config(topic_id)
        if not self._apply_token_cycle_buy_gate_and_drop_override(topic_id, config_data):
            return False
        cfg_path = self.config.data_dir / f"run_params_{_safe_topic_filename(topic_id)}.json"
        _dump_json_file(cfg_path, config_data)

        log_path = self.config.log_dir / f"autorun_{_safe_topic_filename(topic_id)}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_file = log_path.open("a", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 文件系统异常
            print(f"[ERROR] 无法创建日志文件 {log_path}: {exc}")
            return False

        max_stagger = max(0.0, float(self.config.process_stagger_max_sec))
        if max_stagger > 0:
            delay = random.uniform(0, max_stagger)
            if delay > 0:
                print(
                    f"[SCHEDULE] topic={topic_id} 启动前随机延迟 {delay:.2f}s 以错峰运行"
                )
                time.sleep(delay)

        # 构建命令行参数（不再使用环境变量）
        cmd = [
            sys.executable,
            str(MAKER_ROOT / "Volatility_arbitrage_run.py"),
            str(cfg_path),
        ]

        # 基于缓存新鲜度判断是否使用共享 WS 模式
        # 启用调试输出（首次子进程启动时）
        if not hasattr(self, '_first_child_started'):
            self._debug_shared_ws_check = True
            self._first_child_started = True

        with self._ws_cache_lock:
            cached = self._ws_cache.get(topic_id)
        if not cached:
            print(
                f"[WS][WAIT] topic={topic_id[:8]}... 缓存未就绪，启动跳过（等待调度重试）"
            )
            return False

        should_use_shared = True

        # 禁用调试输出（避免刷屏）
        if hasattr(self, '_debug_shared_ws_check'):
            delattr(self, '_debug_shared_ws_check')

        if should_use_shared:
            # 通过命令行参数传递共享缓存路径
            cmd.append(f"--shared-ws-cache={self._ws_cache_path}")
            print(f"[WS][CHILD] topic={topic_id[:8]}... → 共享 WS 模式 ✓")

        proc: Optional[subprocess.Popen] = None
        attempts = max(1, int(self.config.process_start_retries))
        env = os.environ.copy()
        for attempt in range(1, attempts + 1):
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )
                log_file.close()
                break
            except Exception as exc:  # pragma: no cover - 子进程异常
                print(
                    f"[ERROR] 启动 topic={topic_id} 失败（尝试 {attempt}/{attempts}）: {exc}"
                )
                if attempt >= attempts:
                    log_file.close()
                    return False
                time.sleep(self.config.process_retry_delay_sec)

        if not proc:
            print(f"[ERROR] topic={topic_id} 启动失败：proc is None")
            return False

        # 启动后给子进程一个极短的引导窗口，避免“刚启动即退出”被误判为运行中。
        bootstrap_wait = min(1.0, max(0.0, float(self.config.command_poll_sec)))
        if bootstrap_wait > 0:
            time.sleep(bootstrap_wait)

        early_rc = proc.poll()
        if early_rc is not None:
            early_tail = ""
            try:
                if log_path.exists():
                    with log_path.open("rb") as lf:
                        lf.seek(0, os.SEEK_END)
                        size = lf.tell()
                        lf.seek(max(0, size - 1500), os.SEEK_SET)
                        early_tail = lf.read().decode("utf-8", errors="ignore").strip()
            except Exception:
                early_tail = ""
            tail_text = (early_tail.splitlines() or ["-"])[-1].strip() if early_tail else "-"
            print(
                f"[ERROR] topic={topic_id} 启动后立即退出 rc={early_rc} tail={tail_text}，将重试"
            )
            return False

        task = self.tasks.get(topic_id) or TopicTask(topic_id=topic_id)
        task.process = proc
        task.config_path = cfg_path
        task.log_path = log_path
        # 重新成功启动后，清除历史 no_restart 标记，避免后续异常退出无法自恢复
        task.no_restart = False
        task.status = "running"
        task.heartbeat("started")
        self.tasks[topic_id] = task
        self._update_handled_topics([topic_id])
        # 启动成功后，从回填标记中移除（允许后续再次回填）
        self._refilled_tokens.discard(topic_id)
        # 从 sell 清仓状态中移除，确保新一轮交易的 sell 信号能被正常处理
        self._completed_exit_cleanup_tokens.discard(topic_id)
        self._handled_sell_signals.discard(topic_id)
        self._exit_cleanup_retry_counts.pop(topic_id, None)
        self._sell_exit_deadlines.pop(topic_id, None)
        self._seen_self_sell_tokens.discard(topic_id)
        self._clear_active_unmanaged_rearm_block(topic_id)
        # refill retry count 属于“同一笔未闭合仓位恢复链”的状态。
        # 这里不再按启动来源猜测是否清零，避免 startup_reconcile_position
        # 等路径把同一生命周期误判成新一轮并重置 1/6..6/6 计数。
        detail = self.topic_details.get(topic_id) or {}
        cycle_mode = "started_not_bought"
        resume_state = detail.get("resume_state")
        if bool(config_data.get("exit_only")):
            cycle_mode = "sell_pending"
        elif isinstance(resume_state, dict) and resume_state.get("has_position"):
            cycle_mode = "position_resume"
        self._mark_token_cycle_local_start(topic_id, mode=cycle_mode)
        # 清理 topic_details 中的 resume_state（已被使用）
        if topic_id in self.topic_details:
            self.topic_details[topic_id].pop("resume_state", None)
            self.topic_details[topic_id].pop("refill_retry_count", None)
            self.topic_details[topic_id].pop("sell_exit_requested_at", None)
            self.topic_details[topic_id].pop("sell_exit_deadline_at", None)
            self.topic_details[topic_id].pop("sell_exit_phase", None)
        self._clear_reentry_eligible_token(topic_id, reason="maker_started")
        print(f"[START] topic={topic_id} pid={proc.pid} log={log_path}")
        return True

    def _is_sell_cleanup_in_flight(self, token_id: str) -> bool:
        if not token_id:
            return False
        if token_id in self.pending_exit_topics:
            return True
        task = self.tasks.get(token_id)
        if task and task.is_running() and task.end_reason == "sell signal cleanup":
            return True
        detail = self.topic_details.get(token_id) or {}
        sell_phase = str(detail.get("sell_exit_phase") or "").strip().lower()
        return sell_phase == "forced_cleanup"

    def _ensure_resume_state_from_live_position(self, token_id: str) -> None:
        if not token_id:
            return
        detail = self.topic_details.setdefault(token_id, {})
        queue_role = str(detail.get("queue_role") or "").strip().lower()
        has_existing_resume = isinstance(detail.get("resume_state"), dict)
        should_probe = queue_role in {
            "restored_token",
            "startup_reconcile_position",
            "refill_with_position",
        } or has_existing_resume
        if not should_probe:
            return

        rows, snapshot, info, source = self._refresh_unified_position_snapshot(
            force_refresh=False
        )
        if info != "ok":
            print(
                "[RESUME][SKIP] 持仓快照不可用，保持原配置: "
                f"token={token_id[:16]}... info={info} source={source}"
            )
            return

        row = self._find_position_row(rows, token_id)
        size = float(snapshot.get(token_id, 0.0) or 0.0)
        if not self._has_actionable_position(token_id, size, row=row):
            return

        entry_price = _extract_position_avg_price(row or {})
        if entry_price is None or entry_price <= 0:
            state = self._stoploss_reentry_states.get(token_id) or {}
            for candidate in (
                state.get("old_last_buy_price"),
                state.get("old_entry_price"),
                detail.get("entry_price"),
                detail.get("last_buy_price"),
            ):
                val = _coerce_float(candidate)
                if val is not None and val > 0:
                    entry_price = float(val)
                    break

        resume_state = {
            "has_position": True,
            "position_size": float(size),
            "entry_price": float(entry_price) if entry_price and entry_price > 0 else None,
            "skip_buy": True,
        }
        detail["resume_state"] = resume_state
        detail["startup_skip_if_open_sell"] = True
        print(
            "[RESUME] 检测到已有持仓，强制跳过买入: "
            f"token={token_id[:16]}... size={size:.4f}"
        )

    def _reconcile_position_restore_before_start(self, token_id: str) -> str:
        if not token_id:
            return "unchanged"
        detail = self.topic_details.setdefault(token_id, {})
        queue_role = str(detail.get("queue_role") or "").strip().lower()
        resume_state = detail.get("resume_state") if isinstance(detail.get("resume_state"), dict) else {}
        expects_position = queue_role in {
            "restored_token",
            "startup_reconcile_position",
            "refill_with_position",
        } or bool(resume_state.get("has_position"))
        if not expects_position:
            return "unchanged"

        _rows, snapshot, info, source = self._refresh_unified_position_snapshot(force_refresh=True)
        if info != "ok":
            print(
                "[RESUME][SKIP] 启动前持仓快照不可用，保持原恢复配置: "
                f"token={token_id[:16]}... info={info} source={source}"
            )
            return "position_snapshot_unavailable"

        has_live_position = self._has_actionable_position(
            token_id,
            snapshot.get(token_id, 0.0),
        )
        if has_live_position:
            self._ensure_resume_state_from_live_position(token_id)
            return "position_confirmed"

        stale_resume = isinstance(resume_state, dict) and bool(resume_state.get("has_position"))
        if stale_resume:
            detail.pop("resume_state", None)
        for stale_key in ("entry_price", "last_buy_price", "floor_price", "sell_trigger_price"):
            detail.pop(stale_key, None)

        if queue_role == "startup_reconcile_position":
            detail["queue_role"] = "startup_reconcile_buy"
            print(
                "[RESUME][DOWNGRADE] 启动前未检测到真实持仓，降级为 startup_reconcile_buy: "
                f"token={token_id[:16]}..."
            )
            return "downgraded_to_buy"

        if queue_role == "refill_with_position":
            detail["queue_role"] = "refill_buy"
            print(
                "[RESUME][DOWNGRADE] 启动前未检测到真实持仓，降级为 refill_buy: "
                f"token={token_id[:16]}..."
            )
            return "downgraded_to_refill_buy"

        if queue_role == "restored_token":
            self._append_exit_token_record(
                token_id,
                "RESTORE_RUNTIME_NO_POSITION",
                exit_data={
                    "source": "restore_runtime",
                    "queue_role": queue_role,
                    "stale_resume_state": stale_resume,
                },
                refillable=False,
            )
            self._remove_pending_topic(token_id)
            task = self.tasks.get(token_id)
            if task is not None and not task.is_running():
                self.tasks.pop(token_id, None)
            self._purge_token_runtime_state(token_id)
            self._remove_from_handled_topics(token_id)
            print(
                "[RESUME][DROP] 启动前未检测到真实持仓，丢弃历史恢复 token: "
                f"token={token_id[:16]}..."
            )
            return "dropped_restored_token"

        return "cleared_stale_resume"

    def _start_exit_cleanup(self, token_id: str) -> None:
        task = self.tasks.get(token_id)
        if task and task.is_running():
            return
        self._sell_exit_deadlines.pop(token_id, None)
        detail = self.topic_details.setdefault(token_id, {})
        exit_reason = self._normalize_exit_reason(detail.get("sell_exit_reason"))
        if not self._is_ioc_exit_reason_allowed(exit_reason):
            self._mark_token_orphaned(
                token_id,
                reason=f"EXIT_ONLY_BLOCKED_{exit_reason or 'UNSPECIFIED'}",
                trigger_source=str(detail.get("sell_trigger_source") or "start_exit_cleanup"),
                task=task,
                note="exit_only cleanup blocked by IOC reason whitelist",
            )
            return
        detail.pop("sell_exit_requested_at", None)
        detail.pop("sell_exit_deadline_at", None)
        detail["sell_exit_phase"] = "forced_cleanup"
        if token_id in self.pending_topics or token_id in self.pending_burst_topics:
            self._remove_pending_topic(token_id)
        if token_id in self.pending_exit_topics:
            try:
                self.pending_exit_topics.remove(token_id)
            except ValueError:
                pass

        config_data = self._build_run_config(token_id)
        config_data["exit_only"] = True
        config_data["token_id"] = config_data.get("token_id") or token_id
        config_data["exit_cleanup_reason"] = exit_reason
        config_data["exit_cleanup_source"] = str(detail.get("sell_trigger_source") or "")
        config_data["allow_ioc_exit_reasons"] = sorted(ALLOWED_IOC_EXIT_REASONS)
        cfg_path = self.config.data_dir / f"run_params_{_safe_topic_filename(token_id)}.json"
        _dump_json_file(cfg_path, config_data)

        log_path = self.config.log_dir / f"autorun_exit_{_safe_topic_filename(token_id)}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            log_file = log_path.open("a", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 文件系统异常
            print(f"[ERROR] 无法创建清仓日志文件 {log_path}: {exc}")
            self._append_exit_token_record(
                token_id,
                "EXIT_CLEANUP_FAILED",
                exit_data={
                    "source": "exit_only_start",
                    "error": f"log_open_failed: {exc}",
                },
                refillable=False,
            )
            return

        self._append_exit_token_record(
            token_id,
            "EXIT_CLEANUP_STARTED",
            exit_data={
                "source": "exit_only_start",
                "config_path": str(cfg_path),
                "log_path": str(log_path),
            },
            refillable=False,
        )

        # 构建命令行参数（不再使用环境变量）
        cmd = [
            sys.executable,
            str(MAKER_ROOT / "Volatility_arbitrage_run.py"),
            str(cfg_path),
        ]

        # 清仓进程固定使用共享缓存路径（不启用独立 WS 模式）
        cmd.append(f"--shared-ws-cache={self._ws_cache_path}")
        print(f"[WS] 清仓进程将使用共享 WS 模式")

        env = os.environ.copy()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        except Exception as exc:  # pragma: no cover - 子进程异常
            log_file.close()
            print(f"[ERROR] 启动清仓进程失败 token={token_id}: {exc}")
            self._append_exit_token_record(
                token_id,
                "EXIT_CLEANUP_FAILED",
                exit_data={
                    "source": "exit_only_start",
                    "error": f"popen_failed: {exc}",
                },
                refillable=False,
            )
            return
        log_file.close()

        task = task or TopicTask(topic_id=token_id)
        task.process = proc
        task.config_path = cfg_path
        task.log_path = log_path
        task.status = "running"
        task.no_restart = True
        task.end_reason = "sell signal cleanup"
        task.heartbeat("sell signal cleanup started")
        self.tasks[token_id] = task
        self._update_handled_topics([token_id])
        print(f"[EXIT-CLEAN] token={token_id} pid={proc.pid} log={log_path}")

    # ========== 历史记录 ==========
    def _load_handled_topics(self) -> None:
        self.handled_topics = read_handled_topics(self.config.handled_topics_path)
        if self.handled_topics:
            preview = ", ".join(sorted(self.handled_topics)[:5])
            print(
                f"[INIT] 已加载历史话题 {len(self.handled_topics)} 个 preview={preview}"
            )
        else:
            print("[INIT] 尚无历史处理话题记录")

    def _sync_handled_topics_on_startup(self) -> None:
        """启动全量对账：SELL 优先，其次按持仓分流，最后清理无持仓残留。"""
        tokens_path = self.config.copytrade_tokens_path
        signals_path = self.config.copytrade_sell_signals_path
        if not tokens_path.exists() and not signals_path.exists():
            active_tasks = (
                set(self.tasks.keys())
                | set(self.pending_topics)
                | set(self.pending_burst_topics)
                | set(self.pending_exit_topics)
            )
            self._schedule_startup_orphan_profit_sweep(
                copytrade_topics=set(),
                active_tasks=active_tasks,
            )
            self._startup_sync_retry_needed = False
            self._next_startup_sync_retry_at = 0.0
            return

        try:
            copytrade_entries = self._load_copytrade_tokens()
            copytrade_topics = {
                topic_id
                for topic_id in (
                    _topic_id_from_entry(item)
                    for item in copytrade_entries
                )
                if topic_id
            }
            copytrade_entry_map: Dict[str, Dict[str, Any]] = {}
            for item in copytrade_entries:
                token_id = _topic_id_from_entry(item)
                if token_id and isinstance(item, dict):
                    copytrade_entry_map[token_id] = item
            sell_signals = self._load_copytrade_sell_signals()
        except Exception as exc:
            print(f"[HANDLED][WARN] startup sync skipped due to copytrade read error: {exc}")
            self._startup_sync_retry_needed = True
            self._next_startup_sync_retry_at = time.time() + max(
                30.0, float(self.config.copytrade_poll_sec)
            )
            return
        copytrade_topics.update(sell_signals.keys())

        active_tasks = (
            set(self.tasks.keys())
            | set(self.pending_topics)
            | set(self.pending_burst_topics)
            | set(self.pending_exit_topics)
        )
        to_reconcile = copytrade_topics - active_tasks
        if not to_reconcile:
            self._schedule_startup_orphan_profit_sweep(
                copytrade_topics=copytrade_topics,
                active_tasks=active_tasks,
            )
            self._startup_sync_retry_needed = False
            self._next_startup_sync_retry_at = 0.0
            return

        pos_snapshot, pos_info = self._refresh_sell_position_snapshot()
        if pos_info != "ok":
            print(
                "[HANDLED][WARN] startup position snapshot unavailable, defer full reconcile: "
                f"info={pos_info} count={len(to_reconcile)}"
            )
            self._startup_sync_retry_needed = True
            self._next_startup_sync_retry_at = time.time() + max(
                30.0, float(self.config.copytrade_poll_sec)
            )
            return

        tokens_with_sell_signal = set(sell_signals.keys())
        to_liquidate: set[str] = set()
        to_reactivate: set[str] = set()
        to_queue_buy: set[str] = set()
        to_cleanup: set[str] = set()
        owner_retained: set[str] = set()
        orphan_states = self._load_latest_orphan_states()

        for token_id in sorted(to_reconcile):
            if self._has_runtime_owner_blocking_startup_reconcile(
                token_id,
                orphan_states=orphan_states,
            ):
                owner_retained.add(token_id)
                continue
            pos_size = float(pos_snapshot.get(token_id, 0.0) or 0.0)
            has_position = self._has_actionable_position(token_id, pos_size)
            has_sell_signal = token_id in tokens_with_sell_signal
            if has_sell_signal and has_position:
                to_liquidate.add(token_id)
            elif has_sell_signal:
                to_cleanup.add(token_id)
            elif has_position:
                to_reactivate.add(token_id)
            else:
                to_queue_buy.add(token_id)

        for token_id in sorted(to_liquidate):
            self._mark_token_cycle_local_start(token_id, mode="position_resume")
            self._trigger_sell_exit(
                token_id,
                None,
                trigger_source="startup_reconcile_sell_signal",
                trigger_reason="COPYTRADE_SELL",
                signal_entry=sell_signals.get(token_id),
            )

        orphan_stats = self._schedule_startup_orphan_profit_sweep(
            copytrade_topics=copytrade_topics,
            active_tasks=active_tasks | to_reactivate | to_liquidate | owner_retained,
        )

        for token_id in sorted(to_reactivate):
            if token_id in self.pending_burst_topics:
                self._remove_pending_topic(token_id)
            if token_id not in self.pending_topics:
                detail = self.topic_details.setdefault(token_id, {})
                copytrade_detail = copytrade_entry_map.get(token_id) or {}
                if copytrade_detail.get("title") and not detail.get("title"):
                    detail["title"] = copytrade_detail.get("title")
                if copytrade_detail.get("slug") and not detail.get("slug"):
                    detail["slug"] = copytrade_detail.get("slug")

                self._hydrate_topic_metadata_for_blacklist(token_id)
                title_policy = self._enforce_title_blacklist_policy(
                    token_id, source="startup_reconcile"
                )
                if title_policy in {
                    "blocked_no_position",
                    "force_liquidate",
                    "force_sell_only",
                }:
                    continue

                detail["queue_role"] = "startup_reconcile_position"
                detail["schedule_lane"] = "base"
                self._enqueue_pending_topic(token_id)

        for token_id in sorted(to_queue_buy):
            if token_id in self.pending_burst_topics:
                self._remove_pending_topic(token_id)
            if token_id not in self.pending_topics:
                detail = self.topic_details.setdefault(token_id, {})
                copytrade_detail = copytrade_entry_map.get(token_id) or {}
                if copytrade_detail.get("title") and not detail.get("title"):
                    detail["title"] = copytrade_detail.get("title")
                if copytrade_detail.get("slug") and not detail.get("slug"):
                    detail["slug"] = copytrade_detail.get("slug")

                self._hydrate_topic_metadata_for_blacklist(token_id)
                title_policy = self._enforce_title_blacklist_policy(
                    token_id, source="startup_reconcile"
                )
                if title_policy in {
                    "blocked_no_position",
                    "force_liquidate",
                    "force_sell_only",
                }:
                    continue

                detail["queue_role"] = "startup_reconcile_buy"
                detail["schedule_lane"] = "base"
                self._enqueue_pending_topic(token_id)

        for token_id in sorted(to_cleanup):
            self._remove_token_from_copytrade_files(token_id)
            self._purge_token_runtime_state(token_id)
            self._remove_from_handled_topics(token_id)

        keep_handled = to_reactivate | to_liquidate | to_queue_buy | owner_retained
        stale_topics = self.handled_topics - copytrade_topics - active_tasks
        changed = False
        if keep_handled:
            before = len(self.handled_topics)
            self.handled_topics.update(keep_handled)
            changed = changed or (len(self.handled_topics) != before)
        if stale_topics:
            self.handled_topics -= stale_topics
            changed = True
        if changed:
            write_handled_topics(self.config.handled_topics_path, self.handled_topics)

        self._startup_sync_retry_needed = False
        self._next_startup_sync_retry_at = 0.0
        print(
            "[HANDLED] 启动全量对账完成: "
            f"total={len(self.handled_topics)} "
            f"reconciled={len(to_reconcile)} "
            f"reactivated={len(to_reactivate)} "
            f"queued_buy={len(to_queue_buy)} "
            f"liquidating={len(to_liquidate)} "
            f"owner_retained={len(owner_retained)} "
            f"cleaned={len(to_cleanup)} "
            f"stale_removed={len(stale_topics)} "
            f"orphan_candidates={orphan_stats.get('candidates', 0)} "
            f"orphan_queued={orphan_stats.get('queued', 0)}"
        )

    def _build_copytrade_active_token_set(self) -> set[str]:
        active: set[str] = set()
        for item in self._load_copytrade_tokens():
            token_id = _topic_id_from_entry(item)
            if token_id:
                active.add(token_id)
        active.update(self._load_copytrade_sell_signals().keys())
        return active

    def _schedule_startup_orphan_profit_sweep(
        self,
        *,
        copytrade_topics: set[str],
        active_tasks: set[str],
    ) -> Dict[str, int]:
        stats = {
            "candidates": 0,
            "queued": 0,
            "skipped_active": 0,
            "skipped_copytrade": 0,
            "skipped_profit": 0,
            "skipped_invalid_price": 0,
        }
        if self._startup_orphan_profit_sweep_done:
            return stats
        if not bool(self.config.startup_orphan_profit_sweep_enabled):
            self._startup_orphan_profit_sweep_done = True
            return stats
        if not self._position_address:
            address, origin = _resolve_position_address_from_env()
            self._position_address = address
            self._position_address_origin = origin
        if not self._position_address:
            return stats

        min_profit = self._normalize_ratio_value(
            self.config.startup_orphan_profit_sweep_min_profit_pct
        )
        if min_profit is None:
            min_profit = 0.01
        min_profit = max(0.0, float(min_profit))

        rows, info = _fetch_position_rows_from_data_api(self._position_address)
        if info != "ok":
            print(f"[ORPHAN][WARN] startup profit sweep skipped: {info}")
            return stats

        tracked = (
            set(active_tasks)
            | set(self.tasks.keys())
            | set(self.pending_topics)
            | set(self.pending_burst_topics)
            | set(self.pending_exit_topics)
        )
        for row in rows:
            token_id = _extract_position_token_id(row)
            if not token_id:
                continue
            size = float(_extract_position_size(row) or 0.0)
            if not self._has_actionable_position(token_id, size, row=row):
                continue
            stats["candidates"] += 1
            if token_id in copytrade_topics:
                stats["skipped_copytrade"] += 1
                continue
            if token_id in tracked:
                stats["skipped_active"] += 1
                continue
            avg_price = _extract_position_avg_price(row)
            cur_price = _extract_position_current_price(row)
            if avg_price is None or cur_price is None or avg_price <= 0:
                stats["skipped_invalid_price"] += 1
                continue
            profit_ratio = (float(cur_price) / float(avg_price)) - 1.0
            if profit_ratio + 1e-12 < min_profit:
                stats["skipped_profit"] += 1
                continue

            detail = self.topic_details.setdefault(token_id, {})
            title = row.get("title")
            slug = row.get("slug")
            if title and not detail.get("title"):
                detail["title"] = title
            if slug and not detail.get("slug"):
                detail["slug"] = slug
            detail["queue_role"] = "startup_orphan_profit_sweep"
            detail["schedule_lane"] = "base"
            detail["force_sell_only_on_startup"] = True
            detail["force_sell_only_reason"] = "startup orphan profit sweep"
            detail["startup_skip_if_open_sell"] = bool(
                self.config.startup_orphan_profit_sweep_skip_if_open_sell
            )
            detail["startup_orphan_profit_sweep"] = True
            detail["startup_orphan_profit_ratio"] = round(profit_ratio, 6)
            self._enqueue_pending_topic(token_id)
            tracked.add(token_id)
            stats["queued"] += 1

        self._startup_orphan_profit_sweep_done = True
        if stats["queued"] > 0:
            print(
                "[ORPHAN] startup profit sweep queued "
                f"{stats['queued']} tokens (min_profit={min_profit * 100:.2f}%)"
            )
        return stats

    def _reconcile_inactive_copytrade_tokens(self) -> None:
        """运行时查漏：本地活跃但不在 copytrade 的 token 立即分流处理。"""
        local_active = (
            set(self.tasks.keys())
            | set(self.pending_topics)
            | set(self.pending_burst_topics)
        )
        if not local_active:
            return
        try:
            copytrade_active = self._build_copytrade_active_token_set()
        except Exception as exc:
            print(f"[RECONCILE][WARN] 运行时对账跳过，读取 copytrade 失败: {exc}")
            return

        leak_tokens = sorted(local_active - copytrade_active)
        if not leak_tokens:
            return

        pos_snapshot, pos_info = self._refresh_sell_position_snapshot()
        forced_exit = 0
        purged = 0
        skipped = 0

        for token_id in leak_tokens:
            detail = self.topic_details.get(token_id) or {}
            if bool(detail.get("startup_orphan_profit_sweep")):
                skipped += 1
                continue
            task = self.tasks.get(token_id)
            if token_id in self.pending_exit_topics:
                skipped += 1
                continue
            if task and task.end_reason == "sell signal cleanup":
                skipped += 1
                continue

            has_position = False
            if pos_info == "ok":
                pos_size = float(pos_snapshot.get(token_id, 0.0) or 0.0)
                has_position = self._has_actionable_position(token_id, pos_size)

            if has_position or pos_info != "ok":
                self._remove_pending_topic(token_id)
                self._mark_token_orphaned(
                    token_id,
                    reason="INACTIVE_COPYTRADE_LEAK",
                    trigger_source="inactive_copytrade_reconcile",
                    task=task,
                    note=f"pos_info={pos_info}",
                    position_snapshot={
                        "size": float(pos_snapshot.get(token_id, 0.0) or 0.0),
                    },
                )
                forced_exit += 1
                continue

            self._remove_pending_topic(token_id)
            if task and task.is_running():
                task.no_restart = True
                task.end_reason = "inactive copytrade purge"
                task.heartbeat("inactive copytrade token purged")
                self._terminate_task(task, reason="inactive copytrade purge")
            self.tasks.pop(token_id, None)
            self._purge_token_runtime_state(token_id)
            self._remove_from_handled_topics(token_id)
            purged += 1

        print(
            "[RECONCILE] 运行时查漏完成: "
            f"candidates={len(leak_tokens)} "
            f"force_exit={forced_exit} purge={purged} skip={skipped} "
            f"position_info={pos_info}"
        )

    def _update_handled_topics(self, new_topics: List[str]) -> None:
        if not new_topics:
            return
        self.handled_topics.update(new_topics)
        write_handled_topics(self.config.handled_topics_path, self.handled_topics)

    def _remove_from_handled_topics(self, token_id: str) -> None:
        """
        从 handled_topics 中移除指定 token，允许后续重新交易。

        用于：当 token 完成一个完整的交易周期（买入→卖出清仓）后，
        从 handled_topics 中移除，这样 copytrade 再次发出买入信号时可以重新触发交易。
        """
        if not token_id:
            return
        if token_id in self.handled_topics:
            self.handled_topics.discard(token_id)
            write_handled_topics(self.config.handled_topics_path, self.handled_topics)
            print(f"[HANDLED] 已从 handled_topics 移除 token={token_id[:16]}...（允许后续重新交易）")
        self._active_unmanaged_rearm_last_ts.pop(token_id, None)
        self._active_unmanaged_rearm_recent_ts.pop(token_id, None)
        self._active_unmanaged_rearm_suppressed_until.pop(token_id, None)

    def _purge_token_runtime_state(self, token_id: str) -> None:
        if not token_id:
            return
        self.topic_details.pop(token_id, None)
        self._pending_first_seen.pop(token_id, None)
        self._shared_ws_wait_failures.pop(token_id, None)
        self._shared_ws_wait_timeout_events.pop(token_id, None)
        self._shared_ws_paused_until.pop(token_id, None)
        self._shared_ws_pending_since.pop(token_id, None)
        self._clob_book_probe_cache.pop(token_id, None)
        self._position_snapshot_cache.pop(token_id, None)
        self._active_unmanaged_rearm_last_ts.pop(token_id, None)
        self._active_unmanaged_rearm_recent_ts.pop(token_id, None)
        self._active_unmanaged_rearm_suppressed_until.pop(token_id, None)
        self._clear_active_unmanaged_rearm_block(token_id)
        self._refilled_tokens.discard(token_id)
        self._refill_retry_counts.pop(token_id, None)
        self._completed_exit_cleanup_tokens.discard(token_id)
        self._exit_cleanup_retry_counts.pop(token_id, None)
        self._handled_sell_signals.discard(token_id)
        self._seen_self_sell_tokens.discard(token_id)
        self._clear_reentry_eligible_token(token_id, reason="runtime_purge")
        with self._ws_cache_lock:
            self._ws_cache.pop(token_id, None)

    # ========== 命令处理 ==========
    def enqueue_command(self, command: str) -> None:
        self.command_queue.put(command)

    def _process_commands(self) -> None:
        while True:
            try:
                cmd = self.command_queue.get_nowait()
            except queue.Empty:
                break
            print(f"[CMD] processing: {cmd}")
            self._handle_command(cmd.strip())

    def _handle_command(self, cmd: str) -> None:
        if not cmd:
            print("[CMD] 忽略空命令（可能未正确捕获输入或输入仅为空白）")
            return
        if cmd in {"quit", "exit"}:
            print("[CHOICE] exit requested")
            self.stop_event.set()
            return
        if cmd == "list":
            self._print_status()
            return
        if cmd.startswith("stop "):
            _, topic_id = cmd.split(" ", 1)
            self._stop_topic(topic_id.strip())
            return
        if cmd == "refresh":
            self._refresh_topics()
            return
        print(f"[WARN] 未识别命令: {cmd}")

    def _print_status(self) -> None:
        print(
            f"[STATUS] mode={'aggressive' if self._is_aggressive_mode() else 'classic'} "
            f"pending_base={len(self.pending_topics)} "
            f"pending_burst={len(self.pending_burst_topics)} "
            f"pending_exit={len(self.pending_exit_topics)}"
        )
        print(
            "[STATUS] ws_reconnect_reasons="
            f"{self._ws_reconnect_reason_counts} "
            f"last={self._ws_last_reconnect_reason or '-'} "
            f"reentry_eligible={len(self._reentry_eligible_tokens)}"
        )
        if self._ws_last_reconnect_detail:
            detail = self._ws_last_reconnect_detail
            print(
                "[STATUS] ws_last_reconnect_detail="
                f"state={detail.get('state', '-')} "
                f"code={detail.get('status_code', '-')} "
                f"uptime={detail.get('connected_for_sec', '-')}s "
                f"subs={detail.get('subscribed_tokens', '-')} "
                f"msg={str(detail.get('message') or detail.get('error') or '-')[:160]}"
            )
        role_base: Dict[str, int] = {}
        for topic_id in self.pending_topics:
            role = self._queue_role(self.topic_details.get(topic_id) or {})
            role_base[role] = role_base.get(role, 0) + 1
        role_burst: Dict[str, int] = {}
        for topic_id in self.pending_burst_topics:
            role = self._queue_role(self.topic_details.get(topic_id) or {})
            role_burst[role] = role_burst.get(role, 0) + 1
        if role_base or role_burst:
            print(f"[STATUS] pending_base_roles={role_base} pending_burst_roles={role_burst}")
        self._print_cycle_threshold_status()
        if not self.tasks:
            print("[RUN] 当前无运行中的话题")
            return
        running_tasks = self._ordered_running_tasks()
        if not running_tasks:
            print("[RUN] 当前无运行中的话题")
            return

        mode_counts: Dict[str, int] = {}
        for task in running_tasks:
            detail = self.topic_details.get(task.topic_id) or {}
            mode = self._task_runtime_mode(task, detail)
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
        print(f"[STATUS] task_modes={mode_counts}")
        for idx, task in enumerate(running_tasks, 1):
            self._print_single_task(task, idx)

    def _print_cycle_threshold_status(self) -> None:
        active_tokens = (
            set(self.tasks.keys())
            | set(self.pending_topics)
            | set(self.pending_burst_topics)
            | set(self.pending_exit_topics)
        )
        if not active_tokens or not self._token_cycle_states:
            return

        now = time.time()
        rows: List[str] = []
        for token_id in sorted(active_tokens):
            state = self._token_cycle_states.get(token_id)
            if not state:
                continue
            cycle_round = int(state.get("cycle_round", 0) or 0)
            next_buy_ts = float(state.get("next_buy_allowed_ts", 0.0) or 0.0)
            wait_sec = int(max(0.0, next_buy_ts - now)) if next_buy_ts > now else 0
            drop_pct = self._normalize_ratio_value(state.get("next_drop_pct"))
            profit_pct = self._normalize_ratio_value(state.get("next_profit_pct"))
            drop_text = f"{drop_pct * 100:.3f}%" if drop_pct is not None else "-"
            profit_text = f"{profit_pct * 100:.3f}%" if profit_pct is not None else "-"
            rows.append(
                f"token={token_id[:16]}... round={cycle_round} wait={wait_sec}s "
                f"next_drop={drop_text} next_profit={profit_text}"
            )

        if not rows:
            return

        print(
            f"[CYCLE][STATUS] active_with_cycle_state={len(rows)} "
            f"cycle_state_total={len(self._token_cycle_states)}"
        )
        for line in rows[:8]:
            print(f"[CYCLE][STATUS] {line}")

    def _task_runtime_mode(self, task: TopicTask, detail: Dict[str, Any]) -> str:
        role = self._queue_role(detail)
        cfg = self._get_task_run_config(task)
        if bool(cfg.get("exit_only")) or (
            task.log_path and task.log_path.name.startswith("autorun_exit_")
        ):
            return "清仓状态"
        if detail.get("blacklist_keyword") or role.startswith("title_blacklist"):
            return "黑名单"
        low_balance_sell_only = bool(cfg.get("force_sell_only_on_startup")) or role in {
            "refill_with_position",
            "startup_reconcile_position",
            "startup_orphan_profit_sweep",
        }
        if self._buy_paused_due_to_balance and low_balance_sell_only:
            return "余额不足只卖出"
        if (
            str(detail.get("refill_exit_reason") or "").upper() == "LOW_BALANCE_PAUSE"
            and bool(cfg.get("force_sell_only_on_startup"))
        ):
            return "余额不足只卖出"
        return "正常波段"

    def _print_single_task(self, task: TopicTask, index: Optional[int] = None) -> None:
        hb = task.last_heartbeat
        hb_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(hb)) if hb else "-"
        pid_text = str(task.process.pid) if task.process else "-"
        log_name = task.log_path.name if task.log_path else "-"
        log_hint = (task.log_excerpt.splitlines() or ["-"])[-1].strip()

        prefix = f"[RUN {index}]" if index is not None else "[RUN]"
        detail = self.topic_details.get(task.topic_id) or {}
        lane = detail.get("schedule_lane") or "-"
        role = self._queue_role(detail)
        mode = self._task_runtime_mode(task, detail)
        print(
            f"{prefix} topic={task.topic_id} status={task.status} "
            f"lane={lane} role={role} mode={mode} "
            f"start={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(task.start_time))} "
            f"pid={pid_text} hb={hb_text} notes={len(task.notes)} "
            f"log={log_name} last_line={log_hint or '-'}"
        )

    def _ordered_running_tasks(self) -> List[TopicTask]:
        return sorted(
            [task for task in self.tasks.values() if task.is_running()],
            key=lambda t: (t.start_time, t.topic_id),
        )

    def _stop_topic(self, topic_or_index: str) -> None:
        topic_id = self._resolve_topic_identifier(topic_or_index)
        if not topic_id:
            return
        task = self.tasks.get(topic_id)
        if not task:
            print(f"[WARN] topic {topic_id} 不在运行列表中")
            return
        task.no_restart = True
        task.end_reason = "stopped by user"
        # 标记为已处理，避免后续 refresh 把同一话题再次入队
        if topic_id not in self.handled_topics:
            self.handled_topics.add(topic_id)
            write_handled_topics(self.config.handled_topics_path, self.handled_topics)
        if topic_id in self.pending_topics:
            self._remove_pending_topic(topic_id)
        self._terminate_task(task, reason="stopped by user")
        self._purge_inactive_tasks()
        print(f"[CHOICE] stop topic={topic_id}")

    def _resolve_topic_identifier(self, text: str) -> Optional[str]:
        text = text.strip()
        if not text:
            print("[WARN] stop 命令缺少参数")
            return None
        if text.isdigit():
            index = int(text)
            running_tasks = self._ordered_running_tasks()
            if 1 <= index <= len(running_tasks):
                return running_tasks[index - 1].topic_id
            print(
                f"[WARN] 无效的序号 {index}，当前运行中的任务数为 {len(running_tasks)}"
            )
            return None
        return text

    def _terminate_task(self, task: TopicTask, reason: str) -> None:
        proc = task.process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:  # pragma: no cover - 终止异常
                print(f"[WARN] 无法终止 topic {task.topic_id}: {exc}")
            try:
                proc.wait(timeout=self.config.process_graceful_timeout_sec)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception as exc:  # pragma: no cover - kill 失败
                    print(f"[WARN] 无法强杀 topic {task.topic_id}: {exc}")
        if task.status not in {"error", "ended"}:
            task.status = "stopped"
        task.heartbeat(reason)

    def _purge_inactive_tasks(self) -> None:
        """移除已停止/结束且不再需要展示的任务。

        注意：如果 topic 仍在 pending_topics / pending_burst_topics / pending_exit_topics 中等待重新调度，
        则保留 task 对象（避免丢失待重启的 token）。
        """

        removable: List[str] = []
        for topic_id, task in list(self.tasks.items()):
            if task.is_running():
                continue
            if task.status in {"stopped", "ended", "exited", "error"} or task.no_restart:
                # 如果 token 仍在等待队列中，不要 purge（否则会丢失重试机会）
                if (
                    topic_id in self.pending_topics
                    or topic_id in self.pending_burst_topics
                    or topic_id in self.pending_exit_topics
                ):
                    continue
                removable.append(topic_id)

        if not removable:
            return

        for topic_id in removable:
            self.tasks.pop(topic_id, None)

    # ========== 市场关闭时自动清理 token ==========
    def _remove_token_from_copytrade_files(self, token_id: str) -> None:
        """
        将 copytrade JSON 文件中的指定 token 归档，避免重启后再次加入队列。

        会处理以下文件中的记录：
        - tokens_from_copytrade.json
        - copytrade_sell_signals.json
        """
        if not token_id:
            return

        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._file_io_lock:
            tokens_path = self.config.copytrade_tokens_path
            if tokens_path.exists():
                try:
                    with tokens_path.open("r", encoding="utf-8") as f:
                        data = json.load(f)

                    if isinstance(data, dict) and "tokens" in data:
                        archived_tokens = data.get("archived_tokens")
                        if not isinstance(archived_tokens, list):
                            archived_tokens = []
                        remaining_tokens = []
                        invalidated = 0
                        for item in data["tokens"]:
                            if not isinstance(item, dict):
                                remaining_tokens.append(item)
                                continue
                            if _topic_id_from_entry(item) != token_id:
                                remaining_tokens.append(item)
                                continue
                            invalidated += 1
                            item = dict(item)
                            item["active"] = False
                            item["invalidated_at"] = now_iso
                            item["invalidate_reason"] = "cleanup_consumed"
                            item["invalidate_source"] = "_remove_token_from_copytrade_files"
                            item["updated_at"] = now_iso
                            archived_tokens.append(item)
                        if invalidated > 0:
                            data["tokens"] = remaining_tokens
                            data["archived_tokens"] = archived_tokens
                            data["updated_at"] = time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            )
                            _atomic_json_write(tokens_path, data)
                            print(
                                f"[CLEANUP] 已将 tokens_from_copytrade.json 归档 token={token_id[:20]}..."
                            )
                except (json.JSONDecodeError, OSError) as exc:
                    print(f"[CLEANUP] 更新 tokens_from_copytrade.json 失败: {exc}")

            signals_path = self.config.copytrade_sell_signals_path
            if signals_path.exists():
                try:
                    with signals_path.open("r", encoding="utf-8") as f:
                        data = json.load(f)

                    if isinstance(data, dict) and "sell_tokens" in data:
                        archived_tokens = data.get("archived_sell_tokens")
                        if not isinstance(archived_tokens, list):
                            archived_tokens = []
                        remaining_tokens = []
                        invalidated = 0
                        for item in data["sell_tokens"]:
                            if not isinstance(item, dict):
                                remaining_tokens.append(item)
                                continue
                            if _topic_id_from_entry(item) != token_id:
                                remaining_tokens.append(item)
                                continue
                            invalidated += 1
                            item = dict(item)
                            item["active"] = False
                            if str(item.get("status") or "").strip().lower() not in {
                                "done",
                                "stale_ignored",
                                "canceled",
                                "superseded",
                            }:
                                item["status"] = "done"
                            item["consumed_at"] = now_iso
                            item["invalidated_at"] = now_iso
                            item["invalidate_reason"] = "cleanup_consumed"
                            item["invalidate_source"] = "_remove_token_from_copytrade_files"
                            item["updated_at"] = now_iso
                            archived_tokens.append(item)
                        if invalidated > 0:
                            data["sell_tokens"] = remaining_tokens
                            data["archived_sell_tokens"] = archived_tokens
                            data["updated_at"] = time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            )
                            _atomic_json_write(signals_path, data)
                            print(
                                f"[CLEANUP] 已将 copytrade_sell_signals.json 归档 token={token_id[:20]}..."
                            )
                except (json.JSONDecodeError, OSError) as exc:
                    print(f"[CLEANUP] 更新 copytrade_sell_signals.json 失败: {exc}")

        # 3. 从内存队列中移除
        if token_id in self.pending_topics or token_id in self.pending_burst_topics:
            self._remove_pending_topic(token_id)
            print(f"[CLEANUP] 已从 pending_topics 移除 token={token_id[:20]}...")

        if token_id in self.pending_exit_topics:
            try:
                self.pending_exit_topics.remove(token_id)
                print(f"[CLEANUP] 已从 pending_exit_topics 移除 token={token_id[:20]}...")
            except ValueError:
                pass

        # 4. 从 topic_details 中移除
        if token_id in self.topic_details:
            self.topic_details.pop(token_id, None)

        # 5. 从 latest_topics 中移除（latest_topics 是 List[Dict]，需按 token_id 字段过滤）
        original_len = len(self.latest_topics)
        self.latest_topics = [
            entry for entry in self.latest_topics
            if _topic_id_from_entry(entry) != token_id
        ]
        if len(self.latest_topics) < original_len:
            print(f"[CLEANUP] 已从 latest_topics 内存中移除 token={token_id[:20]}...")

    def _is_terminal_exit_reason(self, reason: Any) -> bool:
        return str(reason or "").strip().upper() in {
            "MARKET_CLOSED",
            "MARKET_RESOLVED",
            "MARKET_ARCHIVED",
            "MARKET_NOT_FOUND",
            "MARKET_CLOSED_ON_REFILL",
            "MARKET_TERMINAL_DETECTED",
        }

    def _orphan_state_blocks_refill(self, state: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(state, dict):
            return False
        return self._is_terminal_exit_reason(
            state.get("reason") or state.get("trigger_reason") or ""
        )

    def _invalidate_refill_records_for_token(
        self,
        token_id: str,
        *,
        block_reason: str,
        block_source: str,
    ) -> int:
        if not token_id:
            return 0
        changed = 0
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._file_io_lock:
            records = self._load_exit_tokens()
            if not isinstance(records, list) or not records:
                return 0
            for record in records:
                if str(record.get("token_id") or "").strip() != str(token_id):
                    continue
                if not bool(record.get("refillable", True)):
                    continue
                record["refillable"] = False
                record["refill_block_reason"] = str(block_reason)
                record["refill_block_source"] = str(block_source)
                record["refill_blocked_at"] = now_iso
                changed += 1
            if changed > 0:
                _atomic_json_write(self._exit_tokens_path, records)
        return changed

    # ========== Slot Refill (回填) 逻辑 ==========
    def _load_exit_tokens(self) -> List[Dict[str, Any]]:
        """
        加载退出token记录文件。

        Returns:
            退出token记录列表
        """
        # 【修改】使用文件锁保护读操作
        with self._file_io_lock:
            if not self._exit_tokens_path.exists():
                if self._refill_debug:
                    print(f"[REFILL][DEBUG] exit_tokens.json 不存在: {self._exit_tokens_path}")
                return []
            try:
                with self._exit_tokens_path.open("r", encoding="utf-8") as f:
                    records = json.load(f)
                if not isinstance(records, list):
                    return []
                if self._refill_debug:
                    print(f"[REFILL][DEBUG] 读取 exit_tokens 记录数: {len(records)}")
                return records
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[REFILL] 读取退出token记录失败: {exc}")
                return []

    def _cleanup_closed_market_tokens(self) -> None:
        """
        清理 exit_tokens.json 中因 MARKET_CLOSED 退出的 token。
        从 copytrade JSON 文件中移除这些 token，避免重启后再次加入队列。
        """
        exit_records = self._load_exit_tokens()
        if not exit_records:
            return

        cleaned_tokens: List[str] = []
        latest_topic_ids = {
            _topic_id_from_entry(item)
            for item in self.latest_topics
            if _topic_id_from_entry(item)
        }
        for record in exit_records:
            token_id = record.get("token_id")
            exit_reason = record.get("exit_reason", "")

            if not token_id:
                continue

            # 只处理 MARKET_CLOSED 退出原因
            if exit_reason != "MARKET_CLOSED":
                continue

            # 检查是否仍在 copytrade 文件中（避免重复清理）
            if token_id not in latest_topic_ids:
                continue

            # 从 copytrade 文件中移除
            self._remove_token_from_copytrade_files(token_id)
            cleaned_tokens.append(token_id)

        if cleaned_tokens:
            print(
                f"[CLEANUP] 已清理 {len(cleaned_tokens)} 个市场关闭的 token"
            )

    def _should_refill_slots(self) -> bool:
        """
        判断是否需要回填maker slot。

        条件：
        1. 回填功能已启用
        2. 当前运行的任务数 < max_concurrent_tasks

        Returns:
            True 表示需要回填
        """
        if not self.config.enable_slot_refill:
            if self._refill_debug:
                print("[REFILL][DEBUG] 回填已关闭 enable_slot_refill=False")
            return False

        running = sum(1 for t in self.tasks.values() if t.is_running())
        max_slots = max(1, int(self.config.max_concurrent_tasks))
        pending = len(self.pending_topics) + len(self.pending_burst_topics)
        has_capacity = running < max_slots
        if self._refill_debug:
            print(
                "[REFILL][DEBUG] slot检查: "
                f"running={running} pending={pending} max={max_slots} "
                f"has_capacity={has_capacity}"
            )
        return has_capacity

    def _filter_refillable_tokens(
        self, exit_records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        筛选可回填的token。

        筛选条件：
        1. 退出原因可重试（非 MARKET_CLOSED）
        2. 超过冷却时间
        3. 未超过最大重试次数
        4. 不在当前运行或pending列表中
        5. 未被标记为不可回填

        Args:
            exit_records: 退出token记录列表

        Returns:
            可回填的token记录列表（按优先级排序）
        """
        # 不可重试的退出原因（永久关闭）
        NON_RETRYABLE_REASONS = {
            "MARKET_CLOSED",
            "MARKET_RESOLVED",
            "MARKET_ARCHIVED",
            "MARKET_NOT_FOUND",
            "MARKET_CLOSED_ON_REFILL",
            "MARKET_TERMINAL_DETECTED",
            "USER_STOPPED",
            "DEADLINE_REACHED",
        }

        # 达到重试上限后永久不再回填的退出原因（防止噪声循环）
        PERMANENT_AFTER_MAX_RETRIES_REASONS = {
            "PRICE_NONE_STREAK",
        }
        POSITION_REQUIRED_REASONS = {
            "SELL_ABANDONED",
            "BUY_BLOCK_ENTRY_SYNC_FAILED",
            "BUY_BLOCK_TRIGGER_UNAVAILABLE",
            "TITLE_BLACKLIST_WITH_POSITION",
        }
        now = time.time()
        cooldown_with_position_seconds = (
            self.config.refill_cooldown_minutes_with_position * 60.0
        )
        cooldown_no_position_seconds = (
            self.config.refill_cooldown_minutes_no_position * 60.0
        )
        max_retries = self.config.max_refill_retries

        refillable: List[Dict[str, Any]] = []
        seen_tokens: set[str] = set()  # 避免重复
        skip_stats: Dict[str, int] = {}
        stale_position_downgraded = 0

        # 按退出时间倒序处理（最新的优先，避免处理过期记录）
        sorted_records = sorted(
            exit_records,
            key=lambda r: r.get("exit_ts", 0),
            reverse=True,
        )
        gap_backoff_by_token = self._build_gap_skip_backoff_seconds_by_token(
            exit_records
        )
        orphan_states = self._load_latest_orphan_states()

        for record in sorted_records:
            token_id = record.get("token_id")
            if not token_id:
                skip_stats["missing_token_id"] = skip_stats.get("missing_token_id", 0) + 1
                continue

            # 去重：同一个token只取最新记录
            if token_id in seen_tokens:
                skip_stats["duplicate_token"] = skip_stats.get("duplicate_token", 0) + 1
                continue
            seen_tokens.add(token_id)

            if self._orphan_state_blocks_refill(orphan_states.get(str(token_id))):
                skip_stats["terminal_orphan_block"] = (
                    skip_stats.get("terminal_orphan_block", 0) + 1
                )
                continue

            exit_reason = record.get("exit_reason", "")
            exit_ts = record.get("exit_ts", 0)
            refillable_flag = record.get("refillable", True)

            # 检查是否可重试
            if exit_reason in NON_RETRYABLE_REASONS:
                skip_stats["non_retryable"] = skip_stats.get("non_retryable", 0) + 1
                continue

            # 检查是否标记为不可回填
            if not refillable_flag:
                skip_stats["not_refillable"] = skip_stats.get("not_refillable", 0) + 1
                continue

            stoploss_state = self._stoploss_reentry_states.get(str(token_id)) or {}
            stoploss_state_name = str(stoploss_state.get("state") or "")
            if stoploss_state_name in {
                "STOPLOSS_CLEAR_PENDING_CONFIRM",
                "STOPLOSS_CLEAR_PENDING_ESCALATED",
                "STOPLOSS_EXITED_WAITING_WINDOW",
                "STOPLOSS_EXITED_WAITING_PROBE",
                "STOPLOSS_EXITED_WAITING_REBOUND",
                "REENTRY_HOLD",
            }:
                skip_stats["stoploss_state_owned"] = skip_stats.get("stoploss_state_owned", 0) + 1
                continue

            # 低余额暂停期间产生的 token：
            # - 无持仓：暂停状态下不参与回填
            # - 有持仓：允许回填并走“仅卖出”恢复路径，避免低余额死锁
            exit_data = dict(record.get("exit_data") or {})

            if exit_reason == "LOW_BALANCE_PAUSE":
                has_position_flag = bool(exit_data.get("has_position", False))
                if self._buy_paused_due_to_balance and not has_position_flag:
                    # 兜底：若历史记录未标记 has_position，实时检查一次账户持仓
                    if token_id and self._has_account_position(token_id):
                        has_position_flag = True
                        exit_data["has_position"] = True
                        record["exit_data"] = exit_data
                    else:
                        skip_stats["low_balance_paused"] = skip_stats.get("low_balance_paused", 0) + 1
                        continue
            if bool(exit_data.get("has_position", False)):
                if not self._has_account_position(token_id):
                    if exit_reason in POSITION_REQUIRED_REASONS:
                        skip_stats["stale_position_record"] = (
                            skip_stats.get("stale_position_record", 0) + 1
                        )
                        record["refillable"] = False
                        record["stale_position_record"] = True
                        record["stale_position_detected_at"] = time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)
                        )
                        record["stale_position_detected_ts"] = float(now)
                        stale_position_downgraded += 1
                        print(
                            "[REFILL] finalize stale position record: "
                            f"token={token_id[:20]}... reason={exit_reason}"
                        )
                        continue
                    else:
                        skip_stats["downgraded_to_no_position"] = (
                            skip_stats.get("downgraded_to_no_position", 0) + 1
                        )
                    exit_data["has_position"] = False
                    exit_data["position_size"] = 0.0
                    record["exit_data"] = exit_data
                    stale_position_downgraded += 1

            has_position_now = bool(exit_data.get("has_position", False))
            if exit_reason == "LOW_BALANCE_PAUSE":
                # 余额暂停恢复阶段允许立即重试，避免长时间卡住。
                effective_cooldown_seconds = 0.0
            else:
                effective_cooldown_seconds = (
                    cooldown_with_position_seconds
                    if has_position_now
                    else cooldown_no_position_seconds
                )
            gap_backoff = gap_backoff_by_token.get(str(token_id))
            if gap_backoff:
                effective_cooldown_seconds = max(
                    effective_cooldown_seconds,
                    float(gap_backoff.get("seconds", 0.0) or 0.0),
                )

            # 检查冷却时间（exit_ts 到现在的时间间隔）
            if exit_ts > 0 and (now - exit_ts) < effective_cooldown_seconds:
                is_gap_latest = False
                if gap_backoff:
                    latest_ts = float(gap_backoff.get("latest_exit_ts", 0.0) or 0.0)
                    is_gap_latest = abs(latest_ts - float(exit_ts or 0.0)) <= 1e-6
                if is_gap_latest:
                    skip_stats["gap_backoff_cooldown"] = (
                        skip_stats.get("gap_backoff_cooldown", 0) + 1
                    )
                else:
                    skip_stats["cooldown"] = skip_stats.get("cooldown", 0) + 1
                continue

            # 检查重试次数（按退出原因分级）
            # - NO_DATA_TIMEOUT: 最多2次，优先降低低活跃市场的误淘汰抖动
            # - SHARED_WS_UNAVAILABLE: 视为基础设施瞬态故障，不设置硬上限
            retry_count = self._refill_retry_counts.get(token_id, 0)
            effective_max_retries = self._effective_refill_retry_limit(exit_reason)
            if retry_count >= effective_max_retries:
                skip_stats["max_retries"] = skip_stats.get("max_retries", 0) + 1
                if exit_reason in PERMANENT_AFTER_MAX_RETRIES_REASONS:
                    skip_stats["permanent_block"] = skip_stats.get("permanent_block", 0) + 1
                continue

            # 检查是否已在运行或pending
            if token_id in self.tasks and self.tasks[token_id].is_running():
                skip_stats["already_running"] = skip_stats.get("already_running", 0) + 1
                continue
            if token_id in self.pending_topics:
                skip_stats["already_pending"] = skip_stats.get("already_pending", 0) + 1
                continue
            if token_id in self.pending_burst_topics:
                skip_stats["already_pending_burst"] = skip_stats.get("already_pending_burst", 0) + 1
                continue
            if token_id in self.pending_exit_topics:
                skip_stats["pending_exit"] = skip_stats.get("pending_exit", 0) + 1
                continue

            # 检查是否最近已被回填过（避免短时间内重复回填）
            if token_id in self._refilled_tokens:
                skip_stats["recent_refilled"] = skip_stats.get("recent_refilled", 0) + 1
                continue

            # 【新增】对于 NO_DATA_TIMEOUT/LOW_LIQUIDITY_TIMEOUT，回填前重新验证市场状态
            if exit_reason in ("NO_DATA_TIMEOUT", "LOW_LIQUIDITY_TIMEOUT", "WS_TIMEOUT"):
                if self._market_state_checker:
                    condition_id = self._get_condition_id_for_token(token_id)
                    if condition_id:
                        try:
                            market_state = self._market_state_checker.check_market_state(
                                condition_id, token_id, use_cache=False
                            )
                            
                            if market_state.is_permanently_closed:
                                # 市场已关闭，拒绝回填并更新记录
                                print(
                                    f"[REFILL] 拒绝回填（市场已关闭）: {token_id[:16]}... "
                                    f"status={market_state.status.value}"
                                )
                                record["refillable"] = False
                                record["market_closed_on_refill_check"] = True
                                record["market_status"] = market_state.status.value
                                skip_stats["market_closed_on_refill"] = skip_stats.get("market_closed_on_refill", 0) + 1
                                
                                # 清理文件
                                if self._market_closed_cleaner:
                                    copytrade_state_path = self.config.copytrade_tokens_path.parent / "copytrade_state.json"
                                    self._market_closed_cleaner.clean_closed_market(
                                        token_id=token_id,
                                        condition_id=condition_id,
                                        exit_reason="MARKET_CLOSED_ON_REFILL",
                                        copytrade_file=str(self.config.copytrade_tokens_path),
                                        copytrade_state_file=str(copytrade_state_path) if copytrade_state_path.exists() else None,
                                        exit_tokens_file=str(self._exit_tokens_path),
                                    )
                                continue
                            else:
                                print(
                                    f"[REFILL] 允许回填: {token_id[:16]}... "
                                    f"status={market_state.status.value}"
                                )
                        except Exception as e:
                            print(f"[WARNING] 回填前市场状态检测失败: {token_id[:16]}..., error={e}")

            refillable.append(record)

        if stale_position_downgraded > 0:
            try:
                with self._file_io_lock:
                    _atomic_json_write(self._exit_tokens_path, exit_records)
                print(
                    "[REFILL] synced stale position downgrade to exit_tokens.json: "
                    f"updated={stale_position_downgraded}"
                )
            except Exception as exc:
                print(f"[WARN] failed to sync stale position downgrade: {exc}")

        # 排序优先级：
        # 1. 有持仓的优先（需要尽快卖出）
        # 2. 退出时间早的优先（等待时间长）
        def _sort_key(r: Dict[str, Any]) -> tuple:
            exit_data = r.get("exit_data", {}) or {}
            has_position = exit_data.get("has_position", False)
            exit_ts = r.get("exit_ts", 0)
            # has_position=True 排前面（0 < 1）
            # exit_ts 小的排前面（早退出的优先）
            return (0 if has_position else 1, exit_ts)

        refillable.sort(key=_sort_key)
        if self._refill_debug:
            print(
                "[REFILL][DEBUG] 过滤结果: "
                f"exit_records={len(exit_records)} refillable={len(refillable)} "
                f"cooldown_pos_sec={int(cooldown_with_position_seconds)} "
                f"cooldown_flat_sec={int(cooldown_no_position_seconds)} "
                f"max_retries={max_retries}"
            )
            if skip_stats:
                print(f"[REFILL][DEBUG] 跳过统计: {skip_stats}")

        return refillable

    def _effective_refill_retry_limit(self, exit_reason: str) -> int:
        reason = str(exit_reason or "").strip().upper()
        if reason == "NO_DATA_TIMEOUT":
            return 2
        if reason in {"SHARED_WS_UNAVAILABLE", "LOW_BALANCE_PAUSE"}:
            return 10**9
        return int(self.config.max_refill_retries)

    @staticmethod
    def _format_refill_retry_limit(limit: int) -> str:
        if limit >= 10**8:
            return "INF"
        return str(int(limit))

    def _build_gap_skip_backoff_seconds_by_token(
        self, exit_records: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, float]]:
        if not bool(getattr(self.config, "gap_skip_backoff_enabled", True)):
            return {}
        schedule_minutes = [
            float(v)
            for v in (getattr(self.config, "gap_skip_backoff_minutes", []) or [])
            if _coerce_float(v) is not None and float(v) > 0
        ]
        if not schedule_minutes:
            return {}

        gap_reasons = {"REFILL_SKIP_GAP", "POSITION_SYNC_SKIP_GAP"}
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for record in exit_records:
            token_id = str(record.get("token_id") or "").strip()
            if not token_id:
                continue
            grouped.setdefault(token_id, []).append(record)

        backoff: Dict[str, Dict[str, float]] = {}
        for token_id, records in grouped.items():
            ordered = sorted(records, key=lambda r: r.get("exit_ts", 0), reverse=True)
            if not ordered:
                continue
            latest_reason = str(ordered[0].get("exit_reason", "")).strip().upper()
            if latest_reason not in gap_reasons:
                continue
            streak = 0
            for rec in ordered:
                reason = str(rec.get("exit_reason", "")).strip().upper()
                if reason in gap_reasons:
                    streak += 1
                else:
                    break
            if streak <= 0:
                continue
            idx = min(streak, len(schedule_minutes)) - 1
            backoff[token_id] = {
                "seconds": float(schedule_minutes[idx] * 60.0),
                "streak": float(streak),
                "latest_exit_ts": float(ordered[0].get("exit_ts") or 0.0),
            }
        return backoff

    def _get_active_gap_skip_backoff(
        self,
        token_id: str,
        *,
        exit_records: Optional[List[Dict[str, Any]]] = None,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, float]]:
        token = str(token_id or "").strip()
        if not token:
            return None
        records = exit_records if exit_records is not None else self._load_exit_tokens()
        backoff = self._build_gap_skip_backoff_seconds_by_token(records).get(token)
        if not backoff:
            return None
        latest_exit_ts = float(backoff.get("latest_exit_ts", 0.0) or 0.0)
        backoff_seconds = float(backoff.get("seconds", 0.0) or 0.0)
        if latest_exit_ts <= 0.0 or backoff_seconds <= 0.0:
            return None
        current_ts = time.time() if now is None else float(now)
        hold_until_ts = latest_exit_ts + backoff_seconds
        remaining_seconds = max(0.0, hold_until_ts - current_ts)
        if remaining_seconds <= 0.0:
            return None
        return {
            "seconds": backoff_seconds,
            "streak": float(backoff.get("streak", 0.0) or 0.0),
            "latest_exit_ts": latest_exit_ts,
            "hold_until_ts": hold_until_ts,
            "remaining_seconds": remaining_seconds,
        }

    def _schedule_refill(self) -> None:
        """
        执行回填调度：从退出记录中选取可回填的token重新加入pending队列。
        """
        if not self._should_refill_slots():
            if self._refill_debug:
                running = sum(1 for t in self.tasks.values() if t.is_running())
                print(
                    "[REFILL][DEBUG] slot不足，跳过回填: "
                    f"running={running} pending={len(self.pending_topics)+len(self.pending_burst_topics)} "
                    f"max={self.config.max_concurrent_tasks}"
                )
            return

        exit_records = self._load_exit_tokens()
        if not exit_records:
            self._log_throttled("refill_no_records", 300.0, "[REFILL] no exit records, skip refill")
            return

        refillable = self._filter_refillable_tokens(exit_records)
        if not refillable:
            running = sum(1 for t in self.tasks.values() if t.is_running())
            self._log_throttled("refill_none", 300.0, f"[REFILL] no refillable token: records={len(exit_records)} running={running} pending={len(self.pending_topics)+len(self.pending_burst_topics)}")
            return

        running = sum(1 for t in self.tasks.values() if t.is_running())
        available_slots = max(0, self.config.max_concurrent_tasks - running)

        # 回填加入 pending 不受 slot 限制，由 running 控制实际子进程数量
        to_refill = refillable

        print(f"\n[REFILL] ========== Slot回填检查 ==========")
        print(f"[REFILL] 当前运行: {running}/{self.config.max_concurrent_tasks}")
        print(f"[REFILL] 可用slot: {available_slots}")
        print(f"[REFILL] 可回填token: {len(refillable)} 个")

        for record in to_refill:
            token_id = record.get("token_id")
            exit_reason = record.get("exit_reason", "UNKNOWN")
            exit_data = record.get("exit_data", {}) or {}
            if not token_id:
                continue
            self._hydrate_topic_metadata_for_blacklist(token_id)
            title_policy = self._enforce_title_blacklist_policy(
                token_id, source="refill"
            )
            if title_policy in {"blocked_no_position", "force_liquidate", "force_sell_only"}:
                continue
            has_position = exit_data.get("has_position", False)
            resume_drop_pct = exit_data.get("drop_pct_current")
            if self._is_reentry_eligible_exit(exit_reason, exit_data):
                self._mark_reentry_eligible_token(
                    token_id,
                    source=str(exit_reason or "").strip().upper(),
                )
            if self._block_topic_start_for_active_sell(
                token_id,
                source="refill",
            ):
                continue

            # 增加重试计数
            self._refill_retry_counts[token_id] = self._refill_retry_counts.get(token_id, 0) + 1
            retry_count = self._refill_retry_counts[token_id]

            # 标记为已回填
            self._refilled_tokens.add(token_id)

            # 构建恢复配置
            resume_state = None
            if has_position:
                resume_state = {
                    "has_position": True,
                    "position_size": exit_data.get("position_size"),
                    "entry_price": exit_data.get("entry_price"),
                    "skip_buy": True,  # 跳过买入阶段
                }

            # 保存恢复状态到 topic_details（供 _build_run_config 使用）
            if token_id not in self.topic_details:
                self.topic_details[token_id] = {}
            self.topic_details[token_id]["resume_state"] = resume_state
            self.topic_details[token_id]["refill_retry_count"] = retry_count
            self.topic_details[token_id]["refill_exit_reason"] = exit_reason
            # Fail-closed: 回填启动前必须先检查是否仍有残留 SELL 挂单。
            # 若存在残留卖单，子进程会在 startup guard 直接拒绝启动，避免买卖并发错位。
            self.topic_details[token_id]["startup_skip_if_open_sell"] = True
            # 区分有持仓和无持仓的refill token，便于低余额时区分处理
            if has_position:
                self.topic_details[token_id]["queue_role"] = "refill_with_position"
            else:
                self.topic_details[token_id]["queue_role"] = "refill_buy"
            if resume_drop_pct is not None:
                self.topic_details[token_id]["resume_drop_pct"] = resume_drop_pct
            else:
                self.topic_details[token_id].pop("resume_drop_pct", None)

            # 加入pending队列
            if not self._enqueue_pending_topic(token_id):
                continue

            state_hint = "有持仓→等待卖出" if has_position else "无持仓→等待买入"
            print(
                f"[REFILL] + 回填 token={token_id[:20]}... "
                f"原因={exit_reason} 状态={state_hint} "
                f"重试={retry_count}/{self._format_refill_retry_limit(self._effective_refill_retry_limit(exit_reason))}"
            )

        print(f"[REFILL] 已添加 {len(to_refill)} 个token到pending队列")
        print(f"[REFILL] ========================================\n")

    def _refresh_topics(self) -> None:
        try:
            if self._startup_sync_retry_needed:
                if time.time() >= self._next_startup_sync_retry_at:
                    self._sync_handled_topics_on_startup()
                if self._startup_sync_retry_needed:
                    print("[HANDLED][INFO] startup reconcile pending, skip normal refresh this round")
                    return
            self.latest_topics = self._load_copytrade_tokens()
            # 保留已有运行态覆盖信息（回填状态 + 调度/展示元数据），再覆盖 copytrade 快照。
            # 否则 pending 队列中的 token 在 refresh 后可能丢失 queue_role，状态日志会出现 unknown。
            old_runtime_overlays: Dict[str, Any] = {}
            tracked_topics = (
                set(self.topic_details.keys())
                | set(self.pending_topics)
                | set(self.pending_burst_topics)
                | set(self.pending_exit_topics)
                | set(self.tasks.keys())
            )
            for tid in tracked_topics:
                detail = self.topic_details.get(tid) or {}
                overlay: Dict[str, Any] = {}
                if "resume_state" in detail:
                    overlay["resume_state"] = detail.get("resume_state")
                    overlay["refill_retry_count"] = detail.get("refill_retry_count", 0)
                    overlay["refill_exit_reason"] = detail.get("refill_exit_reason")
                    overlay["resume_drop_pct"] = detail.get("resume_drop_pct")
                    overlay["resume_profit_pct"] = detail.get("resume_profit_pct")
                if detail.get("schedule_lane"):
                    overlay["schedule_lane"] = detail.get("schedule_lane")
                if detail.get("queue_role"):
                    overlay["queue_role"] = detail.get("queue_role")
                if detail.get("startup_orphan_profit_sweep"):
                    overlay["startup_orphan_profit_sweep"] = True
                if detail.get("startup_skip_if_open_sell"):
                    overlay["startup_skip_if_open_sell"] = True
                if overlay:
                    old_runtime_overlays[tid] = overlay
            self.topic_details = {}
            for item in self.latest_topics:
                topic_id = _topic_id_from_entry(item)
                if not topic_id:
                    continue
                detail = dict(item)
                detail.setdefault("topic_id", topic_id)
                self.topic_details[topic_id] = detail
            # 恢复运行态覆盖信息（回填状态 + 调度/展示元数据）
            for tid, saved in old_runtime_overlays.items():
                if tid not in self.topic_details:
                    self.topic_details[tid] = {}
                if "resume_state" in saved:
                    self.topic_details[tid]["resume_state"] = saved.get("resume_state")
                    self.topic_details[tid]["refill_retry_count"] = saved.get("refill_retry_count", 0)
                    if saved.get("refill_exit_reason"):
                        self.topic_details[tid]["refill_exit_reason"] = saved["refill_exit_reason"]
                    if saved.get("resume_drop_pct") is not None:
                        self.topic_details[tid]["resume_drop_pct"] = saved.get("resume_drop_pct")
                    if saved.get("resume_profit_pct") is not None:
                        self.topic_details[tid]["resume_profit_pct"] = saved.get("resume_profit_pct")
                if saved.get("schedule_lane"):
                    self.topic_details[tid]["schedule_lane"] = saved["schedule_lane"]
                if saved.get("queue_role"):
                    self.topic_details[tid]["queue_role"] = saved["queue_role"]
                if saved.get("startup_orphan_profit_sweep"):
                    self.topic_details[tid]["startup_orphan_profit_sweep"] = True
                if saved.get("startup_skip_if_open_sell"):
                    self.topic_details[tid]["startup_skip_if_open_sell"] = True

            sell_signal_events = self._load_copytrade_sell_signals()
            blacklist_tokens = self._load_copytrade_blacklist()
            self._apply_sell_signals(sell_signal_events)
            self._rearm_active_unmanaged_topics(
                sell_signals=sell_signal_events,
                blocked_tokens=blacklist_tokens,
            )
            blocked_tokens = blacklist_tokens
            new_topics = [
                topic_id
                for topic_id in compute_new_topics(self.latest_topics, self.handled_topics)
                if topic_id not in blocked_tokens
            ]
            skipped_blacklist = [
                topic_id
                for topic_id in compute_new_topics(self.latest_topics, self.handled_topics)
                if topic_id in blacklist_tokens
            ]
            if skipped_blacklist:
                print(
                    f"[BLACKLIST] 已过滤黑名单 token {len(skipped_blacklist)} 个"
                )
            if new_topics:
                preview = ", ".join(new_topics[:5])
                print(
                    f"[INCR] 新话题 {len(new_topics)} 个，将更新历史记录 preview={preview}"
                )
                added_topics: List[str] = []
                defer_new_topics = self._is_buy_paused_by_balance()
                for topic_id in new_topics:
                    self._hydrate_topic_metadata_for_blacklist(topic_id)
                    title_policy = self._enforce_title_blacklist_policy(
                        topic_id, source="refresh_new_topic"
                    )
                    if title_policy != "not_matched":
                        continue
                    if topic_id in self.pending_topics:
                        continue
                    if topic_id in self.pending_burst_topics:
                        continue
                    if topic_id in self.tasks and self.tasks[topic_id].is_running():
                        continue
                    if defer_new_topics:
                        if topic_id not in self._buy_pause_deferred_tokens:
                            self._buy_pause_deferred_tokens.add(topic_id)
                            # 【修复】设置 queue_role 以便状态显示
                            detail = self.topic_details.setdefault(topic_id, {})
                            detail["queue_role"] = "deferred_token"
                            has_position = self._has_account_position(topic_id)
                            self._append_exit_token_record(
                                topic_id,
                                "LOW_BALANCE_PAUSE",
                                exit_data={
                                    "has_position": has_position,
                                    "deferred_by_low_balance": True,
                                },
                                refillable=True,
                            )
                    else:
                        detail = self.topic_details.setdefault(topic_id, {})
                        detail["queue_role"] = "new_token"
                        if self._enqueue_burst_topic(topic_id, promote=False):
                            added_topics.append(topic_id)
                        continue
                    added_topics.append(topic_id)
                if defer_new_topics and added_topics:
                    print(
                        f"[BUY_GATE] 低余额暂停中：新增 token {len(added_topics)} 个已进入回填队列，待余额恢复后再回填"
                    )
                # 立即标记为已处理，防止下次轮询时重复检测到同一 token
                if added_topics:
                    self._update_handled_topics(added_topics)
            else:
                self._log_throttled("incr_none", 300.0, "[INCR] no new topics")
        except Exception as exc:  # pragma: no cover - 网络/外部依赖
            print(f"[ERROR] 读取 copytrade token 失败：{exc}")
            self.latest_topics = []

    def _has_stoploss_runtime_owner(self, token_id: str) -> bool:
        state = self._stoploss_reentry_states.get(token_id) or {}
        if not isinstance(state, dict) or not state:
            return False
        state_name = str(state.get("state") or "").strip().upper()
        return state_name in {
            "STOPLOSS_EXITED_WAITING_WINDOW",
            "STOPLOSS_EXITED_WAITING_PROBE",
            "STOPLOSS_EXITED_WAITING_REBOUND",
            "REENTRY_HOLD",
            "STOPLOSS_IOC_RETRY_PENDING",
            "STOPLOSS_IOC_RETRY_ESCALATED",
            "STOPLOSS_MANUAL_INTERVENTION_REQUIRED",
            "STOPLOSS_CLEAR_PENDING_CONFIRM",
            "STOPLOSS_CLEAR_PENDING_ESCALATED",
        }

    def _resolve_runtime_owner(
        self,
        token_id: str,
        *,
        orphan_states: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        if not token_id:
            return "none"
        detail = self.topic_details.get(token_id) or {}
        queue_role = str(detail.get("queue_role") or "").strip().lower()
        if queue_role == "manual_intervention":
            return "manual_intervention"
        if self._has_stoploss_runtime_owner(token_id):
            return "stoploss"
        if self._has_orphan_runtime_owner(token_id, orphan_states):
            return "orphan_recovery"
        if queue_role in {"startup_reconcile_position", "startup_reconcile_buy", "restored_token"}:
            return "startup_reconcile"
        return "none"

    def _has_higher_priority_runtime_owner(
        self,
        token_id: str,
        claimant: str,
        *,
        orphan_states: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        priority = {
            "none": 0,
            "active_unmanaged_rearm": 10,
            "startup_restore": 20,
            "startup_reconcile": 30,
            "orphan_recovery": 200,
            "stoploss": 300,
            "manual_intervention": 400,
        }
        owner = self._resolve_runtime_owner(token_id, orphan_states=orphan_states)
        return priority.get(owner, 0) > priority.get(str(claimant or "none"), 0)

    def _has_orphan_runtime_owner(
        self,
        token_id: str,
        orphan_states: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        latest = orphan_states if orphan_states is not None else self._load_latest_orphan_states()
        state = latest.get(token_id) or {}
        if not isinstance(state, dict) or not state:
            return False
        status = str(state.get("status") or "orphaned").strip().lower()
        return status not in {"recovered", "manual_only"}

    def _has_runtime_owner_blocking_startup_reconcile(
        self,
        token_id: str,
        *,
        orphan_states: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        return self._has_higher_priority_runtime_owner(
            token_id,
            "startup_reconcile",
            orphan_states=orphan_states,
        )

    def _has_runtime_owner_blocking_plain_restore(
        self,
        token_id: str,
        *,
        orphan_states: Optional[Dict[str, Dict[str, Any]]] = None,
        pending_exit_topics: Optional[set[str]] = None,
    ) -> bool:
        if self._has_higher_priority_runtime_owner(
            token_id,
            "startup_restore",
            orphan_states=orphan_states,
        ):
            return True
        pending_exit_scope = (
            pending_exit_topics if pending_exit_topics is not None else set(self.pending_exit_topics)
        )
        if token_id in pending_exit_scope:
            return True
        detail = self.topic_details.get(token_id) or {}
        queue_role = str(detail.get("queue_role") or "").strip().lower()
        if bool(detail.get("startup_orphan_profit_sweep")):
            return True
        return queue_role in {
            "startup_orphan_profit_sweep",
            "orphan_recovery",
            "stoploss_nofill_sell_only",
            "title_blacklist_sell_only",
            "reentry_token",
        }

    def _rearm_active_unmanaged_topics(
        self,
        *,
        sell_signals: Dict[str, Dict[str, Any]],
        blocked_tokens: set[str],
    ) -> None:
        active_copytrade_topics = {
            topic_id
            for topic_id in (_topic_id_from_entry(item) for item in self.latest_topics)
            if topic_id
        }
        if not active_copytrade_topics:
            return
        managed_scope = (
            set(self.tasks.keys())
            | set(self.pending_topics)
            | set(self.pending_burst_topics)
            | set(self.pending_exit_topics)
        )
        orphan_states = self._load_latest_orphan_states()
        now = time.time()
        self._cleanup_active_unmanaged_rearm_blocks(now)
        cooldown_sec = max(60.0, float(self.config.active_unmanaged_rearm_cooldown_sec))
        budget_window_sec = max(
            300.0, float(self.config.active_unmanaged_rearm_budget_window_sec)
        )
        budget_max_attempts = max(
            1, int(self.config.active_unmanaged_rearm_budget_max_attempts)
        )
        suppress_sec = max(
            300.0, float(self.config.active_unmanaged_rearm_budget_suppress_sec)
        )
        guard_sec = max(
            900.0,
            float(self.config.refill_cooldown_minutes_with_position) * 60.0,
        )
        unresolved_active = set(active_copytrade_topics)
        hydrated_blocks = 0
        for record in reversed(self._load_exit_tokens()):
            token_id = str(record.get("token_id") or "").strip()
            if not token_id or token_id not in unresolved_active:
                continue
            unresolved_active.discard(token_id)
            reason = str(record.get("exit_reason") or "").strip().upper()
            if reason != "SELL_ABANDONED":
                if not unresolved_active:
                    break
                continue
            exit_data = record.get("exit_data") or {}
            inactive_timeout_hours = _coerce_float(
                exit_data.get("inactive_timeout_hours") if isinstance(exit_data, dict) else None
            )
            # Only hydrate SELL_ABANDONED blocks from strategy-runtime records.
            # Child runtime records include inactive_timeout_hours; synthetic/manual records usually do not.
            if inactive_timeout_hours is None:
                if not unresolved_active:
                    break
                continue
            exit_ts = float(_coerce_float(record.get("exit_ts")) or 0.0)
            if exit_ts <= 0.0:
                if not unresolved_active:
                    break
                continue
            blocked_until = exit_ts + guard_sec
            if blocked_until > now:
                current_until = float(
                    self._active_unmanaged_rearm_blocked_until.get(token_id) or 0.0
                )
                if blocked_until > current_until:
                    self._active_unmanaged_rearm_blocked_until[token_id] = blocked_until
                    self._active_unmanaged_rearm_block_reasons[token_id] = "SELL_ABANDONED"
                    hydrated_blocks += 1
            if not unresolved_active:
                break
        if hydrated_blocks:
            self._log_throttled(
                "active_unmanaged_rearm_hydrated_sell_abandoned_blocks",
                120.0,
                (
                    "[HANDLED][RUNTIME_REARM] hydrated SELL_ABANDONED blocks from exit records: "
                    f"count={hydrated_blocks}"
                ),
            )
        candidates: List[str] = []
        for token_id in sorted(active_copytrade_topics):
            if token_id in blocked_tokens or token_id in sell_signals:
                continue
            if token_id in managed_scope:
                continue
            if self._has_higher_priority_runtime_owner(
                token_id,
                "active_unmanaged_rearm",
                orphan_states=orphan_states,
            ):
                continue
            suppressed_until = float(
                self._active_unmanaged_rearm_suppressed_until.get(token_id) or 0.0
            )
            if suppressed_until > now:
                continue
            blocked_until = float(
                self._active_unmanaged_rearm_blocked_until.get(token_id) or 0.0
            )
            if blocked_until > now:
                continue
            last_rearm_ts = float(self._active_unmanaged_rearm_last_ts.get(token_id) or 0.0)
            if last_rearm_ts > 0 and (now - last_rearm_ts) < cooldown_sec:
                continue
            candidates.append(token_id)
        if not candidates:
            return

        pos_snapshot, pos_info = self._refresh_sell_position_snapshot()
        if pos_info != "ok":
            self._log_throttled(
                "active_unmanaged_rearm_pos_unavailable",
                120.0,
                (
                    "[HANDLED][RUNTIME_REARM][WARN] position snapshot unavailable, "
                    f"skip candidates={len(candidates)} info={pos_info}"
                ),
            )
            return

        rearmed_position = 0
        rearmed_buy = 0
        for token_id in candidates:
            if token_id in self.pending_topics or token_id in self.pending_burst_topics:
                continue
            recent_attempts = [
                float(ts)
                for ts in (self._active_unmanaged_rearm_recent_ts.get(token_id) or [])
                if (now - float(ts)) <= budget_window_sec
            ]
            self._active_unmanaged_rearm_recent_ts[token_id] = recent_attempts
            if len(recent_attempts) >= budget_max_attempts:
                suppress_until = now + suppress_sec
                self._active_unmanaged_rearm_suppressed_until[token_id] = suppress_until
                self._append_exit_token_record(
                    token_id,
                    "ACTIVE_UNMANAGED_REARM_SUPPRESSED",
                    exit_data={
                        "copytrade_active": True,
                        "handled_topic": token_id in self.handled_topics,
                        "recent_attempts": len(recent_attempts),
                        "budget_window_sec": budget_window_sec,
                        "budget_max_attempts": budget_max_attempts,
                        "suppress_sec": suppress_sec,
                        "suppressed_until_ts": suppress_until,
                    },
                    refillable=False,
                )
                continue
            detail = self.topic_details.setdefault(token_id, {})
            # 运行时失管重接管时，明确回到单一路径，避免沿用旧的 refill/orphan/resume 语义。
            stale_keys_cleared: List[str] = []
            for stale_key in (
                "resume_state",
                "refill_retry_count",
                "refill_exit_reason",
                "resume_drop_pct",
                "resume_profit_pct",
                "startup_orphan_profit_sweep",
                "startup_skip_if_open_sell",
                "stoploss_reentry_resume",
                "orphaned",
                "orphaned_at",
                "sell_trigger_source",
                "sell_exit_reason",
                "sell_trigger_ts",
                "sell_exit_requested_at",
                "sell_exit_deadline_at",
                "sell_exit_phase",
                "sell_exit_source",
            ):
                if stale_key in detail:
                    stale_keys_cleared.append(stale_key)
                    detail.pop(stale_key, None)
            has_position = self._has_actionable_position(
                token_id,
                pos_snapshot.get(token_id, 0.0),
            )
            detail["schedule_lane"] = "base"
            rearm_path = "startup_reconcile_position" if has_position else "startup_reconcile_buy"
            detail["queue_role"] = rearm_path
            if self._enqueue_pending_topic(token_id):
                self._active_unmanaged_rearm_last_ts[token_id] = now
                recent_attempts.append(now)
                self._active_unmanaged_rearm_recent_ts[token_id] = recent_attempts
                self._append_exit_token_record(
                    token_id,
                    "ACTIVE_UNMANAGED_REARM",
                    exit_data={
                        "rearm_path": rearm_path,
                        "has_position": has_position,
                        "copytrade_active": True,
                        "handled_topic": token_id in self.handled_topics,
                        "stale_keys_cleared": stale_keys_cleared,
                    },
                    refillable=False,
                )
                if has_position:
                    rearmed_position += 1
                else:
                    rearmed_buy += 1
        total = rearmed_position + rearmed_buy
        if total > 0:
            print(
                "[HANDLED][RUNTIME_REARM] "
                f"rearmed={total} position={rearmed_position} buy={rearmed_buy}"
            )

    def _load_copytrade_tokens(self) -> List[Dict[str, Any]]:
        path = self.config.copytrade_tokens_path
        if not path.exists():
            print(f"[WARN] copytrade token 文件不存在：{path}")
            return []
        with self._file_io_lock:
            payload = _load_json_file(path)
        raw_tokens = payload.get("tokens")
        if not isinstance(raw_tokens, list):
            print(f"[WARN] copytrade token 文件格式异常：{path}")
            return []
        topics: List[Dict[str, Any]] = []
        skipped_not_buy = 0
        skipped_follow_cooldown = 0
        follow_cooldown_map = self._load_active_follow_cooldown_map()
        for item in raw_tokens:
            if not isinstance(item, dict):
                continue
            if not bool(item.get("active", True)):
                continue
            if not bool(item.get("introduced_by_buy", False)):
                skipped_not_buy += 1
                continue
            token_id = item.get("token_id") or item.get("tokenId")
            if not token_id:
                continue
            token_id_str = str(token_id)
            cooldown_until_ts = _coerce_float(follow_cooldown_map.get(token_id_str))
            if cooldown_until_ts is not None and cooldown_until_ts > time.time():
                skipped_follow_cooldown += 1
                continue
            market_slug = item.get("market_slug") or item.get("slug")
            market_title = (
                item.get("title")
                or item.get("question")
                or item.get("market_title")
                or item.get("topic_name")
            )
            topics.append(
                {
                    "topic_id": token_id_str,
                    "token_id": token_id_str,
                    "slug": market_slug,
                    "title": market_title,
                    "last_seen": item.get("last_seen"),
                }
            )
        print(f"[COPYTRADE] 已读取 token {len(topics)} 条 | {path}")
        if skipped_not_buy:
            print(f"[COPYTRADE] 已跳过非BUY引入 token {skipped_not_buy} 条")
        if skipped_follow_cooldown:
            print(f"[COPYTRADE] 已跳过冷却中 token {skipped_follow_cooldown} 条")
        return topics

    def _load_copytrade_sell_signals(self) -> Dict[str, Dict[str, Any]]:
        path = self.config.copytrade_sell_signals_path
        if not path.exists():
            return {}
        with self._file_io_lock:
            payload = _load_json_file(path)
        raw_tokens = payload.get("sell_tokens")
        if not isinstance(raw_tokens, list):
            print(f"[WARN] copytrade sell_signal 文件格式异常：{path}")
            return {}
        signals: Dict[str, Dict[str, Any]] = {}
        skipped = 0
        for item in raw_tokens:
            if not isinstance(item, dict):
                continue
            token_id = item.get("token_id") or item.get("tokenId")
            if not token_id:
                continue
            if not bool(item.get("active", True)):
                continue
            if not item.get("introduced_by_buy", False):
                skipped += 1
                continue
            status = str(item.get("status") or "pending").strip().lower()
            if status in {"done", "stale_ignored"}:
                continue
            entry = dict(item)
            entry["status"] = status if status else "pending"
            try:
                entry["attempts"] = int(entry.get("attempts", 0) or 0)
            except (TypeError, ValueError):
                entry["attempts"] = 0
            signals[str(token_id)] = entry
        if signals:
            preview = ", ".join(list(signals.keys())[:5])
            print(f"[COPYTRADE] 已读取 sell 信号 {len(signals)} 条 preview={preview}")
        if skipped:
            print(f"[COPYTRADE] 已跳过未引入的 sell 信号 {skipped} 条")
        return signals

    @staticmethod
    def _coerce_sell_signal_ts(entry: Dict[str, Any]) -> Optional[float]:
        if not isinstance(entry, dict):
            return None
        for key in ("signal_ts", "updated_ts", "created_ts"):
            raw = entry.get(key)
            ts = _coerce_float(raw)
            if ts is not None and ts > 0:
                return float(ts)
        for key in ("updated_at", "last_seen"):
            raw_text = str(entry.get(key) or "").strip()
            if not raw_text:
                continue
            normalized = raw_text.replace("Z", "+00:00")
            try:
                return float(datetime.fromisoformat(normalized).timestamp())
            except Exception:
                continue
        return None

    def _filter_sell_signals_for_execution(
        self,
        sell_signals: Dict[str, Dict[str, Any]],
        *,
        now: float,
    ) -> Dict[str, Dict[str, Any]]:
        if not sell_signals:
            return {}
        if self._sell_bootstrap_done:
            return sell_signals
        cutoff_ts = float(self._process_started_at) - float(SELL_SIGNAL_REPLAY_WARMUP_SEC)
        eligible: Dict[str, Dict[str, Any]] = {}
        stale_tokens: List[str] = []
        stale_tokens_with_position: List[str] = []
        for token_id, entry in sell_signals.items():
            signal_ts = self._coerce_sell_signal_ts(entry)
            if signal_ts is None or signal_ts < cutoff_ts:
                if self._has_account_position(token_id):
                    eligible[token_id] = entry
                    stale_tokens_with_position.append(token_id)
                else:
                    stale_tokens.append(token_id)
                continue
            eligible[token_id] = entry
        if stale_tokens_with_position:
            preview = ", ".join(stale_tokens_with_position[:5])
            print(
                "[COPYTRADE][REPLAY_GUARD] 启动期旧 sell 信号存在本地持仓，保留执行: "
                f"count={len(stale_tokens_with_position)} preview={preview}"
            )
        if stale_tokens:
            preview = ", ".join(stale_tokens[:5])
            print(
                "[COPYTRADE][REPLAY_GUARD] 启动期忽略历史 sell 信号: "
                f"count={len(stale_tokens)} cutoff={cutoff_ts:.0f} preview={preview}"
            )
            for token_id in stale_tokens:
                self._update_sell_signal_event(
                    token_id,
                    status="stale_ignored",
                    note="startup_replay_guard",
                )
        return eligible

    def _compute_orderbook_missing_backoff(
        self, token_id: str, *, now: float
    ) -> tuple[float, int]:
        orderbook_missing_reasons = {"ORDERBOOK_MISSING_EXIT_ONLY", "ORDERBOOK_NOT_FOUND"}
        recent_missing_ts: List[float] = []
        for rec in reversed(self._load_exit_tokens()):
            if str(rec.get("token_id") or "") != token_id:
                continue
            reason = str(rec.get("exit_reason") or "").strip().upper()
            if reason not in orderbook_missing_reasons:
                continue
            ts = float(_coerce_float(rec.get("exit_ts")) or 0.0)
            if ts <= 0:
                continue
            recent_missing_ts.append(ts)
            if len(recent_missing_ts) >= len(EXIT_ORDERBOOK_MISSING_BACKOFF_SCHEDULE_SEC):
                break
        if not recent_missing_ts:
            return 0.0, 0
        latest_ts = recent_missing_ts[0]
        streak = len(recent_missing_ts)
        idx = min(streak, len(EXIT_ORDERBOOK_MISSING_BACKOFF_SCHEDULE_SEC)) - 1
        backoff_sec = float(EXIT_ORDERBOOK_MISSING_BACKOFF_SCHEDULE_SEC[idx])
        age = max(0.0, now - latest_ts)
        if age >= backoff_sec:
            return 0.0, streak
        return backoff_sec - age, streak

    def _should_suppress_exit_cleanup(self, token_id: str, *, now: float) -> tuple[bool, Optional[str]]:
        until_ts = float(self._exit_cleanup_suppressed_until.get(token_id, 0.0) or 0.0)
        if until_ts > now:
            wait_sec = max(0.0, until_ts - now)
            return True, f"in_memory_backoff:{wait_sec:.0f}s"
        hold_sec, streak = self._compute_orderbook_missing_backoff(token_id, now=now)
        if hold_sec > 0:
            self._exit_cleanup_suppressed_until[token_id] = now + hold_sec
            return True, f"orderbook_missing_backoff:{hold_sec:.0f}s(streak={streak})"
        return False, None

    def _load_copytrade_blacklist(self) -> set[str]:
        path = self.config.copytrade_blacklist_path
        if not path.exists():
            return set()
        with self._file_io_lock:
            payload = _load_json_file(path)
        rows = payload.get("tokens") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            print(f"[WARN] copytrade blacklist 文件格式异常：{path}")
            return set()
        out: set[str] = set()
        for item in rows:
            if not isinstance(item, dict):
                continue
            token_id = item.get("token_id") or item.get("tokenId")
            if token_id is not None and str(token_id).strip():
                out.add(str(token_id).strip())
        return out

    def _match_title_blacklist(
        self, token_id: str
    ) -> tuple[bool, Optional[str], Optional[str]]:
        if not bool(getattr(self.config, "title_blacklist_enabled", False)):
            return False, None, None
        keywords = list(getattr(self.config, "title_blacklist_keywords", []) or [])
        if not keywords:
            return False, None, None
        detail = self.topic_details.get(token_id) or {}
        title = str(
            detail.get("title")
            or detail.get("question")
            or detail.get("topic_name")
            or detail.get("market_title")
            or ""
        ).strip()
        slug = str(detail.get("slug") or "").strip()
        candidates: List[str] = []
        if title:
            candidates.append(title)
        if bool(getattr(self.config, "title_blacklist_match_on_slug", True)) and slug:
            candidates.append(slug)
        if not candidates:
            return False, None, title or None

        haystack = "\n".join(candidates).lower()
        for keyword in keywords:
            kw = str(keyword or "").strip().lower()
            if not kw:
                continue
            if kw in haystack:
                return True, str(keyword), title or None
        return False, None, title or None

    def _remove_pending_exit_topic(self, token_id: str) -> None:
        if token_id in self.pending_exit_topics:
            try:
                self.pending_exit_topics.remove(token_id)
            except ValueError:
                pass

    def _enforce_title_blacklist_policy(self, token_id: str, *, source: str) -> str:
        matched, matched_keyword, matched_title = self._match_title_blacklist(token_id)
        detail = self.topic_details.setdefault(token_id, {})
        detail["blacklist_last_checked_at"] = time.time()
        detail["blacklist_last_check_source"] = source
        if not matched:
            detail["blacklist_last_result"] = "not_matched"
            if not str(
                detail.get("title")
                or detail.get("question")
                or detail.get("topic_name")
                or detail.get("market_title")
                or detail.get("slug")
                or ""
            ).strip():
                detail["blacklist_check_skipped_reason"] = (
                    "no_title_or_slug_after_metadata_hydration"
                )
            return "not_matched"
        detail.pop("blacklist_check_skipped_reason", None)
        detail["blacklist_last_result"] = "matched"
        detail["blacklist_keyword"] = matched_keyword
        detail["blacklist_matched_title"] = matched_title

        has_position = self._has_account_position(token_id)
        action = str(
            getattr(self.config, "title_blacklist_action_with_position", "sell_only_maker")
            or "sell_only_maker"
        ).strip().lower()
        task = self.tasks.get(token_id)

        if not has_position:
            self._remove_pending_topic(token_id)
            self._remove_pending_exit_topic(token_id)
            if task and task.is_running():
                task.no_restart = True
                task.end_reason = "title blacklist no position"
                self._terminate_task(task, reason="title blacklist no position")
                self.tasks.pop(token_id, None)
            if token_id not in self._title_blacklist_finalized_no_position:
                self._append_exit_token_record(
                    token_id,
                    "TITLE_BLACKLIST_NO_POSITION",
                    exit_data={
                        "source": source,
                        "has_position": False,
                        "matched_keyword": matched_keyword,
                        "title": matched_title,
                    },
                    refillable=False,
                )
                self._title_blacklist_finalized_no_position.add(token_id)
            self._update_handled_topics([token_id])
            print(
                "[BLACKLIST][TITLE] 命中且无仓位，跳过交易: "
                f"token={token_id[:20]}... keyword={matched_keyword} source={source}"
            )
            return "blocked_no_position"

        if action == "liquidate":
            self._remove_pending_topic(token_id)
            self._mark_token_orphaned(
                token_id,
                reason="TITLE_BLACKLIST_LIQUIDATE_BLOCKED",
                trigger_source=f"title_blacklist:{source}",
                task=task,
                note="liquidate action downgraded to orphan manual handling",
            )
            if not detail.get("blacklist_with_position_recorded"):
                self._append_exit_token_record(
                    token_id,
                    "TITLE_BLACKLIST_WITH_POSITION",
                    exit_data={
                        "source": source,
                        "has_position": True,
                        "mode": "liquidate",
                        "matched_keyword": matched_keyword,
                        "title": matched_title,
                    },
                    refillable=False,
                )
                detail["blacklist_with_position_recorded"] = True
            self._update_handled_topics([token_id])
            print(
                "[BLACKLIST][TITLE] 命中且有仓位，已降级为 orphan 人工处理: "
                f"token={token_id[:20]}... keyword={matched_keyword} source={source}"
            )
            return "force_liquidate"

        # 默认：sell_only_maker。若当前是普通 maker，先重启为仅卖出。
        self._remove_pending_exit_topic(token_id)
        detail["force_sell_only_on_startup"] = True
        detail["force_sell_only_reason"] = "title blacklist policy"
        detail["queue_role"] = "title_blacklist_sell_only"
        detail["schedule_lane"] = "base"
        if task and task.is_running() and source != "before_start":
            task.no_restart = True
            task.end_reason = "title blacklist switch to sell_only"
            self._terminate_task(task, reason="title blacklist switch to sell_only")
            self.tasks.pop(token_id, None)
        if (
            source != "before_start"
            and token_id not in self.pending_topics
            and token_id not in self.pending_burst_topics
            and not (task and task.is_running())
        ):
            self._enqueue_pending_topic(token_id)
        if not detail.get("blacklist_with_position_recorded"):
            self._append_exit_token_record(
                token_id,
                "TITLE_BLACKLIST_WITH_POSITION",
                exit_data={
                    "source": source,
                    "has_position": True,
                    "mode": "sell_only_maker",
                    "matched_keyword": matched_keyword,
                    "title": matched_title,
                },
                refillable=True,
            )
            detail["blacklist_with_position_recorded"] = True
        self._update_handled_topics([token_id])
        print(
            "[BLACKLIST][TITLE] 命中且有仓位，切换仅卖出 maker: "
            f"token={token_id[:20]}... keyword={matched_keyword} source={source}"
        )
        return "force_sell_only"

    def _exit_signal_path(self, token_id: str) -> Path:
        safe_id = _safe_topic_filename(token_id)
        return self.config.data_dir / f"exit_signal_{safe_id}.json"

    def _has_exit_signal_file(self, token_id: str) -> bool:
        if not token_id:
            return False
        return self._is_exit_signal_payload_active(self._load_exit_signal_payload(token_id))

    def _active_sell_block_reason(
        self,
        token_id: str,
        *,
        sell_signals: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        if not token_id:
            return None
        if self._has_exit_signal_file(token_id):
            return "exit_signal_active"
        signal_map = (
            sell_signals
            if sell_signals is not None
            else self._load_copytrade_sell_signals()
        )
        if token_id in signal_map:
            return "sell_signal_active"
        return None

    def _block_topic_start_for_active_sell(
        self,
        token_id: str,
        *,
        source: str,
        sell_signals: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        reason = self._active_sell_block_reason(token_id, sell_signals=sell_signals)
        if not reason:
            return False

        has_position = self._has_account_position(token_id)
        print(
            f"[BUY_GATE][BLOCK] token={token_id[:20]}... "
            f"reason={reason} has_position={has_position} source={source}"
        )
        self._remove_pending_topic(token_id)
        if has_position and not self._is_sell_cleanup_in_flight(token_id):
            if reason == "sell_signal_active" and not self._has_exit_signal_file(token_id):
                self._issue_exit_signal(
                    token_id,
                    trigger_source=f"{source}_buy_gate",
                    trigger_reason="COPYTRADE_SELL",
                )
            self.pending_exit_topics.append(token_id)
            print(
                f"[EXIT-SIGNAL][REQUEUE] token={token_id[:20]}... "
                f"reason={reason} source={source}"
            )
        return True

    def _issue_exit_signal(
        self,
        token_id: str,
        *,
        trigger_source: str = "unspecified",
        trigger_reason: str = "UNSPECIFIED",
        require_position: bool = True,
    ) -> bool:
        normalized_reason = self._normalize_exit_reason(trigger_reason)
        if normalized_reason not in ALLOWED_EXIT_SIGNAL_REASONS:
            print(
                f"[EXIT-SIGNAL][SKIP] token={token_id[:20]}... "
                f"illegal_reason={normalized_reason or 'UNSPECIFIED'} source={trigger_source}"
            )
            return False
        if require_position and not self._has_account_position(token_id):
            print(
                f"[EXIT-SIGNAL][SKIP] token={token_id[:20]}... "
                f"reason={normalized_reason} source={trigger_source} no_position"
            )
            return False
        path = self._exit_signal_path(token_id)
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload = {
            "schema_version": 2,
            "token_id": token_id,
            "active": True,
            "status": "pending",
            "trigger_path": "exit_signal",
            "trigger_source": str(trigger_source),
            "exit_reason": normalized_reason,
            "trigger_reason": normalized_reason,
            "issued_at": now_iso,
            "updated_at": now_iso,
        }
        _dump_json_file(path, payload)
        return True

    def _load_exit_signal_payload(self, token_id: str) -> Dict[str, Any]:
        path = self._exit_signal_path(token_id)
        if not path.exists():
            return {}
        try:
            with self._file_io_lock:
                payload = _load_json_file(path)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _is_exit_signal_payload_active(payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        token_id = str(payload.get("token_id") or "").strip()
        if not token_id:
            return False
        exit_reason = str(payload.get("exit_reason") or "").strip().upper()
        if exit_reason not in ALLOWED_EXIT_SIGNAL_REASONS:
            return False
        if not bool(payload.get("active", True)):
            return False
        status = str(payload.get("status") or "pending").strip().lower()
        return status not in {"done", "canceled", "stale_ignored", "failed"}

    def _mark_exit_signal_inactive(
        self,
        token_id: str,
        *,
        status: str,
        source: str,
        invalidate_reason: str,
    ) -> None:
        path = self._exit_signal_path(token_id)
        if not path.exists():
            return
        payload = self._load_exit_signal_payload(token_id)
        if not payload:
            payload = {"token_id": token_id}
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload["active"] = False
        payload["status"] = str(status or "done")
        payload["consumed_at"] = now_iso
        payload["consumed_by"] = str(source or "")
        payload["invalidate_reason"] = str(invalidate_reason or "")
        payload["updated_at"] = now_iso
        _dump_json_file(path, payload)

    def _finalize_sell_lifecycle_without_position(
        self,
        token_id: str,
        *,
        task: Optional[TopicTask],
        source: str,
        reason: str,
    ) -> None:
        if not token_id:
            return
        if task and task.is_running():
            task.no_restart = True
            task.end_reason = "sell signal lifecycle ended without position"
            task.heartbeat("sell signal arrived before local position")
            self._terminate_task(task, reason="sell signal lifecycle ended without position")
        self.tasks.pop(token_id, None)
        self._mark_exit_signal_inactive(
            token_id,
            status="done",
            source=source,
            invalidate_reason="sell_signal_without_position",
        )
        self._mark_token_cycle_invalidated(
            token_id,
            reason="sell_signal_without_local_position",
            source=source,
        )
        self._remove_token_from_copytrade_files(token_id)
        self._purge_token_runtime_state(token_id)
        self._remove_from_handled_topics(token_id)
        print(
            "[COPYTRADE] SELL 信号终结当前 token 生命周期（无本地持仓）: "
            f"token_id={token_id} source={source} reason={reason}"
        )

    def _finalize_copytrade_sell_after_flat(
        self,
        token_id: str,
        *,
        task: Optional[TopicTask],
        source: str,
        reason: str,
    ) -> None:
        if not token_id:
            return
        self.tasks.pop(token_id, None)
        self._mark_exit_signal_inactive(
            token_id,
            status="done",
            source=source,
            invalidate_reason="position_flat",
        )
        self._remove_token_from_copytrade_files(token_id)
        self._mark_token_cycle_closed_runtime(token_id, task=task)
        self._purge_token_runtime_state(token_id)
        self._remove_from_handled_topics(token_id)
        print(
            "[COPYTRADE] SELL 信号在本地周期收口后完成终态冻结: "
            f"token_id={token_id} source={source} reason={reason}"
        )

    def _has_account_position(self, token_id: str, *, force_refresh: bool = False) -> bool:
        if not token_id:
            return False
        now = time.time()
        if not force_refresh:
            cached = self._position_snapshot_cache.get(token_id)
            if cached:
                cached_has_position = bool(cached.get("has_position", False))
                ttl = (
                    POSITION_CHECK_CACHE_TTL_SEC
                    if cached_has_position
                    else POSITION_CHECK_NEGATIVE_CACHE_TTL_SEC
                )
                if now - cached.get("ts", 0.0) <= ttl:
                    return cached_has_position

        if not self._position_address:
            address, origin = _resolve_position_address_from_env()
            self._position_address = address
            self._position_address_origin = origin
            if not address and not self._position_address_warned:
                self._position_address_warned = True
                print(f"[COPYTRADE][WARN] {origin}")

        if not self._position_address:
            self._position_snapshot_cache[token_id] = {
                "ts": now,
                "has_position": False,
            }
            return False

        pos_size, info = _fetch_position_size_from_data_api(
            self._position_address,
            token_id,
        )
        normalized_pos_size = float(pos_size or 0.0)
        has_position = self._has_actionable_position(token_id, normalized_pos_size)
        if info != "ok" and not has_position:
            print(f"[COPYTRADE][INFO] 持仓检查失败 token={token_id} info={info}")
        self._position_snapshot_cache[token_id] = {
            "ts": now,
            "has_position": has_position,
            "pos_size": normalized_pos_size,
        }
        return has_position

    def _reconcile_exit_signals(self) -> None:
        signal_paths = sorted(self.config.data_dir.glob("exit_signal_*.json"))
        if not signal_paths:
            return

        for path in signal_paths:
            try:
                with self._file_io_lock:
                    payload = _load_json_file(path)
            except Exception as exc:
                print(f"[EXIT-SIGNAL][WARN] 读取失败 path={path.name} error={exc}")
                continue

            token_id = ""
            if isinstance(payload, dict):
                token_id = str(payload.get("token_id") or "").strip()
            if not token_id:
                token_id = path.stem.replace("exit_signal_", "", 1)
            if not token_id:
                continue
            if isinstance(payload, dict):
                active = self._is_exit_signal_payload_active(payload)
                if not active:
                    raw_reason = str(payload.get("exit_reason") or "").strip().upper()
                    status = str(payload.get("status") or "").strip().lower()
                    if status not in {"done", "canceled", "stale_ignored", "failed"}:
                        invalidate_reason = (
                            "missing_exit_reason"
                            if not raw_reason
                            else f"illegal_exit_reason:{raw_reason}"
                        )
                        self._mark_exit_signal_inactive(
                            token_id,
                            status="stale_ignored",
                            source="reconcile_exit_signals",
                            invalidate_reason=invalidate_reason,
                        )
                        print(
                            f"[EXIT-SIGNAL][GC] token={token_id[:20]}... "
                            f"reason={invalidate_reason}"
                        )
                    continue

            if token_id in self.pending_topics or token_id in self.pending_burst_topics:
                self._remove_pending_topic(token_id)
                print(
                    f"[EXIT-SIGNAL][BLOCK] token={token_id[:20]}... "
                    "reason=exit_signal_active"
                )

            has_position = self._has_account_position(token_id, force_refresh=True)
            task = self.tasks.get(token_id)
            has_running_task = bool(task and task.is_running())
            if has_position:
                if not has_running_task and not self._is_sell_cleanup_in_flight(token_id):
                    self.pending_exit_topics.append(token_id)
                    print(
                        f"[EXIT-SIGNAL][REQUEUE] token={token_id[:20]}... "
                        "reason=signal_with_position_no_task"
                    )
                continue

            self._mark_exit_signal_inactive(
                token_id,
                status="done",
                source="reconcile_exit_signals",
                invalidate_reason="no_position",
            )
            print(
                f"[EXIT-SIGNAL][GC] token={token_id[:20]}... "
                "reason=no_position"
            )

    def _has_position_for_sell_signal(
        self,
        token_id: str,
        snapshot: Dict[str, float],
        snapshot_info: str,
    ) -> tuple[bool, str]:
        pos_size = float(snapshot.get(token_id, 0.0) or 0.0)
        if self._has_actionable_position(token_id, pos_size):
            return True, "snapshot"
        if snapshot_info == "ok":
            has_position = self._has_account_position(token_id, force_refresh=True)
            if has_position:
                return True, "fallback_has_position_after_snapshot_miss"
            return False, "snapshot_no_position_confirmed"
        has_position = self._has_account_position(token_id)
        if has_position:
            return True, f"fallback_has_position:{snapshot_info}"
        return False, f"snapshot_unavailable:{snapshot_info}"

    def _apply_sell_signals(self, sell_signals: Dict[str, Dict[str, Any]]) -> None:
        if not sell_signals:
            return
        now = time.time()
        sell_signals = self._filter_sell_signals_for_execution(sell_signals, now=now)
        if not sell_signals:
            return
        signal_tokens = set(sell_signals.keys())
        # 仅保留当前仍在 sell 文件中的处理记录，
        # 当上游移除并再次写入同一 token 时，允许新一轮 sell 信号重新触发。
        self._handled_sell_signals.intersection_update(signal_tokens)

        full_recheck_due = (not self._sell_bootstrap_done) or (
            now >= self._next_sell_full_recheck_at
        )

        if full_recheck_due:
            self._run_full_sell_signal_recheck(sell_signals)
            self._sell_bootstrap_done = True
            self._next_sell_full_recheck_at = now + max(
                10.0,
                float(self._unified_position_cycle_interval_sec()),
            )
            return

        new_signals = signal_tokens - self._handled_sell_signals
        if not new_signals:
            return

        for token_id in new_signals:
            task = self.tasks.get(token_id)
            has_running_task = bool(task and task.is_running())

            if token_id in self._completed_exit_cleanup_tokens:
                self._handled_sell_signals.add(token_id)
                continue
            if task and not has_running_task and task.end_reason == "sell signal cleanup":
                self._completed_exit_cleanup_tokens.add(token_id)
                self._handled_sell_signals.add(token_id)
                continue
            self._ensure_local_cycle_for_managed_task(token_id, task)
            cycle_allowed, cycle_reason = self._evaluate_sell_signal_local_gate(
                token_id,
                signal_entry=sell_signals.get(token_id),
                allow_started_not_bought_cleanup=bool(task or token_id in self.pending_topics),
            )
            if not cycle_allowed:
                self._mark_token_cycle_invalidated(
                    token_id,
                    reason=f"sell_signal_without_active_local_cycle:{cycle_reason}",
                    source="copytrade_sell_signal_incremental",
                )
                self._update_sell_signal_event(
                    token_id,
                    status="stale_ignored",
                    note=f"local_cycle_gate:{cycle_reason}",
                )
                self._handled_sell_signals.add(token_id)
                print(
                    "[CYCLE_GATE][SELL][IGNORE] increment signal ignored: "
                    f"token_id={token_id} cycle={cycle_reason}"
                )
                continue

            has_position, reason = self._has_position_for_sell_signal(
                token_id,
                self._sell_position_snapshot,
                self._sell_position_snapshot_info,
            )
            if not has_position:
                print(
                    "[COPYTRADE] SELL 信号终结本轮 token（无本地持仓，不执行清仓）: "
                    f"token_id={token_id} info={reason}"
                )
                self._finalize_sell_lifecycle_without_position(
                    token_id,
                    task=task,
                    source="copytrade_sell_signal_incremental",
                    reason=reason,
                )
                self._handled_sell_signals.add(token_id)
                continue
            print(
            "[COPYTRADE] SELL 信号触发持仓清仓: "
                f"token_id={token_id} reason={reason}"
            )
            self._trigger_sell_exit(
                token_id,
                task,
                trigger_source="copytrade_sell_signal_incremental",
                trigger_reason="COPYTRADE_SELL",
                signal_entry=sell_signals.get(token_id),
            )

    def _run_full_sell_signal_recheck(self, sell_signals: Dict[str, Dict[str, Any]]) -> None:
        """启动首轮 + 周期性兜底：全量检查全部 sell token。"""
        if not sell_signals:
            return
        signal_tokens = set(sell_signals.keys())
        snapshot, info = self._refresh_sell_position_snapshot()
        # 缓存快照供增量路径使用（新增 sell 信号无需再次拉取）
        self._sell_position_snapshot = snapshot
        self._sell_position_snapshot_info = info
        print(
            "[COPYTRADE][INFO] SELL 全量复检: "
            f"signals={len(signal_tokens)} positions={len(snapshot)} info={info}"
        )
        for token_id in signal_tokens:
            task = self.tasks.get(token_id)
            has_running_task = bool(task and task.is_running())

            if token_id in self._completed_exit_cleanup_tokens:
                self._handled_sell_signals.add(token_id)
                continue
            if task and not has_running_task and task.end_reason == "sell signal cleanup":
                self._completed_exit_cleanup_tokens.add(token_id)
                self._handled_sell_signals.add(token_id)
                continue
            self._ensure_local_cycle_for_managed_task(token_id, task)
            cycle_allowed, cycle_reason = self._evaluate_sell_signal_local_gate(
                token_id,
                signal_entry=sell_signals.get(token_id),
                allow_started_not_bought_cleanup=bool(task or token_id in self.pending_topics),
            )
            if not cycle_allowed:
                detail = self.topic_details.get(token_id) or {}
                terminal_reason = str(detail.get("terminal_sell_reason") or "").strip().upper()
                if cycle_reason == "cycle_closed" and terminal_reason == "COPYTRADE_SELL":
                    has_position = self._has_account_position(token_id, force_refresh=True)
                    if not has_position:
                        self._finalize_copytrade_sell_after_flat(
                            token_id,
                            task=task,
                            source="copytrade_sell_signal_full_recheck",
                            reason="cycle_closed_after_copytrade_sell",
                        )
                        self._handled_sell_signals.add(token_id)
                        continue
                self._mark_token_cycle_invalidated(
                    token_id,
                    reason=f"sell_signal_without_active_local_cycle:{cycle_reason}",
                    source="copytrade_sell_signal_full_recheck",
                )
                self._update_sell_signal_event(
                    token_id,
                    status="stale_ignored",
                    note=f"local_cycle_gate:{cycle_reason}",
                )
                self._handled_sell_signals.add(token_id)
                print(
                    "[CYCLE_GATE][SELL][IGNORE] full recheck signal ignored: "
                    f"token_id={token_id} cycle={cycle_reason}"
                )
                continue

            has_position, reason = self._has_position_for_sell_signal(
                token_id,
                snapshot,
                info,
            )
            if not has_position:
                print(
                    "[COPYTRADE] SELL 信号终结本轮 token（无本地持仓，不执行清仓）: "
                    f"token_id={token_id} info={reason}"
                )
                self._finalize_sell_lifecycle_without_position(
                    token_id,
                    task=task,
                    source="copytrade_sell_signal_full_recheck",
                    reason=reason,
                )
                self._handled_sell_signals.add(token_id)
                continue
            print(
                "[COPYTRADE] SELL 信号触发持仓清仓: "
                f"token_id={token_id} reason={reason}"
            )
            self._trigger_sell_exit(
                token_id,
                task,
                trigger_source="copytrade_sell_signal_full_recheck",
                trigger_reason="COPYTRADE_SELL",
                signal_entry=sell_signals.get(token_id),
            )

    def _trigger_sell_exit(
        self,
        token_id: str,
        task: Optional[TopicTask],
        *,
        trigger_source: str = "unspecified",
        trigger_reason: str = "UNSPECIFIED",
        signal_entry: Optional[Dict[str, Any]] = None,
    ) -> bool:
        exit_reason = self._normalize_exit_reason(trigger_reason)
        if not self._is_ioc_exit_reason_allowed(exit_reason):
            self._mark_token_orphaned(
                token_id,
                reason=f"IOC_BLOCKED_{exit_reason or 'UNSPECIFIED'}",
                trigger_source=trigger_source,
                task=task,
                note="trigger requested exit-only cleanup but reason is not whitelisted",
            )
            return False
        cycle_allowed, cycle_reason = self._evaluate_sell_signal_local_gate(
            token_id,
            signal_entry=signal_entry,
        )
        if not cycle_allowed:
            self._mark_token_cycle_invalidated(
                token_id,
                reason=f"sell_signal_without_active_local_cycle:{cycle_reason}",
                source=trigger_source,
            )
            self._update_sell_signal_event(
                token_id,
                status="stale_ignored",
                note=f"local_cycle_gate:{cycle_reason}",
            )
            return False
        now = time.time()
        suppressed, suppress_reason = self._should_suppress_exit_cleanup(token_id, now=now)
        if suppressed:
            self._log_throttled(
                f"exit_cleanup_suppressed_{token_id}",
                120.0,
                (
                    f"[EXIT-CLEAN][SUPPRESS] token={token_id[:20]}... "
                    f"source={trigger_source} reason={suppress_reason}"
                ),
            )
            return False
        self._clear_reentry_eligible_token(token_id, reason="sell_exit_triggered")
        self._update_sell_signal_event(token_id, status="processing", note=f"source={trigger_source}")
        if token_id in self.pending_topics:
            self._remove_pending_topic(token_id)
        detail = self.topic_details.setdefault(token_id, {})
        detail["sell_trigger_source"] = str(trigger_source)
        detail["sell_exit_reason"] = exit_reason
        detail["sell_trigger_ts"] = float(now)
        if exit_reason == "COPYTRADE_SELL":
            detail["terminal_sell_reason"] = exit_reason
            detail["terminal_sell_requested_at"] = float(now)
        else:
            detail.pop("terminal_sell_reason", None)
            detail.pop("terminal_sell_requested_at", None)
        if task and task.is_running():
            task.no_restart = True
            task.end_reason = "sell signal"
            task.heartbeat("sell signal received")
            deadline = now + SELL_SIGNAL_FORCE_CLEANUP_TIMEOUT_SEC
            self._sell_exit_deadlines[token_id] = deadline
            detail["sell_exit_requested_at"] = now
            detail["sell_exit_deadline_at"] = deadline
            detail["sell_exit_phase"] = "grace"
            detail["sell_exit_source"] = str(trigger_source)
        self._issue_exit_signal(
            token_id,
            trigger_source=trigger_source,
            trigger_reason=exit_reason,
        )
        if not (task and task.is_running()):
            if (
                token_id not in self.pending_exit_topics
                and token_id not in self.pending_topics
                and token_id not in self.pending_burst_topics
            ):
                self.pending_exit_topics.append(token_id)
        print(
            f"[EXIT-CLEAN][TRIGGER] token={token_id[:20]}... source={trigger_source} "
            f"reason={exit_reason} running={bool(task and task.is_running())}"
        )
        self._handled_sell_signals.add(token_id)
        return True

    @staticmethod
    def _normalize_exit_reason(reason: Any) -> str:
        return str(reason or "").strip().upper()

    def _is_ioc_exit_reason_allowed(self, reason: Any) -> bool:
        normalized = self._normalize_exit_reason(reason)
        return normalized in ALLOWED_IOC_EXIT_REASONS

    def _append_orphan_token_record(self, record: Dict[str, Any]) -> None:
        path = self._orphan_tokens_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_io_lock:
            existing: List[Dict[str, Any]] = []
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, list):
                        existing = [x for x in loaded if isinstance(x, dict)]
                except Exception:
                    existing = []
            token_id = str(record.get("token_id") or "").strip()
            if token_id:
                existing = [
                    item
                    for item in existing
                    if str(item.get("token_id") or "").strip() != token_id
                ]
            existing.append(record)
            if len(existing) > 2000:
                existing = existing[-2000:]
            _atomic_json_write(path, existing)

    def _load_orphan_token_records(self) -> List[Dict[str, Any]]:
        path = self._orphan_tokens_path
        if not path.exists():
            return []
        with self._file_io_lock:
            try:
                with path.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, list):
                    return []
                return [item for item in loaded if isinstance(item, dict)]
            except Exception:
                return []

    @staticmethod
    def _coerce_orphan_record_ts(record: Dict[str, Any]) -> float:
        for key in ("updated_ts", "probe_ts", "next_probe_at"):
            ts = _coerce_float(record.get(key))
            if ts is not None and ts > 0:
                return float(ts)
        raw_text = str(record.get("updated_at") or "").strip()
        if raw_text:
            normalized = raw_text.replace("Z", "+00:00")
            try:
                return float(datetime.fromisoformat(normalized).timestamp())
            except Exception:
                pass
        return 0.0

    def _load_latest_orphan_states(self) -> Dict[str, Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for record in self._load_orphan_token_records():
            token_id = str(record.get("token_id") or "").strip()
            if not token_id:
                continue
            prev = latest.get(token_id)
            prev_ts = self._coerce_orphan_record_ts(prev) if prev else 0.0
            curr_ts = self._coerce_orphan_record_ts(record)
            if prev is None or curr_ts >= prev_ts:
                latest[token_id] = dict(record)
        return latest

    def _orphan_probe_delay_sec(self, attempts: int) -> float:
        base = max(30.0, float(self.config.orphan_recovery_probe_interval_sec))
        max_backoff = max(base, float(self.config.orphan_recovery_max_backoff_sec))
        # 指数退避：base, 2*base, 4*base...
        delay = base * (2 ** max(0, int(attempts) - 1))
        return max(30.0, min(float(delay), max_backoff))

    def _get_official_orphan_terminal_market_state(
        self, token_id: str
    ) -> Tuple[Optional[str], Optional[MarketState]]:
        if not token_id or not self._market_state_checker:
            return None, None
        condition_id = self._get_condition_id_for_token(token_id)
        if not condition_id:
            return None, None
        try:
            market_state = self._market_state_checker.check_market_state(
                condition_id,
                token_id,
                use_cache=False,
            )
        except Exception as exc:
            print(
                "[ORPHAN][WARN] official market check failed: "
                f"token={token_id[:16]}... error={exc}"
            )
            return None, None
        if not market_state.is_permanently_closed:
            return None, market_state
        reason_map = {
            MarketStatus.CLOSED: "MARKET_CLOSED",
            MarketStatus.RESOLVED: "MARKET_RESOLVED",
            MarketStatus.ARCHIVED: "MARKET_ARCHIVED",
            MarketStatus.NOT_FOUND: "MARKET_NOT_FOUND",
        }
        return reason_map.get(market_state.status, "MARKET_TERMINAL_DETECTED"), market_state

    def _run_orphan_recovery_probe(self, now: float) -> None:
        latest = self._load_latest_orphan_states()
        if not latest:
            return
        due_tokens: List[Tuple[str, Dict[str, Any]]] = []
        for token_id, state in latest.items():
            status = str(state.get("status") or "orphaned").strip().lower()
            if status in {"recovered", "manual_only"}:
                continue
            next_probe_at = _coerce_float(state.get("next_probe_at"))
            if next_probe_at is None:
                due_tokens.append((token_id, state))
                continue
            if next_probe_at > 0 and float(next_probe_at) <= now:
                due_tokens.append((token_id, state))
        if not due_tokens:
            return

        try:
            copytrade_active = self._build_copytrade_active_token_set()
        except Exception as exc:
            print(f"[ORPHAN][WARN] recovery probe skipped: copytrade read failed: {exc}")
            return
        pos_snapshot, pos_info = self._refresh_sell_position_snapshot()
        sell_signals = self._load_copytrade_sell_signals()

        recovered = 0
        failed = 0
        for token_id, state in due_tokens:
            attempts = max(0, int(_coerce_float(state.get("probe_attempts")) or 0))
            next_attempt = attempts + 1
            reason = ""

            tracked = (
                token_id in self.tasks
                or token_id in self.pending_topics
                or token_id in self.pending_burst_topics
                or token_id in self.pending_exit_topics
            )
            if tracked:
                continue
            if token_id not in copytrade_active:
                reason = "copytrade_not_active"
            elif token_id in sell_signals:
                reason = "sell_signal_active"
            else:
                self._hydrate_topic_metadata_for_blacklist(token_id)
                market_reason, market_state = self._get_official_orphan_terminal_market_state(
                    token_id
                )
                if market_reason:
                    condition_id = self._get_condition_id_for_token(token_id)
                    if self._market_closed_cleaner and condition_id:
                        try:
                            copytrade_state_path = (
                                self.config.copytrade_tokens_path.parent / "copytrade_state.json"
                            )
                            self._market_closed_cleaner.clean_closed_market(
                                token_id=token_id,
                                condition_id=condition_id,
                                exit_reason=market_reason,
                                copytrade_file=str(self.config.copytrade_tokens_path),
                                copytrade_state_file=str(copytrade_state_path)
                                if copytrade_state_path.exists()
                                else None,
                                exit_tokens_file=str(self._exit_tokens_path),
                            )
                        except Exception as exc:
                            print(
                                "[ORPHAN][WARN] terminal market cleanup failed: "
                                f"token={token_id[:16]}... error={exc}"
                            )
                    self._append_orphan_token_record(
                        {
                            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                            "updated_ts": float(now),
                            "token_id": token_id,
                            "status": "manual_only",
                            "probe_attempts": int(next_attempt),
                            "next_probe_at": 0.0,
                            "reason": market_reason,
                            "trigger_source": "orphan_recovery_probe",
                            "note": (
                                f"official_market_status="
                                f"{market_state.status.value if market_state else 'unknown'}"
                            ),
                        }
                    )
                    failed += 1
                    continue
                if pos_info != "ok":
                    reason = f"positions_unavailable:{pos_info}"
                else:
                    pos_size = float(pos_snapshot.get(token_id, 0.0) or 0.0)
                    if not self._has_actionable_position(token_id, pos_size):
                        self._append_orphan_token_record(
                            {
                                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                                "updated_ts": float(now),
                                "token_id": token_id,
                                "status": "manual_only",
                                "probe_attempts": int(next_attempt),
                                "next_probe_at": 0.0,
                                "reason": "POSITIONS_NO_POSITION",
                                "trigger_source": "orphan_recovery_probe",
                                "note": "official_positions_absent",
                            }
                        )
                        failed += 1
                        continue

            if not reason:
                title_policy = self._enforce_title_blacklist_policy(
                    token_id, source="orphan_recovery_probe"
                )
                if title_policy in {"blocked_no_position", "force_liquidate", "force_sell_only"}:
                    reason = f"title_policy:{title_policy}"
            if not reason and self._stoploss_is_market_closed(token_id):
                reason = "market_closed_or_resolved"

            if reason:
                if next_attempt >= int(self.config.orphan_recovery_max_attempts):
                    self._append_orphan_token_record(
                        {
                            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                            "updated_ts": float(now),
                            "token_id": token_id,
                            "status": "manual_only",
                            "probe_attempts": int(next_attempt),
                            "next_probe_at": 0.0,
                            "reason": str(state.get("reason") or ""),
                            "trigger_source": "orphan_recovery_probe",
                            "note": f"max_attempts_reached last_reason={reason}",
                        }
                    )
                else:
                    delay = self._orphan_probe_delay_sec(next_attempt)
                    self._append_orphan_token_record(
                        {
                            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                            "updated_ts": float(now),
                            "token_id": token_id,
                            "status": "probe_failed",
                            "probe_attempts": int(next_attempt),
                            "next_probe_at": float(now + delay),
                            "reason": str(state.get("reason") or ""),
                            "trigger_source": "orphan_recovery_probe",
                            "note": reason,
                        }
                    )
                failed += 1
                continue

            detail = self.topic_details.setdefault(token_id, {})
            detail.pop("orphaned", None)
            detail.pop("orphaned_at", None)
            detail.pop("orphan_reason", None)
            detail.pop("orphan_trigger_source", None)
            detail["queue_role"] = "orphan_recovery"
            detail["schedule_lane"] = "base"
            self._remove_pending_exit_topic(token_id)
            self._enqueue_pending_topic(token_id)
            self._append_orphan_token_record(
                {
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                    "updated_ts": float(now),
                    "token_id": token_id,
                    "status": "recovered",
                    "probe_attempts": int(next_attempt),
                    "next_probe_at": 0.0,
                    "reason": str(state.get("reason") or ""),
                    "trigger_source": "orphan_recovery_probe",
                    "note": "requeued_to_maker",
                }
            )
            recovered += 1

        if recovered or failed:
            print(
                "[ORPHAN][PROBE] "
                f"due={len(due_tokens)} recovered={recovered} failed={failed}"
            )

    def _mark_token_orphaned(
        self,
        token_id: str,
        *,
        reason: str,
        trigger_source: str,
        task: Optional[TopicTask] = None,
        note: str = "",
        position_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not token_id:
            return
        now = time.time()
        had_stoploss_owner = str(token_id) in self._stoploss_reentry_states
        if had_stoploss_owner:
            self._remove_stoploss_reentry_state(token_id)
            self._save_stoploss_reentry_states()
        detail = self.topic_details.setdefault(token_id, {})
        detail["orphaned"] = True
        detail["orphaned_at"] = float(now)
        detail["orphan_reason"] = str(reason)
        detail["orphan_trigger_source"] = str(trigger_source)
        self._sell_exit_deadlines.pop(token_id, None)
        self._remove_pending_topic(token_id)
        self._remove_pending_exit_topic(token_id)
        self._clear_reentry_eligible_token(token_id, reason="orphaned")
        self._update_sell_signal_event(
            token_id,
            status="orphaned",
            note=f"source={trigger_source} reason={reason}",
        )
        active_task = task or self.tasks.get(token_id)
        if active_task and active_task.is_running():
            active_task.no_restart = True
            active_task.end_reason = "orphan detached"
            active_task.heartbeat(f"orphaned: {reason}")
            self._terminate_task(active_task, reason=f"orphaned ({reason})")
        if active_task and not active_task.is_running():
            self.tasks.pop(token_id, None)
        if position_snapshot is None:
            pos_snapshot, pos_info = self._refresh_sell_position_snapshot()
            position_snapshot = {
                "size": float(pos_snapshot.get(token_id, 0.0) or 0.0),
                "snapshot_info": pos_info,
            }
        record = {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "updated_ts": float(now),
            "token_id": token_id,
            "reason": str(reason),
            "trigger_reason": str(reason),
            "trigger_path": "orphan",
            "trigger_source": str(trigger_source),
            "note": str(note or ""),
            "status": "orphaned",
            "probe_attempts": 0,
            "next_probe_at": float(now + self._orphan_probe_delay_sec(1)),
            "position_snapshot": position_snapshot or {},
        }
        self._append_orphan_token_record(record)
        invalidated_refill_records = 0
        if self._is_terminal_exit_reason(reason):
            invalidated_refill_records = self._invalidate_refill_records_for_token(
                token_id,
                block_reason=str(reason),
                block_source="terminal_orphan",
            )
            self._refill_retry_counts.pop(token_id, None)
        print(
            f"[ORPHAN] token={token_id[:20]}... reason={reason} source={trigger_source} "
            f"note={note or '-'} stoploss_owner_cleared={had_stoploss_owner} "
            f"refill_records_blocked={invalidated_refill_records}"
        )

    def _unified_position_cycle_interval_sec(self) -> float:
        # Unified full-position cycle for stoploss + sell-signal full recheck.
        return 60.0

    def _refresh_unified_position_snapshot(
        self,
        *,
        force_refresh: bool = False,
        now: Optional[float] = None,
    ) -> tuple[List[Dict[str, Any]], Dict[str, float], str, str]:
        ts_now = float(now if now is not None else time.time())
        ttl = max(10.0, float(self._unified_position_cycle_interval_sec()))
        if (
            not force_refresh
            and self._unified_position_info == "ok"
            and (ts_now - float(self._unified_position_ts or 0.0)) <= ttl
        ):
            return (
                list(self._unified_position_rows),
                dict(self._unified_position_snapshot),
                "ok",
                "shared_cache",
            )

        if not self._position_address:
            address, origin = _resolve_position_address_from_env()
            self._position_address = address
            self._position_address_origin = origin
            if not address and not self._position_address_warned:
                self._position_address_warned = True
                print(f"[COPYTRADE][WARN] {origin}")

        if not self._position_address:
            return [], {}, "缺少地址，无法查询持仓。", "missing_address"

        rows, info = _fetch_position_rows_from_data_api(self._position_address)
        if info != "ok":
            # Prefer stale-ok snapshot over hard failure to reduce transient jitter impact.
            if (
                self._unified_position_info == "ok"
                and (ts_now - float(self._unified_position_ts or 0.0)) <= ttl * 2.0
            ):
                return (
                    list(self._unified_position_rows),
                    dict(self._unified_position_snapshot),
                    "ok",
                    f"shared_stale_cache:{info}",
                )
            return [], {}, info, "live_error"

        snapshot: Dict[str, float] = {}
        for row in rows:
            token_id = _extract_position_token_id(row)
            if not token_id:
                continue
            snapshot[token_id] = float(_extract_position_size(row) or 0.0)

        self._unified_position_rows = list(rows)
        self._unified_position_snapshot = dict(snapshot)
        self._unified_position_info = "ok"
        self._unified_position_ts = ts_now
        return list(rows), dict(snapshot), "ok", "live"

    def _refresh_sell_position_snapshot(self) -> tuple[Dict[str, float], str]:
        _rows, snapshot, info, source = self._refresh_unified_position_snapshot(force_refresh=False)
        if info == "ok":
            print(
                "[COPYTRADE][INFO] SELL 持仓快照已刷新: "
                f"positions={len(snapshot)} source={source}"
            )
        else:
            print(f"[COPYTRADE][INFO] SELL 持仓快照刷新失败 info={info} source={source}")
        return snapshot, info

    def _cleanup_old_logs(self) -> None:
        """清理7天前的日志文件，每天只执行一次"""
        today = time.strftime("%Y-%m-%d")
        if self._last_cleanup_date == today:
            # 今天已经清理过，跳过
            return

        # 计算截止时间（7天前）
        cutoff_ts = time.time() - (self._log_retention_days * 24 * 3600)

        # 要清理的目录列表
        cleanup_dirs = [
            self.config.data_dir,
            self.config.log_dir,
        ]

        total_deleted = 0
        total_size_freed = 0

        for dir_path in cleanup_dirs:
            if not dir_path.exists():
                continue
            try:
                deleted, size_freed = self._cleanup_directory(dir_path, cutoff_ts)
                total_deleted += deleted
                total_size_freed += size_freed
            except Exception as exc:
                print(f"[LOG_CLEANUP][WARN] 清理目录 {dir_path} 时出错: {exc}")

        self._last_cleanup_date = today

        if total_deleted > 0:
            size_mb = total_size_freed / (1024 * 1024)
            print(f"[LOG_CLEANUP] 清理完成: 删除 {total_deleted} 个文件，释放 {size_mb:.2f} MB")
        else:
            print(f"[LOG_CLEANUP] 检查完成，无需清理（保留 {self._log_retention_days} 天内的文件）")

    def _cleanup_directory(self, dir_path: Path, cutoff_ts: float) -> tuple:
        """递归清理指定目录中的旧文件，返回 (删除文件数, 释放字节数)"""
        deleted_count = 0
        size_freed = 0

        # 保护列表：这些文件/目录不应被删除
        protected_names = {
            "handled_topics.json",
            "autorun_status.json",
            "exit_tokens.json",
            "ws_cache.json",
            ".gitkeep",
        }

        try:
            for item in dir_path.rglob("*"):
                if not item.is_file():
                    continue

                # 跳过受保护的文件
                if item.name in protected_names:
                    continue

                # 只清理日志文件和临时文件
                # 清理的文件类型：.log, .log.*, .json (非保护), .tmp
                suffix_lower = item.suffix.lower()
                name_lower = item.name.lower()

                # 判断是否为可清理的文件类型
                is_log_file = (
                    suffix_lower == ".log"
                    or ".log." in name_lower  # 如 xxx.log.1, xxx.log.2024-01-01
                    or suffix_lower == ".tmp"
                )

                # 对于 data 目录下的 JSON 文件，只清理非保护的
                is_old_json = (
                    suffix_lower == ".json"
                    and item.name not in protected_names
                    and "data" in str(item.parent)
                )

                if not (is_log_file or is_old_json):
                    continue

                try:
                    mtime = item.stat().st_mtime
                    if mtime < cutoff_ts:
                        file_size = item.stat().st_size
                        item.unlink()
                        deleted_count += 1
                        size_freed += file_size
                except OSError:
                    # 文件可能正在被使用，跳过
                    pass

        except Exception as exc:
            print(f"[LOG_CLEANUP][WARN] 遍历目录 {dir_path} 时出错: {exc}")

        # 清理空目录
        try:
            for item in sorted(dir_path.rglob("*"), reverse=True):
                if item.is_dir():
                    try:
                        # 尝试删除空目录
                        if not any(item.iterdir()):
                            item.rmdir()
                    except OSError:
                        pass
        except Exception:
            pass

        return deleted_count, size_freed

    def _cleanup_all_tasks(self) -> None:
        for task in list(self.tasks.values()):
            if task.is_running():
                print(f"[CLEAN] 停止 topic={task.topic_id} ...")
                self._terminate_task(task, reason="cleanup")
        # 写回 handled_topics，确保最新状态落盘
        write_handled_topics(self.config.handled_topics_path, self.handled_topics)

    def _reset_all_runtime_state(self) -> None:
        """将调度器状态清零到初始空白态，并立即落盘。"""
        self.pending_topics.clear()
        self.pending_burst_topics.clear()
        self.pending_exit_topics.clear()
        self.tasks.clear()
        self.handled_topics.clear()
        self.topic_details.clear()
        self.latest_topics = []

        self._handled_sell_signals.clear()
        self._completed_exit_cleanup_tokens.clear()
        self._exit_cleanup_retry_counts.clear()
        self._sell_exit_deadlines.clear()
        self._startup_orphan_profit_sweep_done = False

        self._pending_first_seen.clear()
        self._refilled_tokens.clear()
        self._refill_retry_counts.clear()
        self._title_blacklist_finalized_no_position.clear()

        self._shared_ws_wait_failures.clear()
        self._shared_ws_paused_until.clear()
        self._shared_ws_wait_timeout_events.clear()
        self._active_unmanaged_rearm_last_ts.clear()
        self._clob_book_probe_cache.clear()
        self._gamma_market_probe_cache.clear()
        self._shared_ws_pending_since.clear()
        self._price_cap_stuck_since.clear()
        self._price_cap_flat_stuck_since.clear()
        self._shock_stuck_since.clear()
        self._task_run_config_cache.clear()
        self._seen_self_sell_trade_keys.clear()
        self._seen_self_sell_tokens.clear()
        self._active_unmanaged_rearm_recent_ts.clear()
        self._active_unmanaged_rearm_suppressed_until.clear()
        self._process_started_at = time.time()
        self._last_self_sell_trade_ts = int(self._process_started_at)
        self._reentry_eligible_tokens.clear()
        self._reentry_eligible_reasons.clear()
        self._ws_reconnect_reason_counts = {"closed": 0, "error": 0, "silence": 0}
        self._ws_last_reconnect_reason = None
        self._ws_last_reconnect_detail = {}

        self._ws_cache.clear()
        self._ws_token_ids = []

        self._dump_runtime_status()

    def _restore_runtime_status(self) -> None:
        """尝试从上次运行的状态文件恢复待处理队列等信息。
        
        【修复】不再直接恢复 handled_topics，只恢复实际有任务在运行的 token。
        防止程序重启后，handled_topics 包含大量无运行任务的 token，导致无法启动新管理。
        """

        if not self.status_path.exists():
            return
        try:
            payload = _load_json_file(self.status_path)
            pending_topics = payload.get("pending_topics") or []
            tasks_snapshot = payload.get("tasks") or {}
            topic_details_snapshot = payload.get("topic_details") or {}
            pending_exit_topics = payload.get("pending_exit_topics") or []
        except Exception as exc:  # pragma: no cover - 容错
            print(f"[WARN] 无法读取运行状态文件，已忽略: {exc}")
            return
        
        # 【修复】只恢复有实际运行任务的 token 到 handled_topics
        # 其他的 token 应该通过 _sync_handled_topics_on_startup 重新评估
        active_tokens_from_snapshot = set(str(t) for t in pending_topics) | set(str(t) for t in tasks_snapshot.keys())
        if active_tokens_from_snapshot:
            self.handled_topics.update(active_tokens_from_snapshot)
            print(f"[RESTORE] 从运行状态恢复 {len(active_tokens_from_snapshot)} 个活跃 token 到 handled_topics")

        # ===== 构建黑名单：确定已死亡的 token =====
        # 过滤已确认 terminal 的 token，避免 runtime restore 把它们重新拉回普通路径。
        dead_tokens: set = set()
        for record in self._load_exit_tokens():
            if self._is_terminal_exit_reason(record.get("exit_reason")):
                tid = record.get("token_id")
                if tid:
                    dead_tokens.add(str(tid))

        restored_topics: List[str] = []
        skipped_dead: List[str] = []
        skipped_owner: List[str] = []
        orphan_states = self._load_latest_orphan_states()
        pending_exit_snapshot = {
            str(topic_id) for topic_id in pending_exit_topics if str(topic_id).strip()
        }

        if isinstance(topic_details_snapshot, dict):
            with self._topic_details_lock:
                for topic_id, saved in topic_details_snapshot.items():
                    tid = str(topic_id).strip()
                    if not tid or not isinstance(saved, dict):
                        continue
                    detail = self.topic_details.setdefault(tid, {})
                    if saved.get("schedule_lane"):
                        detail["schedule_lane"] = saved.get("schedule_lane")
                    if saved.get("queue_role"):
                        detail["queue_role"] = saved.get("queue_role")
                    if saved.get("startup_orphan_profit_sweep"):
                        detail["startup_orphan_profit_sweep"] = True

        for topic_id in pending_topics:
            topic_id = str(topic_id)
            # 跳过确定已死亡的 token（市场已关闭）
            if topic_id in dead_tokens:
                skipped_dead.append(topic_id)
                continue
            if self._has_runtime_owner_blocking_plain_restore(
                topic_id,
                orphan_states=orphan_states,
                pending_exit_topics=pending_exit_snapshot,
            ):
                skipped_owner.append(topic_id)
                continue
            # 只检查是否已在 pending 队列中，不检查 handled_topics
            # 因为保存的 pending_topics 代表"还未完成的任务"，即使在 handled 中也应恢复
            if topic_id in self.pending_topics:
                continue
            self._hydrate_topic_metadata_for_blacklist(topic_id)
            title_policy = self._enforce_title_blacklist_policy(
                topic_id, source="restore_runtime"
            )
            if title_policy in {"blocked_no_position", "force_liquidate", "force_sell_only"}:
                continue
            restored_topics.append(topic_id)
            # 【修复】设置 queue_role 以便状态显示
            detail = self.topic_details.setdefault(topic_id, {})
            detail["queue_role"] = "restored_token"
            self._enqueue_pending_topic(topic_id)

        for topic_id, info in tasks_snapshot.items():
            topic_id = str(topic_id)
            # 跳过确定已死亡的 token（市场已关闭）
            if topic_id in dead_tokens:
                if topic_id not in skipped_dead:
                    skipped_dead.append(topic_id)
                continue
            if self._has_runtime_owner_blocking_plain_restore(
                topic_id,
                orphan_states=orphan_states,
                pending_exit_topics=pending_exit_snapshot,
            ):
                if topic_id not in skipped_owner:
                    skipped_owner.append(topic_id)
                continue
            # 不检查 handled_topics，因为保存的 tasks 代表"上次运行中的任务"
            # 即使在 handled 中也应恢复，以确保任务不丢失
            if topic_id not in self.pending_topics:
                self._hydrate_topic_metadata_for_blacklist(topic_id)
                title_policy = self._enforce_title_blacklist_policy(
                    topic_id, source="restore_runtime"
                )
                if title_policy in {
                    "blocked_no_position",
                    "force_liquidate",
                    "force_sell_only",
                }:
                    continue
                restored_topics.append(topic_id)
                detail = self.topic_details.setdefault(topic_id, {})
                detail["queue_role"] = "restored_token"
                self._enqueue_pending_topic(topic_id)

            task = TopicTask(topic_id=topic_id)
            task.status = "pending"
            task.notes.append("restored from runtime_status")
            config_path = info.get("config_path")
            log_path = info.get("log_path")
            if config_path:
                task.config_path = Path(config_path)
            if log_path:
                task.log_path = Path(log_path)
            self.tasks[topic_id] = task

        if skipped_dead:
            preview = ", ".join(t[:8] + "..." for t in skipped_dead[:5])
            print(f"[RESTORE] 跳过 {len(skipped_dead)} 个已关闭市场的 token: {preview}")

        if restored_topics:
            preview = ", ".join(restored_topics[:5])
            print(f"[RESTORE] 已从运行状态恢复 {len(restored_topics)} 个话题：{preview}")
        for topic_id in pending_exit_topics:
            topic_id = str(topic_id)
            if topic_id in self.pending_exit_topics:
                continue
            self.pending_exit_topics.append(topic_id)

        pending_burst_topics = payload.get("pending_burst_topics") or []
        restored_burst_to_base = 0
        for topic_id in pending_burst_topics:
            topic_id = str(topic_id)
            if not topic_id:
                continue
            if topic_id in dead_tokens:
                continue
            if self._has_runtime_owner_blocking_plain_restore(
                topic_id,
                orphan_states=orphan_states,
                pending_exit_topics=pending_exit_snapshot,
            ):
                if topic_id not in skipped_owner:
                    skipped_owner.append(topic_id)
                continue
            self._hydrate_topic_metadata_for_blacklist(topic_id)
            title_policy = self._enforce_title_blacklist_policy(
                topic_id, source="restore_runtime"
            )
            if title_policy in {
                "blocked_no_position",
                "force_liquidate",
                "force_sell_only",
            }:
                continue
            # 避免历史残留 burst 队列在重启后直接把额外槽位占满。
            # 【修复】设置 queue_role 以便状态显示
            detail = self.topic_details.setdefault(topic_id, {})
            detail["queue_role"] = "restored_token"
            self._enqueue_pending_topic(topic_id)
            restored_burst_to_base += 1
        if restored_burst_to_base:
            print(
                f"[RESTORE] 已将 {restored_burst_to_base} 个历史 burst token 降级为普通 pending"
            )
        if skipped_owner:
            preview = ", ".join(t[:8] + "..." for t in skipped_owner[:5])
            print(f"[RESTORE] 跳过 {len(skipped_owner)} 个已有专属 owner 的 token: {preview}")

        handled_sell_signals = payload.get("handled_sell_signals") or []
        self._handled_sell_signals = {
            str(token_id) for token_id in handled_sell_signals if str(token_id).strip()
        }

        completed_exit_cleanup_tokens = payload.get("completed_exit_cleanup_tokens") or []
        self._completed_exit_cleanup_tokens = {
            str(token_id)
            for token_id in completed_exit_cleanup_tokens
            if str(token_id).strip()
        }

        exit_cleanup_retry_counts = payload.get("exit_cleanup_retry_counts") or {}
        for token_id, count in exit_cleanup_retry_counts.items():
            token_id = str(token_id)
            try:
                self._exit_cleanup_retry_counts[token_id] = int(count)
            except (TypeError, ValueError):
                pass

        reentry_eligible_tokens = payload.get("reentry_eligible_tokens") or []
        self._reentry_eligible_tokens = {
            str(token_id)
            for token_id in reentry_eligible_tokens
            if str(token_id).strip()
        }
        reentry_eligible_reasons = payload.get("reentry_eligible_reasons") or {}
        if isinstance(reentry_eligible_reasons, dict):
            self._reentry_eligible_reasons = {
                str(k): str(v)
                for k, v in reentry_eligible_reasons.items()
                if str(k).strip()
            }
        active_unmanaged_rearm_blocked_until = (
            payload.get("active_unmanaged_rearm_blocked_until") or {}
        )
        if isinstance(active_unmanaged_rearm_blocked_until, dict):
            for token_id, until_ts in active_unmanaged_rearm_blocked_until.items():
                token_id = str(token_id).strip()
                if not token_id:
                    continue
                try:
                    until_val = float(until_ts or 0.0)
                except (TypeError, ValueError):
                    continue
                if until_val > time.time():
                    self._active_unmanaged_rearm_blocked_until[token_id] = until_val
        active_unmanaged_rearm_block_reasons = (
            payload.get("active_unmanaged_rearm_block_reasons") or {}
        )
        if isinstance(active_unmanaged_rearm_block_reasons, dict):
            self._active_unmanaged_rearm_block_reasons = {
                str(k): str(v)
                for k, v in active_unmanaged_rearm_block_reasons.items()
                if str(k).strip()
            }
        self._cleanup_active_unmanaged_rearm_blocks()

        ws_reconnect_reason_counts = payload.get("ws_reconnect_reason_counts") or {}
        if isinstance(ws_reconnect_reason_counts, dict):
            for reason in ("closed", "error", "silence"):
                try:
                    self._ws_reconnect_reason_counts[reason] = int(
                        ws_reconnect_reason_counts.get(reason, 0) or 0
                    )
                except (TypeError, ValueError):
                    self._ws_reconnect_reason_counts[reason] = 0
        self._ws_last_reconnect_reason = str(
            payload.get("ws_last_reconnect_reason") or ""
        ).strip() or None
        ws_last_reconnect_detail = payload.get("ws_last_reconnect_detail") or {}
        self._ws_last_reconnect_detail = (
            dict(ws_last_reconnect_detail)
            if isinstance(ws_last_reconnect_detail, dict)
            else {}
        )

    def _dump_runtime_status(self) -> None:
        self._cleanup_active_unmanaged_rearm_blocks()
        # GC: 清理 _completed_exit_cleanup_tokens 中不再活跃的条目
        # 仅保留仍在 sell 信号文件、pending 队列或 tasks 中的 token
        active_tokens = (
            set(self.pending_topics)
            | set(self.pending_burst_topics)
            | set(self.pending_exit_topics)
            | set(self.tasks.keys())
            | self._handled_sell_signals
        )
        stale = self._completed_exit_cleanup_tokens - active_tokens
        if stale:
            self._completed_exit_cleanup_tokens -= stale
            for tk in stale:
                self._exit_cleanup_retry_counts.pop(tk, None)
        
        # 【修复】包含 topic_details 中的 schedule_lane 信息，供子进程同步
        topic_details_export = {}
        with self._topic_details_lock:
            for topic_id, detail in self.topic_details.items():
                if topic_id in self.tasks:  # 只导出运行中的任务
                    topic_details_export[topic_id] = {
                        "schedule_lane": detail.get("schedule_lane", "base"),
                        "queue_role": detail.get("queue_role"),
                    }
        
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "handled_topics_total": len(self.handled_topics),
            "handled_topics": sorted(self.handled_topics),
            "pending_topics": list(self.pending_topics),
            "pending_burst_topics": list(self.pending_burst_topics),
            "pending_exit_topics": list(self.pending_exit_topics),
            "handled_sell_signals": sorted(self._handled_sell_signals),
            "completed_exit_cleanup_tokens": sorted(
                self._completed_exit_cleanup_tokens
            ),
            "exit_cleanup_retry_counts": dict(self._exit_cleanup_retry_counts),
            "reentry_eligible_tokens": sorted(self._reentry_eligible_tokens),
            "reentry_eligible_reasons": dict(self._reentry_eligible_reasons),
            "active_unmanaged_rearm_blocked_until": dict(
                self._active_unmanaged_rearm_blocked_until
            ),
            "active_unmanaged_rearm_block_reasons": dict(
                self._active_unmanaged_rearm_block_reasons
            ),
            "ws_reconnect_reason_counts": dict(self._ws_reconnect_reason_counts),
            "ws_last_reconnect_reason": self._ws_last_reconnect_reason,
            "ws_last_reconnect_detail": dict(self._ws_last_reconnect_detail),
            "token_cycle_state_total": len(self._token_cycle_states),
            "tasks": {},
            "topic_details": topic_details_export,  # 【修复】添加 topic_details 供子进程读取
        }
        for topic_id, task in self.tasks.items():
            payload["tasks"][topic_id] = {
                "status": task.status,
                "pid": task.process.pid if task.process else None,
                "last_heartbeat": task.last_heartbeat,
                "notes": task.notes,
                "log_path": str(task.log_path) if task.log_path else None,
                "config_path": str(task.config_path) if task.config_path else None,
            }
        _dump_json_file(self.status_path, payload)
        print(f"[STATE] 已写入运行状态到 {self.status_path}")

    # ========== 入口方法 ==========
    def command_loop(self) -> None:
        try:
            prompt_shown = False
            while not self.stop_event.is_set():
                try:
                    if not prompt_shown:
                        # 主动刷新提示符，避免被后台日志刷屏覆盖
                        print("poly> ", end="", flush=True)
                        prompt_shown = True

                    ready, _, _ = select.select(
                        [sys.stdin], [], [], self.config.command_poll_sec
                    )
                    if not ready:
                        continue

                    line = sys.stdin.readline()
                    if line == "":
                        cmd = "exit"
                    else:
                        cmd = line.rstrip("\n")
                    prompt_shown = False
                except EOFError:
                    cmd = "exit"
                except Exception as exc:  # pragma: no cover - 保护交互循环不被意外异常终止
                    print(f"[ERROR] command loop input failed: {exc}")
                    traceback.print_exc()
                    time.sleep(self.config.command_poll_sec)
                    continue
                # 立刻反馈收到的命令，避免在日志刷屏时用户误以为命令未被捕获
                if cmd:
                    print(f"[CMD] received: {cmd}")
                else:
                    # 空行依旧入队，后续会在 _handle_command 里被忽略
                    print("[CMD] received: <empty>")
                self.enqueue_command(cmd)
                # 轻微休眠，防止输入为空或重复换行时产生过多提示刷屏
                time.sleep(self.config.command_poll_sec)
        except KeyboardInterrupt:
            print("\n[WARN] Ctrl+C detected, stopping...")
            self.stop_event.set()
        except Exception as exc:  # pragma: no cover - 防御性保护
            print(f"[ERROR] command loop crashed: {exc}")
            traceback.print_exc()


# =====================
# CLI 入口
# =====================

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket maker autorun")
    parser.add_argument(
        "--global-config",
        type=Path,
        default=MAKER_ROOT / "config" / "global_config.json",
        help="全局调度配置 JSON 路径",
    )
    parser.add_argument(
        "--strategy-config",
        type=Path,
        default=MAKER_ROOT / "config" / "strategy_defaults.json",
        help="策略参数模板 JSON 路径",
    )
    parser.add_argument(
        "--run-config-template",
        type=Path,
        default=MAKER_ROOT / "config" / "run_params.json",
        help="运行参数模板 JSON 路径（传递给 Volatility_arbitrage_run.py）",
    )
    parser.add_argument(
        "--no-repl",
        action="store_true",
        help="禁用交互式命令循环，仅按配置运行",
    )
    parser.add_argument(
        "--command",
        action="append",
        help="启动后自动执行的命令（可多次提供），例如 list 或 stop <topic_id>",
    )
    return parser.parse_args(argv)


def load_configs(
    args: argparse.Namespace,
) -> tuple[GlobalConfig, Dict[str, Any], Dict[str, Any]]:
    global_conf_raw = _load_json_file(args.global_config)
    strategy_conf_raw = _load_json_file(args.strategy_config)
    run_params_template = _load_json_file(args.run_config_template)
    global_conf = GlobalConfig.from_dict(global_conf_raw)
    global_conf.global_config_path = args.global_config
    return (
        global_conf,
        strategy_conf_raw,
        run_params_template,
    )


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    global_conf, strategy_conf, run_params_template = load_configs(args)
    log_path = _setup_main_log(global_conf.log_dir)
    print("=" * 60)
    print("[INIT] Polymarket Maker AutoRun - 聚合器启动")
    print("[VERSION] 支持book/tick事件处理 (2026-01-21)")
    if log_path:
        print(f"[INIT] 主程序日志: {log_path}")
    print("=" * 60)

    manager = AutoRunManager(global_conf, strategy_conf, run_params_template)

    def _handle_sigterm(signum: int, frame: Any) -> None:  # pragma: no cover - 信号处理不可测
        print(f"\n[WARN] signal {signum} received, exiting...")
        manager.stop_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    worker = threading.Thread(target=manager.run_loop, daemon=True)
    worker.start()

    if args.command:
        for cmd in args.command:
            manager.enqueue_command(cmd)

    if args.no_repl or args.command:
        try:
            while worker.is_alive():
                time.sleep(global_conf.command_poll_sec)
        except KeyboardInterrupt:
            print("\n[WARN] Ctrl+C detected, stopping...")
            manager.stop_event.set()
    else:
        manager.command_loop()

    worker.join()


if __name__ == "__main__":
    main()
