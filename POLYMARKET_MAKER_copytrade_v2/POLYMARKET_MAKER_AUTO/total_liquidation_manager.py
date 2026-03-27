from __future__ import annotations

import importlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import urllib.parse
import urllib.request


@dataclass
class LiquidationConfig:
    enabled: bool = False
    min_interval_hours: float = 72.0
    idle_slot_ratio_threshold: float = 0.5
    idle_slot_duration_minutes: float = 120.0
    startup_grace_hours: float = 6.0
    no_trade_duration_minutes: float = 180.0
    min_free_balance: float = 20.0
    low_balance_force_hours: float = 6.0
    enable_low_balance_force_trigger: bool = True
    balance_poll_interval_sec: float = 120.0
    require_conditions: int = 2
    token_scope_mode: str = "copytrade"
    position_value_threshold: float = 3.0
    spread_threshold: float = 0.01
    maker_timeout_minutes: float = 20.0
    taker_slippage_bps: float = 30.0
    hard_reset_enabled: bool = True
    remove_logs: bool = True
    remove_json_state: bool = True
    task_stop_timeout_minutes: float = 5.0
    blacklist_enabled: bool = True
    execution_mode: str = "liquidate_then_restart"

    @classmethod
    def from_global_config(cls, cfg: Any) -> "LiquidationConfig":
        raw = getattr(cfg, "total_liquidation", None) or {}
        trigger = raw.get("trigger") or {}
        liquidation = raw.get("liquidation") or {}
        reset = raw.get("reset") or {}
        blacklist = raw.get("blacklist") or {}
        return cls(
            enabled=bool(raw.get("enable_total_liquidation", False)),
            execution_mode=str(raw.get("execution_mode", "liquidate_then_restart") or "liquidate_then_restart").strip().lower(),
            min_interval_hours=float(raw.get("min_interval_hours", 72.0)),
            idle_slot_ratio_threshold=float(trigger.get("idle_slot_ratio_threshold", 0.5)),
            idle_slot_duration_minutes=float(trigger.get("idle_slot_duration_minutes", 120.0)),
            startup_grace_hours=max(0.0, float(trigger.get("startup_grace_hours", 6.0))),
            no_trade_duration_minutes=float(trigger.get("no_trade_duration_minutes", 180.0)),
            min_free_balance=float(trigger.get("min_free_balance", 20.0)),
            low_balance_force_hours=max(0.0, float(trigger.get("low_balance_force_hours", 6.0))),
            enable_low_balance_force_trigger=bool(trigger.get("enable_low_balance_force_trigger", True)),
            balance_poll_interval_sec=max(5.0, float(trigger.get("balance_poll_interval_sec", 120.0))),
            require_conditions=max(1, int(trigger.get("require_conditions", 2))),
            token_scope_mode=str(liquidation.get("token_scope_mode", "copytrade") or "copytrade").strip().lower(),
            position_value_threshold=float(liquidation.get("position_value_threshold", 3.0)),
            spread_threshold=float(liquidation.get("spread_threshold", 0.01)),
            maker_timeout_minutes=float(liquidation.get("maker_timeout_minutes", 20.0)),
            taker_slippage_bps=float(liquidation.get("taker_slippage_bps", 30.0)),
            hard_reset_enabled=bool(reset.get("hard_reset_enabled", True)),
            remove_logs=bool(reset.get("remove_logs", True)),
            remove_json_state=bool(reset.get("remove_json_state", True)),
            task_stop_timeout_minutes=max(0.5, float(liquidation.get("task_stop_timeout_minutes", 5.0))),
            blacklist_enabled=bool(blacklist.get("enabled", True)),
        )


class TotalLiquidationManager:
    """全局清仓管理器：监控活跃度 -> 触发清仓 -> 硬重置。"""

    _ALLOWED_SINGLE_TOKEN_IOC_REASONS = {"COPYTRADE_SELL", "STOPLOSS_REENTRY"}
    _COLLATERAL_DECIMALS = 6
    _TOKEN_SCOPE_COPYTRADE = "copytrade"
    _TOKEN_SCOPE_ALL_POSITIONS = "all_positions"
    _EXECUTION_MODE_LIQUIDATE_THEN_RESTART = "liquidate_then_restart"
    _EXECUTION_MODE_RESTART_ONLY = "restart_only"

    def __init__(self, cfg: Any, project_root: Path):
        self.cfg = LiquidationConfig.from_global_config(cfg)
        self.project_root = project_root
        self.state_path = cfg.data_dir / "total_liquidation_state.json"
        self.blacklist_path = project_root.parent / "copytrade" / "liquidation_blacklist.json"
        self._running = False

        self._idle_since: Optional[float] = None
        self._last_trade_activity_ts: float = time.time()
        self._last_fill_activity_ts: float = time.time()

        self._state = self._load_state()
        self._started_at_ts: float = time.time()

        self._cached_client: Optional[Any] = None
        self._next_client_retry_at: float = 0.0
        self._cached_free_balance: Optional[float] = None
        self._low_balance_since: Optional[float] = None
        self._next_balance_probe_at: float = 0.0
        self._last_balance_probe_error: Optional[str] = None
        self._last_positions_fetch_error: Optional[str] = None
        self._task_activity_markers: Dict[str, str] = {}
        self._task_fill_markers: Dict[str, str] = {}
        self._last_api_trade_probe_error: Optional[str] = None
        self._next_trade_probe_at: float = 0.0

    _TRADE_ACTIVITY_HINTS = (
        "[maker][buy] 挂单",
        "[maker][sell] 挂单",
        "挂单成功",
        "撤单",
        "下单",
    )

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _save_state(self, payload: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self._state = dict(payload)

    @classmethod
    def _normalize_ioc_reason(cls, reason: Any) -> str:
        return str(reason or "").strip().upper()

    def _record_blocked_single_token_ioc(
        self,
        autorun: Any,
        token_id: str,
        *,
        requested_reason: str,
        target_size: Optional[float],
    ) -> None:
        append_exit_record = getattr(autorun, "_append_exit_token_record", None)
        if not callable(append_exit_record):
            return
        try:
            append_exit_record(
                token_id,
                "IOC_BLOCKED_NON_WHITELIST",
                exit_data={
                    "source": "total_liquidation_single_token_taker",
                    "requested_reason": requested_reason or "UNSPECIFIED",
                    "requested_target_size": (
                        float(target_size) if target_size is not None else None
                    ),
                    "allowed_reasons": sorted(self._ALLOWED_SINGLE_TOKEN_IOC_REASONS),
                },
                refillable=False,
            )
        except Exception as exc:
            print(
                "[GLB_LIQ][WARN] failed to persist blocked IOC audit: "
                f"token={token_id[:20]}... error={exc}"
            )

    def _get_last_trigger_ts(self) -> float:
        value = self._state.get("last_trigger_ts")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def update_metrics(self, autorun: Any) -> Dict[str, Any]:
        now = time.time()
        running = sum(1 for t in autorun.tasks.values() if t.is_running())
        max_slots = max(1, int(getattr(autorun.config, "max_concurrent_tasks", 1)))
        idle_ratio = max(0.0, min(1.0, 1.0 - (running / max_slots)))

        if idle_ratio >= self.cfg.idle_slot_ratio_threshold:
            if self._idle_since is None:
                self._idle_since = now
        else:
            self._idle_since = None

        latest_trade_activity, latest_fill_activity = self._collect_trade_activity_ts(autorun)
        api_fill_activity = self._query_recent_fill_activity_ts(autorun)
        if api_fill_activity > 0:
            latest_fill_activity = max(latest_fill_activity, api_fill_activity)

        if latest_trade_activity > 0:
            self._last_trade_activity_ts = max(self._last_trade_activity_ts, latest_trade_activity)
        if latest_fill_activity > 0:
            self._last_fill_activity_ts = max(self._last_fill_activity_ts, latest_fill_activity)

        free_balance = self._query_free_balance_usdc(autorun)
        if free_balance is not None and free_balance < self.cfg.min_free_balance:
            if self._low_balance_since is None:
                self._low_balance_since = now
        else:
            self._low_balance_since = None

        startup_grace_sec = max(0.0, self.cfg.startup_grace_hours * 3600.0)
        in_startup_grace = (now - self._started_at_ts) < startup_grace_sec

        idle_minutes = ((now - float(self._idle_since)) / 60.0) if self._idle_since is not None else 0.0
        no_trade_minutes = (now - float(self._last_fill_activity_ts or now)) / 60.0
        bal_text = "NA" if free_balance is None else f"{float(free_balance):.4f}"
        print(
            "[GLB_LIQ][METRICS] "
            f"running={running}/{max_slots} idle_ratio={idle_ratio:.2f} "
            f"idle_minutes={idle_minutes:.1f} no_trade_minutes={no_trade_minutes:.1f} "
            f"free_balance={bal_text}"
        )
        if self._last_balance_probe_error:
            print(f"[GLB_LIQ][WARN] 余额查询失败，沿用缓存: {self._last_balance_probe_error}")
        if self._last_api_trade_probe_error:
            print(f"[GLB_LIQ][WARN] 成交查询失败，沿用日志判定: {self._last_api_trade_probe_error}")

        return {
            "running": running,
            "max_slots": max_slots,
            "idle_ratio": idle_ratio,
            "idle_since": self._idle_since,
            "last_trade_activity_ts": self._last_trade_activity_ts,
            "last_fill_activity_ts": self._last_fill_activity_ts,
            "free_balance": free_balance,
            "low_balance_since": self._low_balance_since,
            "in_startup_grace": in_startup_grace,
        }

    def should_trigger(self, metrics: Dict[str, Any]) -> Tuple[bool, List[str]]:
        if not self.cfg.enabled or self._running:
            return False, []

        now = time.time()
        min_interval_sec = max(1.0, self.cfg.min_interval_hours * 3600.0)
        if now - self._get_last_trigger_ts() < min_interval_sec:
            return False, []

        reasons: List[str] = []

        in_startup_grace_flag = metrics.get("in_startup_grace")
        if in_startup_grace_flag is None:
            startup_grace_sec = max(0.0, self.cfg.startup_grace_hours * 3600.0)
            in_startup_grace = (now - self._started_at_ts) < startup_grace_sec
        else:
            in_startup_grace = bool(in_startup_grace_flag)

        idle_since = metrics.get("idle_since")
        if idle_since is not None and not in_startup_grace:
            idle_minutes = (now - float(idle_since)) / 60.0
            if idle_minutes >= self.cfg.idle_slot_duration_minutes:
                reasons.append(
                    f"idle_slots>={self.cfg.idle_slot_ratio_threshold:.2f} for {idle_minutes:.1f}m"
                )

        no_trade_minutes = (now - float(metrics.get("last_fill_activity_ts") or now)) / 60.0
        if no_trade_minutes >= self.cfg.no_trade_duration_minutes:
            reasons.append(f"no_trade_for={no_trade_minutes:.1f}m")

        free_balance = metrics.get("free_balance")
        if free_balance is not None and free_balance < self.cfg.min_free_balance:
            reasons.append(f"free_balance={free_balance:.4f}<min={self.cfg.min_free_balance:.4f}")

        if self.cfg.enable_low_balance_force_trigger:
            low_balance_since = metrics.get("low_balance_since")
            if low_balance_since is not None:
                low_balance_hours = (now - float(low_balance_since)) / 3600.0
                if low_balance_hours >= self.cfg.low_balance_force_hours:
                    reasons.append(
                        f"low_balance_for={low_balance_hours:.2f}h"
                        f">=force={self.cfg.low_balance_force_hours:.2f}h"
                    )
                    return True, reasons

        return len(reasons) >= self.cfg.require_conditions, reasons

    def _collect_trade_activity_ts(self, autorun: Any) -> Tuple[float, float]:
        latest_trade = 0.0
        latest_fill = 0.0
        active_ids: set[str] = set()

        for topic_id, task in (getattr(autorun, "tasks", {}) or {}).items():
            if not task or not getattr(task, "is_running", lambda: False)():
                continue
            active_ids.add(str(topic_id))

            excerpt = str(getattr(task, "log_excerpt", "") or "")
            if not excerpt:
                continue

            lines = [ln.strip() for ln in excerpt.strip().splitlines() if ln.strip()]
            if not lines:
                continue

            last_line = lines[-1]
            excerpt_ts = float(getattr(task, "last_log_excerpt_ts", 0.0) or 0.0)
            marker = f"{last_line}|{excerpt_ts:.3f}"
            if self._task_activity_markers.get(str(topic_id)) != marker:
                self._task_activity_markers[str(topic_id)] = marker
                normalized_last = last_line.lower()
                if any(hint in normalized_last for hint in self._TRADE_ACTIVITY_HINTS):
                    latest_trade = max(latest_trade, time.time())

            # 成交识别不只看最后一行：日志刷新的末行可能是状态行，导致漏记实际成交。
            fill_line = ""
            for ln in reversed(lines):
                normalized_ln = ln.lower()
                if self._line_has_real_fill_activity(normalized_ln):
                    fill_line = normalized_ln
                    break
            if fill_line:
                fill_marker = fill_line
                if self._task_fill_markers.get(str(topic_id)) != fill_marker:
                    self._task_fill_markers[str(topic_id)] = fill_marker
                    latest_fill = max(latest_fill, time.time())

        stale = [tid for tid in self._task_activity_markers.keys() if tid not in active_ids]
        for tid in stale:
            self._task_activity_markers.pop(tid, None)
            self._task_fill_markers.pop(tid, None)

        return latest_trade, latest_fill

    def _line_has_real_fill_activity(self, normalized_line: str) -> bool:
        """仅在显式出现正成交量字段时返回 True。"""
        if not normalized_line:
            return False

        # 严格口径：仅识别显式数量字段，且数值必须 > 0。
        for key in ("filled", "sold"):
            m = re.search(rf"\b{key}\s*=\s*([0-9]+(?:\.[0-9]+)?)", normalized_line)
            if m is None:
                continue
            try:
                if float(m.group(1)) > 0:
                    return True
            except (TypeError, ValueError):
                continue

        return False

    def _precheck_liquidation_ready(self) -> Tuple[Optional[str], Optional[Any], Optional[set[str]]]:
        client = self._get_cached_client()
        if client is None:
            return "client init failed", None, None
        if self.cfg.token_scope_mode == self._TOKEN_SCOPE_ALL_POSITIONS:
            return None, client, None
        allowed_token_ids = self._load_copytrade_token_scope()
        if not allowed_token_ids:
            return "copytrade token scope is empty; skip liquidation for safety", None, None
        return None, client, allowed_token_ids

    def execute(self, autorun: Any, reasons: List[str]) -> Dict[str, Any]:
        self._running = True
        start = time.time()
        print(f"[GLB_LIQ] 开始总清仓流程, reasons={reasons}")

        result: Dict[str, Any] = {
            "trigger_reasons": reasons,
            "liquidated": 0,
            "maker_count": 0,
            "taker_count": 0,
            "errors": [],
            "hard_reset": False,
        }

        try:
            if self.cfg.execution_mode == self._EXECUTION_MODE_RESTART_ONLY:
                now = time.time()
                state = {
                    "last_trigger_ts": now,
                    "last_trigger_reason": reasons,
                    "last_result": result,
                    "last_duration_sec": now - start,
                    "execution_mode": self.cfg.execution_mode,
                }
                self._save_state(state)
                print("[GLB_LIQ] 命中 restart_only 模式：跳过清仓与硬重置，仅请求重启 autorun")
                autorun.stop_event.set()
                return result

            precheck_error, prechecked_client, prechecked_token_scope = self._precheck_liquidation_ready()
            if precheck_error:
                result.update({"errors": [precheck_error], "aborted": True})
                now = time.time()
                self._save_state(
                    {
                        "last_abort_ts": now,
                        "last_abort_reason": reasons,
                        "last_result": result,
                        "last_duration_sec": now - start,
                    }
                )
                print(f"[GLB_LIQ][WARN] 预检失败，跳过总清仓: {precheck_error}")
                return result

            if hasattr(autorun, "_suspend_ws_updates"):
                autorun._suspend_ws_updates("total-liquidation")

            autorun._cleanup_all_tasks()
            wait_timeout_sec = max(30.0, self.cfg.task_stop_timeout_minutes * 60.0)
            tasks_stopped = self._wait_for_tasks_stopped(autorun, timeout_sec=wait_timeout_sec)
            if not tasks_stopped:
                msg = f"timeout waiting tasks to stop ({wait_timeout_sec:.0f}s)"
                result.update({"errors": [msg], "aborted": True})
                now = time.time()
                self._save_state(
                    {
                        "last_abort_ts": now,
                        "last_abort_reason": reasons,
                        "last_result": result,
                        "last_duration_sec": now - start,
                    }
                )
                print(f"[GLB_LIQ][WARN] 任务停止超时，终止总清仓: {msg}")
                return result

            autorun.pending_topics.clear()
            autorun.pending_exit_topics.clear()

            liquidation_stats = self._liquidate_positions(
                autorun,
                prechecked_client=prechecked_client,
                prechecked_token_scope=prechecked_token_scope,
            )
            result.update(liquidation_stats)

            blacklisted = 0
            if self.cfg.blacklist_enabled:
                liquidated_tokens = liquidation_stats.get("liquidated_tokens") or []
                blacklisted = self._append_blacklist_tokens(liquidated_tokens)
            result["blacklisted"] = blacklisted

            now = time.time()
            aborted = bool(liquidation_stats.get("aborted", False))
            if aborted:
                self._save_state(
                    {
                        "last_abort_ts": now,
                        "last_abort_reason": reasons,
                        "last_result": result,
                        "last_duration_sec": now - start,
                    }
                )
                print("[GLB_LIQ][WARN] 本次总清仓已中止，跳过硬重置与重启")
                return result

            state = {
                "last_trigger_ts": now,
                "last_trigger_reason": reasons,
                "last_result": result,
                "last_duration_sec": now - start,
            }
            self._save_state(state)

            if self.cfg.hard_reset_enabled:
                self._hard_reset_files(autorun)
                if hasattr(autorun, "_reset_all_runtime_state"):
                    try:
                        autorun._reset_all_runtime_state()
                    except Exception as exc:
                        print(f"[GLB_LIQ][WARN] 清空内存运行态失败: {exc}")
                result["hard_reset"] = True

            print("[GLB_LIQ] 总清仓完成，准备重启 autorun")
            autorun.stop_event.set()
        except Exception as exc:
            result["errors"].append(str(exc))
            print(f"[GLB_LIQ][ERROR] 总清仓流程失败: {exc}")
            try:
                # 若在停止运行态后抛异常，主动触发主循环重启以避免卡在半停机状态。
                autorun.stop_event.set()
            except Exception:
                pass
        finally:
            if hasattr(autorun, "_resume_ws_updates"):
                autorun._resume_ws_updates("total-liquidation-finished")
            self._running = False

        return result

    def _wait_for_tasks_stopped(self, autorun: Any, timeout_sec: float = 30.0) -> bool:
        deadline = time.time() + max(1.0, float(timeout_sec))
        while time.time() < deadline:
            running = [
                t for t in list(getattr(autorun, "tasks", {}).values())
                if t and getattr(t, "is_running", lambda: False)()
            ]
            if not running:
                return True
            time.sleep(0.2)
        return False

    @staticmethod
    def _normalize_collateral_balance(value: float, raw: Any) -> float:
        """
        CLOB balance-allowance 的 collateral balance 为 USDC 最小单位（6 decimals）。
        若原始值为纯整数（如 "53608824"），换算为 53.608824 USDC。
        """
        if isinstance(raw, bool):
            return value
        if isinstance(raw, int):
            return value / (10 ** TotalLiquidationManager._COLLATERAL_DECIMALS)
        if isinstance(raw, float):
            if raw.is_integer():
                return value / (10 ** TotalLiquidationManager._COLLATERAL_DECIMALS)
            return value
        if isinstance(raw, str):
            text = raw.strip()
            if text and text.lstrip("+-").isdigit():
                return value / (10 ** TotalLiquidationManager._COLLATERAL_DECIMALS)
        return value

    @staticmethod
    def _extract_balance_float(payload: Any, from_balance_key: bool = False) -> Optional[float]:
        """
        严格按 balance 语义字段提取余额，避免误取 allowance 等其他数值。
        只有命中余额语义键（balance/available）后的值才允许被解析为数值。
        """
        if isinstance(payload, (int, float)) and not isinstance(payload, bool):
            if not from_balance_key:
                return None
            return TotalLiquidationManager._normalize_collateral_balance(float(payload), payload)
        if isinstance(payload, str):
            if not from_balance_key:
                return None
            try:
                parsed = float(payload)
            except ValueError:
                return None
            return TotalLiquidationManager._normalize_collateral_balance(parsed, payload)
        if isinstance(payload, dict):
            for key in ("balance", "available", "availableBalance", "available_balance"):
                if key in payload:
                    parsed = TotalLiquidationManager._extract_balance_float(payload[key], True)
                    if parsed is not None:
                        return parsed

            # 已进入余额语义子树时，允许解析常见数值承载字段（如 amount/value）
            if from_balance_key:
                for key in ("amount", "value", "balance", "available", "availableBalance", "available_balance"):
                    if key in payload:
                        parsed = TotalLiquidationManager._extract_balance_float(payload[key], True)
                        if parsed is not None:
                            return parsed
                for v in payload.values():
                    if isinstance(v, (dict, list, tuple)):
                        parsed = TotalLiquidationManager._extract_balance_float(v, True)
                        if parsed is not None:
                            return parsed

            for v in payload.values():
                if isinstance(v, (dict, list, tuple)):
                    parsed = TotalLiquidationManager._extract_balance_float(v, False)
                    if parsed is not None:
                        return parsed
            return None
        if isinstance(payload, (list, tuple)):
            for item in payload:
                if from_balance_key:
                    parsed = TotalLiquidationManager._extract_balance_float(item, True)
                    if parsed is not None:
                        return parsed
                if isinstance(item, (dict, list, tuple)):
                    parsed = TotalLiquidationManager._extract_balance_float(item, False)
                    if parsed is not None:
                        return parsed
        return None

    @staticmethod
    def _extract_first_float(payload: Any) -> Optional[float]:
        if isinstance(payload, (int, float)) and not isinstance(payload, bool):
            return float(payload)
        if isinstance(payload, str):
            try:
                return float(payload)
            except ValueError:
                return None
        if isinstance(payload, dict):
            for key in ("available", "availableBalance", "available_balance", "balance", "amount", "value"):
                if key in payload:
                    parsed = TotalLiquidationManager._extract_first_float(payload[key])
                    if parsed is not None:
                        return parsed
            for v in payload.values():
                parsed = TotalLiquidationManager._extract_first_float(v)
                if parsed is not None:
                    return parsed
        if isinstance(payload, (list, tuple)):
            for item in payload:
                parsed = TotalLiquidationManager._extract_first_float(item)
                if parsed is not None:
                    return parsed
        return None

    def _get_cached_client(self) -> Optional[Any]:
        now = time.time()
        if self._cached_client is not None:
            return self._cached_client
        if now < self._next_client_retry_at:
            return None
        try:
            self._cached_client = self._load_client()
            return self._cached_client
        except Exception:
            self._next_client_retry_at = now + 60.0
            return None

    def _query_free_balance_usdc(
        self,
        autorun: Any,
        *,
        ignore_enabled: bool = False,
        force: bool = False,
        poll_interval_sec: Optional[float] = None,
    ) -> Optional[float]:
        if not self.cfg.enabled and not ignore_enabled:
            return None
        override = os.getenv("POLY_FREE_BALANCE_OVERRIDE")
        if override is not None:
            try:
                return float(override)
            except ValueError:
                return None

        now = time.time()
        interval = (
            self.cfg.balance_poll_interval_sec
            if poll_interval_sec is None
            else max(1.0, float(poll_interval_sec))
        )

        if (not force) and now < self._next_balance_probe_at:
            return self._cached_free_balance

        self._next_balance_probe_at = now + interval
        self._last_balance_probe_error = None

        client = self._get_cached_client()
        if client is None:
            self._last_balance_probe_error = "client init failed"
            return self._cached_free_balance

        # 严格使用官方 py-clob-client 方法：get_balance_allowance(BalanceAllowanceParams)
        get_balance_allowance = getattr(client, "get_balance_allowance", None)
        if not callable(get_balance_allowance):
            self._last_balance_probe_error = "client.get_balance_allowance 不可调用"
            return self._cached_free_balance

        try:
            from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        except Exception as exc:
            self._last_balance_probe_error = f"py_clob_client 类型导入失败: {exc}"
            return self._cached_free_balance

        params = BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL,
            token_id=None,
            signature_type=-1,
        )

        try:
            resp = get_balance_allowance(params)
            parsed = self._extract_balance_float(resp)
            if parsed is not None:
                self._cached_free_balance = parsed
                return self._cached_free_balance
            self._last_balance_probe_error = f"响应中未找到 balance 字段: {resp}"
        except Exception as exc:
            self._last_balance_probe_error = str(exc)

        return self._cached_free_balance

    @staticmethod
    def _parse_trade_timestamp(raw: Any) -> float:
        if raw is None:
            return 0.0
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            return ts if ts > 0 else 0.0
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return 0.0
            try:
                return TotalLiquidationManager._parse_trade_timestamp(float(text))
            except ValueError:
                pass
            try:
                norm = text.replace("Z", "+00:00")
                dt = datetime.fromisoformat(norm)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0.0, dt.timestamp())
            except ValueError:
                return 0.0
        return 0.0

    def _query_recent_fill_activity_ts(self, autorun: Any) -> float:
        if not self.cfg.enabled:
            return 0.0

        now = time.time()
        poll_interval = max(10.0, float(getattr(self.cfg, "balance_poll_interval_sec", 120.0)))
        if now < self._next_trade_probe_at:
            return 0.0
        self._next_trade_probe_at = now + poll_interval
        self._last_api_trade_probe_error = None

        address = None
        if getattr(autorun, "_position_address", None):
            address = str(getattr(autorun, "_position_address") or "").strip()
        if not address:
            address = self._resolve_wallet()
        if not address:
            self._last_api_trade_probe_error = "wallet address missing"
            return 0.0

        url = os.getenv("POLY_DATA_API_ROOT", "https://data-api.polymarket.com").rstrip("/") + "/trades"
        # Keep probe lightweight to reduce read-timeout probability on /trades.
        params = {"user": address, "limit": 50}
        headers = {
            "Accept": "application/json",
            "User-Agent": "POLYMARKET_MAKER_AUTO/1.0 (+data-api-trades)",
        }

        latest_ts = 0.0
        for attempt in range(3):
            try:
                query = urllib.parse.urlencode(params)
                req = urllib.request.Request(f"{url}?{query}", headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))

                if isinstance(payload, list):
                    items = payload
                elif isinstance(payload, dict):
                    items = payload.get("data")
                else:
                    self._last_api_trade_probe_error = "trades response schema invalid"
                    return 0.0

                if not isinstance(items, list):
                    self._last_api_trade_probe_error = "trades response schema invalid"
                    return 0.0

                for row in items:
                    if not isinstance(row, dict):
                        continue
                    ts = self._parse_trade_timestamp(row.get("timestamp"))
                    if ts <= 0:
                        ts = self._parse_trade_timestamp(row.get("time"))
                    if ts <= 0:
                        ts = self._parse_trade_timestamp(row.get("created_at"))
                    if ts > latest_ts:
                        latest_ts = ts
                return latest_ts
            except Exception as exc:
                self._last_api_trade_probe_error = str(exc)
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue

        return 0.0

    def _resolve_wallet(self) -> Optional[str]:
        for key in ("POLY_DATA_ADDRESS", "POLY_FUNDER"):
            cand = os.getenv(key)
            if cand and str(cand).strip():
                return str(cand).strip()
        return None

    def _fetch_positions(self) -> List[Dict[str, Any]]:
        address = self._resolve_wallet()
        if not address:
            self._last_positions_fetch_error = "wallet address missing"
            return []

        url = os.getenv("POLY_DATA_API_ROOT", "https://data-api.polymarket.com").rstrip("/") + "/positions"
        params = {"user": address, "sizeThreshold": 0, "limit": 500, "offset": 0}
        out: List[Dict[str, Any]] = []
        self._last_positions_fetch_error = None

        headers = {
            "Accept": "application/json",
            "User-Agent": "POLYMARKET_MAKER_AUTO/1.0 (+data-api-positions)",
        }

        for attempt in range(3):
            try:
                cursor = dict(params)
                while True:
                    query = urllib.parse.urlencode(cursor)
                    req = urllib.request.Request(f"{url}?{query}", headers=headers, method="GET")
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        payload = json.loads(resp.read().decode("utf-8"))

                    if isinstance(payload, list):
                        items = payload
                    elif isinstance(payload, dict):
                        items = payload.get("data")
                    else:
                        items = None

                    if not isinstance(items, list):
                        self._last_positions_fetch_error = "positions response schema invalid"
                        return []

                    if not items:
                        break

                    out.extend([x for x in items if isinstance(x, dict)])
                    if len(items) < int(cursor["limit"]):
                        break
                    cursor["offset"] = int(cursor["offset"]) + len(items)

                return out
            except Exception as exc:
                self._last_positions_fetch_error = str(exc)
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue

        print(f"[GLB_LIQ][WARN] 拉取持仓失败: {self._last_positions_fetch_error}")
        return []

    @staticmethod
    def _extract_token(entry: Dict[str, Any]) -> Optional[str]:
        for key in ("token_id", "tokenId", "asset", "asset_id"):
            val = entry.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return None

    @staticmethod
    def _extract_size(entry: Dict[str, Any]) -> float:
        for key in ("size", "position", "position_size", "balance", "amount", "shares"):
            val = entry.get(key)
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _extract_price(entry: Dict[str, Any]) -> float:
        for key in ("current_price", "price", "avgPrice", "average_price", "entry_price", "mark_price"):
            val = entry.get(key)
            try:
                px = float(val)
                if px > 0:
                    return px
            except (TypeError, ValueError):
                continue
        return 0.0

    def _load_client(self) -> Any:
        from Volatility_arbitrage_run import _get_client

        return _get_client()



    @staticmethod
    def _is_fak_no_match_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return "no orders found to match" in text and "fak" in text

    @staticmethod
    def _build_sell_price_ladder(price: float) -> List[float]:
        base = max(float(price or 0.0), 0.01)
        ladder = [base, max(0.01, base * 0.997), max(0.01, base * 0.992), max(0.01, base * 0.985), 0.01]
        dedup: List[float] = []
        for px in ladder:
            px = round(float(px), 4)
            if dedup and abs(dedup[-1] - px) < 1e-9:
                continue
            dedup.append(px)
        return dedup

    @staticmethod
    def _build_buy_price_ladder(price: float) -> List[float]:
        base = max(float(price or 0.0), 0.01)
        ladder = [base, min(0.9999, base * 1.003), min(0.9999, base * 1.008), min(0.9999, base * 1.015)]
        dedup: List[float] = []
        for px in ladder:
            px = round(float(px), 4)
            if dedup and abs(dedup[-1] - px) < 1e-9:
                continue
            dedup.append(px)
        return dedup

    def _place_sell_ioc(self, client: Any, token_id: str, price: float, size: float) -> Dict[str, Any]:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        eff_size = max(float(size), 0.0)
        order_type = getattr(OrderType, "FAK", None) or getattr(OrderType, "IOC", OrderType.FOK)
        last_exc: Optional[Exception] = None

        for idx, eff_price in enumerate(self._build_sell_price_ladder(price), start=1):
            order = OrderArgs(token_id=str(token_id), side=SELL, price=max(float(eff_price), 0.01), size=eff_size)
            signed = client.create_order(order)
            try:
                resp = client.post_order(signed, order_type)
                if idx > 1:
                    print(
                        f"[GLB_LIQ][IOC] token={token_id} 阶梯价格 level={idx} price={eff_price:.4f} 下单成功"
                    )
                return resp
            except Exception as exc:
                last_exc = exc
                if self._is_fak_no_match_error(exc):
                    print(
                        f"[GLB_LIQ][IOC] token={token_id} level={idx} price={eff_price:.4f} "
                        "无可匹配买单，继续尝试更低价"
                    )
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        return {}

    @staticmethod
    def _extract_filled_and_price(resp: Any) -> Tuple[float, Optional[float]]:
        """Best-effort parse of IOC response fill stats."""
        if not isinstance(resp, dict):
            return 0.0, None

        # 1) direct aggregate fields
        filled = 0.0
        avg_price: Optional[float] = None
        try:
            filled = float(resp.get("filled") or 0.0)
        except (TypeError, ValueError):
            filled = 0.0
        for key in ("avg_price", "average_price", "price", "avgPrice"):
            try:
                px = float(resp.get(key))
                if px > 0:
                    avg_price = px
                    break
            except (TypeError, ValueError):
                continue
        if filled > 0 and avg_price is not None:
            return filled, avg_price

        # 2) nested order list
        orders = resp.get("orders")
        if isinstance(orders, list):
            total_size = 0.0
            total_notional = 0.0
            for row in orders:
                if not isinstance(row, dict):
                    continue
                try:
                    row_filled = float(row.get("filled") or 0.0)
                except (TypeError, ValueError):
                    row_filled = 0.0
                if row_filled <= 0:
                    continue
                row_px: Optional[float] = None
                for key in ("avg_price", "average_price", "price", "avgPrice"):
                    try:
                        p = float(row.get(key))
                        if p > 0:
                            row_px = p
                            break
                    except (TypeError, ValueError):
                        continue
                if row_px is None:
                    continue
                total_size += row_filled
                total_notional += row_filled * row_px
            if total_size > 0:
                return total_size, (total_notional / total_size)
        return 0.0, None

    def _place_buy_ioc(self, client: Any, token_id: str, price: float, size: float) -> Dict[str, Any]:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        eff_size = max(float(size), 0.0)
        order_type = getattr(OrderType, "FAK", None) or getattr(OrderType, "IOC", OrderType.FOK)
        last_exc: Optional[Exception] = None

        for idx, eff_price in enumerate(self._build_buy_price_ladder(price), start=1):
            order = OrderArgs(
                token_id=str(token_id),
                side=BUY,
                price=min(max(float(eff_price), 0.01), 0.9999),
                size=eff_size,
            )
            signed = client.create_order(order)
            try:
                resp = client.post_order(signed, order_type)
                if idx > 1:
                    print(
                        f"[GLB_LIQ][IOC_BUY] token={token_id} 阶梯价格 level={idx} price={eff_price:.4f} 下单成功"
                    )
                return resp
            except Exception as exc:
                last_exc = exc
                if self._is_fak_no_match_error(exc):
                    print(
                        f"[GLB_LIQ][IOC_BUY] token={token_id} level={idx} price={eff_price:.4f} "
                        "无可匹配卖单，继续尝试更高价"
                    )
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        return {}

    def _estimate_value(self, size: float, pos_price: float, bid: float, ask: float) -> float:
        if pos_price > 0:
            return size * pos_price
        if bid > 0 and ask > 0 and ask >= bid:
            return size * ((bid + ask) / 2.0)
        if bid > 0:
            return size * bid
        if ask > 0:
            return size * ask
        return 0.0

    def _resolve_bid_ask(self, autorun: Any, token_id: str) -> Tuple[float, float]:
        with autorun._ws_cache_lock:
            cached = dict(autorun._ws_cache.get(token_id) or {})
        try:
            bid = float(cached.get("best_bid") or 0.0)
        except (TypeError, ValueError):
            bid = 0.0
        try:
            ask = float(cached.get("best_ask") or 0.0)
        except (TypeError, ValueError):
            ask = 0.0
        return bid, ask

    def _is_quote_fresh(self, autorun: Any, token_id: str, max_age_sec: float = 30.0) -> bool:
        try:
            with autorun._ws_cache_lock:
                cached = dict(autorun._ws_cache.get(token_id) or {})
        except Exception:
            return False
        updated_at = cached.get("updated_at")
        try:
            ts = float(updated_at)
        except (TypeError, ValueError):
            return False
        if ts <= 0:
            return False
        return (time.time() - ts) <= max(1.0, float(max_age_sec))

    def _compute_taker_price(self, bid: float, ask: float) -> float:
        base = bid if bid > 0 else ask if ask > 0 else 0.01
        bps = max(0.0, float(self.cfg.taker_slippage_bps))
        return max(0.01, base * (1.0 - bps / 10000.0))

    def _fetch_single_position_size(self, token_id: str) -> float:
        for entry in self._fetch_positions():
            if self._extract_token(entry) == str(token_id):
                return max(self._extract_size(entry), 0.0)
        return 0.0

    def _load_copytrade_token_scope(self) -> set[str]:
        copytrade_dir = self.project_root.parent / "copytrade"
        tokens_path = copytrade_dir / "tokens_from_copytrade.json"
        signals_path = copytrade_dir / "copytrade_sell_signals.json"
        token_ids: set[str] = set()

        for path, key in ((tokens_path, "tokens"), (signals_path, "sell_tokens")):
            if not path.exists():
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
                rows = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(rows, list):
                    for item in rows:
                        if not isinstance(item, dict):
                            continue
                        tid = item.get("token_id") or item.get("tokenId")
                        if tid is not None and str(tid).strip():
                            token_ids.add(str(tid).strip())
            except Exception:
                continue
        return token_ids

    def _cancel_open_orders_for_token(self, client: Any, token_id: str) -> int:
        """撤销指定token的所有开放订单。返回撤销的订单数量。"""
        try:
            open_orders = self._fetch_open_orders(client)
            canceled = 0
            for order in open_orders:
                if str(order.get("token_id")) != str(token_id):
                    continue
                order_id = order.get("order_id")
                if not order_id:
                    continue
                try:
                    self._cancel_order_compat(client, str(order_id))
                    canceled += 1
                except Exception:
                    pass
            return canceled
        except Exception:
            return 0

    def _fetch_open_orders(self, client: Any) -> List[Dict[str, Any]]:
        """获取所有开放订单。"""
        try:
            # 尝试使用client的get_orders方法
            if hasattr(client, 'get_orders'):
                return client.get_orders() or []
            return []
        except Exception:
            return []

    def _cancel_order(self, client: Any, order_id: str) -> None:
        """撤销单个订单。"""
        try:
            if hasattr(client, 'cancel_order'):
                client.cancel_order(order_id)
        except Exception:
            pass

    def _fetch_open_orders(self, client: Any) -> List[Dict[str, Any]]:
        """Fetch normalized open orders for the current client."""
        try:
            get_orders = getattr(client, "get_orders", None)
            if not callable(get_orders):
                return []
            try:
                payload = get_orders()
            except TypeError:
                try:
                    module = importlib.import_module("py_clob_client.clob_types")
                except Exception:
                    return []
                open_order_params = getattr(module, "OpenOrderParams", None)
                if open_order_params is None:
                    return []
                payload = get_orders(open_order_params())
            orders = payload if isinstance(payload, list) else []
            normalized: List[Dict[str, Any]] = []
            for order in orders:
                if not isinstance(order, dict):
                    continue
                order_id = order.get("id") or order.get("orderID") or order.get("order_id")
                order_token_id = order.get("asset_id") or order.get("tokenId") or order.get("token_id")
                if order_id is None or order_token_id is None:
                    continue
                normalized.append(
                    {
                        "order_id": str(order_id),
                        "token_id": str(order_token_id),
                        "side": str(order.get("side") or ""),
                    }
                )
            return normalized
        except Exception:
            return []

    @staticmethod
    def _extract_canceled_order_ids(payload: Any) -> Tuple[List[str], List[str]]:
        canceled_ids: List[str] = []
        not_canceled: List[str] = []
        if isinstance(payload, dict):
            canceled_payload = payload.get("canceled")
            not_canceled_payload = payload.get("not_canceled")
        else:
            canceled_payload = None
            not_canceled_payload = None

        def _collect(items: Any, target: List[str]) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if isinstance(item, dict):
                    value = (
                        item.get("orderID")
                        or item.get("order_id")
                        or item.get("id")
                        or item.get("reason")
                        or item.get("error")
                    )
                    if value is not None:
                        target.append(str(value))
                elif item is not None:
                    target.append(str(item))

        _collect(canceled_payload, canceled_ids)
        _collect(not_canceled_payload, not_canceled)
        return canceled_ids, not_canceled

    def _cancel_orders_batch(self, client: Any, order_ids: List[str]) -> Dict[str, Any]:
        ids = [str(order_id).strip() for order_id in order_ids if str(order_id).strip()]
        if not ids:
            return {"used_batch": False, "canceled_ids": [], "not_canceled": []}
        for method_name in ("cancel_orders", "cancelOrders"):
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            payload = method(ids)
            canceled_ids, not_canceled = self._extract_canceled_order_ids(payload)
            if not canceled_ids and not not_canceled:
                canceled_ids = list(ids)
            return {
                "used_batch": True,
                "canceled_ids": canceled_ids,
                "not_canceled": not_canceled,
                "raw": payload,
            }
        return {"used_batch": False, "canceled_ids": [], "not_canceled": []}

    def _cancel_order_compat(self, client: Any, order_id: str) -> None:
        last_exc: Optional[Exception] = None
        for method_name in ("cancel", "cancel_order"):
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            try:
                method(order_id)
                return
            except Exception as exc:
                last_exc = exc
        cancel_orders = getattr(client, "cancel_orders", None)
        if callable(cancel_orders):
            try:
                cancel_orders([order_id])
                return
            except Exception as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise AttributeError("client has no supported cancel method")

    def _token_has_open_orders(self, client: Any, token_id: str) -> bool:
        try:
            open_orders = self._fetch_open_orders(client)
        except Exception:
            return False
        token_id_str = str(token_id)
        return any(str(order.get("token_id")) == token_id_str for order in open_orders)

    def _wait_for_open_orders_clear(
        self,
        client: Any,
        token_id: str,
        *,
        attempts: int = 6,
        sleep_sec: float = 0.25,
    ) -> bool:
        total_attempts = max(1, int(attempts))
        for idx in range(total_attempts):
            if not self._token_has_open_orders(client, token_id):
                return True
            if idx + 1 < total_attempts:
                time.sleep(max(0.0, float(sleep_sec)))
        return False

    def _get_token_open_orders(self, client: Any, token_id: str) -> List[Dict[str, Any]]:
        token_id_str = str(token_id or "").strip()
        return [
            dict(order)
            for order in self._fetch_open_orders(client)
            if str(order.get("token_id")) == token_id_str
        ]

    def _clear_open_orders_for_token(
        self,
        client: Any,
        token_id: str,
        *,
        cancel_rounds: int = 3,
        clear_attempts_per_round: int = 8,
        sleep_sec: float = 0.35,
    ) -> Dict[str, Any]:
        token_id_str = str(token_id or "").strip()
        total_rounds = max(1, int(cancel_rounds))
        canceled_ids: List[str] = []
        cancel_errors: List[str] = []
        not_canceled: List[str] = []
        remaining_orders: List[Dict[str, Any]] = []

        for round_idx in range(total_rounds):
            remaining_orders = self._get_token_open_orders(client, token_id_str)
            if not remaining_orders:
                return {
                    "cleared": True,
                    "rounds": round_idx + 1,
                    "canceled_ids": canceled_ids,
                    "cancel_errors": cancel_errors,
                    "not_canceled": not_canceled,
                    "remaining_orders": [],
                }
            order_ids = [
                str(order.get("order_id") or "").strip()
                for order in remaining_orders
                if str(order.get("order_id") or "").strip()
            ]
            batch_result = self._cancel_orders_batch(client, order_ids)
            if batch_result.get("used_batch"):
                canceled_ids.extend(list(batch_result.get("canceled_ids") or []))
                not_canceled.extend(list(batch_result.get("not_canceled") or []))
            else:
                for order_id in order_ids:
                    try:
                        self._cancel_order_compat(client, order_id)
                        canceled_ids.append(order_id)
                    except Exception as exc:
                        cancel_errors.append(f"{order_id}:{exc}")
            if self._wait_for_open_orders_clear(
                client,
                token_id_str,
                attempts=clear_attempts_per_round,
                sleep_sec=sleep_sec,
            ):
                return {
                    "cleared": True,
                    "rounds": round_idx + 1,
                    "canceled_ids": canceled_ids,
                    "cancel_errors": cancel_errors,
                    "not_canceled": not_canceled,
                    "remaining_orders": [],
                }
        remaining_orders = self._get_token_open_orders(client, token_id_str)
        return {
            "cleared": len(remaining_orders) == 0,
            "rounds": total_rounds,
            "canceled_ids": canceled_ids,
            "cancel_errors": cancel_errors,
            "not_canceled": not_canceled,
            "remaining_orders": remaining_orders,
        }

    def _probe_position_size_after_error(
        self,
        token_id: str,
        *,
        attempts: int = 3,
        sleep_sec: float = 0.5,
    ) -> float:
        total_attempts = max(1, int(attempts))
        latest_size = max(0.0, float(self._fetch_single_position_size(token_id)))
        for idx in range(total_attempts):
            latest_size = max(0.0, float(self._fetch_single_position_size(token_id)))
            if latest_size <= 1e-6:
                return 0.0
            if idx + 1 < total_attempts:
                time.sleep(max(0.0, float(sleep_sec)))
        return latest_size

    def liquidate_single_token_taker(
        self,
        autorun: Any,
        token_id: str,
        *,
        target_size: Optional[float] = None,
        reason: str = "",
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        """对单个 token 执行 taker 清仓（支持部分数量）。"""
        token_id = str(token_id or "").strip()
        if not token_id:
            return {"ok": False, "error": "missing token_id"}
        normalized_reason = self._normalize_ioc_reason(reason)
        if normalized_reason not in self._ALLOWED_SINGLE_TOKEN_IOC_REASONS:
            print(
                "[GLB_LIQ][BLOCK] single-token IOC blocked by whitelist: "
                f"token={token_id[:20]}... reason={normalized_reason or 'UNSPECIFIED'}"
            )
            self._record_blocked_single_token_ioc(
                autorun,
                token_id,
                requested_reason=normalized_reason,
                target_size=target_size,
            )
            return {
                "ok": False,
                "blocked": True,
                "ioc_allowed": False,
                "token_id": token_id,
                "requested_size": max(0.0, float(target_size or 0.0)),
                "filled_size": 0.0,
                "before_size": None,
                "after_size": None,
                "attempts": 0,
                "reason": normalized_reason,
                "error": (
                    "single-token IOC blocked by whitelist "
                    f"reason={normalized_reason or 'UNSPECIFIED'}"
                ),
            }

        client = self._get_cached_client()
        if client is None:
            return {"ok": False, "error": "client init failed", "token_id": token_id}

        before_size = max(0.0, float(self._fetch_single_position_size(token_id)))
        if before_size <= 0:
            return {
                "ok": True,
                "token_id": token_id,
                "requested_size": 0.0,
                "filled_size": 0.0,
                "before_size": before_size,
                "after_size": 0.0,
                "attempts": 0,
                "reason": normalized_reason,
            }

        requested_size = before_size
        if target_size is not None:
            try:
                requested_size = max(0.0, min(before_size, float(target_size)))
            except (TypeError, ValueError):
                requested_size = before_size
        if requested_size <= 0:
            return {
                "ok": True,
                "token_id": token_id,
                "requested_size": 0.0,
                "filled_size": 0.0,
                "before_size": before_size,
                "after_size": before_size,
                "attempts": 0,
                "reason": normalized_reason,
            }

        # 先撤单，避免 maker/遗留订单和 taker 冲突。
        try:
            clear_info = self._clear_open_orders_for_token(client, token_id)
        except Exception as exc:
            clear_info = {
                "cleared": False,
                "rounds": 0,
                "canceled_ids": [],
                "cancel_errors": [f"clear_open_orders:{exc}"],
                "remaining_orders": self._get_token_open_orders(client, token_id),
            }
        if not bool(clear_info.get("cleared")):
            remaining_orders = list(clear_info.get("remaining_orders") or [])
            remaining_ids = [
                str(order.get("order_id") or "").strip()
                for order in remaining_orders
                if str(order.get("order_id") or "").strip()
            ]
            error_parts = [
                f"open orders still live after cancel for token={token_id}",
                f"rounds={int(clear_info.get('rounds') or 0)}",
            ]
            if remaining_ids:
                error_parts.append(f"remaining_order_ids={','.join(remaining_ids[:5])}")
            cancel_errors = list(clear_info.get("cancel_errors") or [])
            if cancel_errors:
                error_parts.append(f"cancel_errors={'; '.join(cancel_errors[:3])}")
            not_canceled = list(clear_info.get("not_canceled") or [])
            if not_canceled:
                error_parts.append(f"not_canceled={'; '.join(not_canceled[:3])}")
            return {
                "ok": False,
                "token_id": token_id,
                "requested_size": requested_size,
                "filled_size": 0.0,
                "before_size": before_size,
                "after_size": before_size,
                "remaining_target": requested_size,
                "attempts": 0,
                "reason": normalized_reason,
                "error": " | ".join(error_parts),
                "executed_avg_price": None,
                "executed_price_source": "unknown",
                "cancel_debug": clear_info,
            }

        attempts = 0
        sold_total = 0.0
        last_error: Optional[str] = None
        remain_to_sell = requested_size
        executed_notional = 0.0
        executed_size = 0.0
        last_quote_price: Optional[float] = None
        while attempts < max(1, int(max_attempts)) and remain_to_sell > 1e-6:
            attempts += 1
            try:
                bid, ask = self._resolve_bid_ask(autorun, token_id)
                taker_price = self._compute_taker_price(bid=bid, ask=ask)
                last_quote_price = taker_price
                resp = self._place_sell_ioc(client, token_id, taker_price, remain_to_sell)
                fill_size, fill_price = self._extract_filled_and_price(resp)
                if fill_size > 0 and fill_price is not None:
                    executed_size += fill_size
                    executed_notional += fill_size * fill_price
                after_size = max(0.0, float(self._fetch_single_position_size(token_id)))
                sold_total = max(0.0, before_size - after_size)
                remain_to_sell = max(0.0, requested_size - sold_total)
                if remain_to_sell <= 1e-6:
                    break
            except Exception as exc:
                last_error = str(exc)
                after_error_size = self._probe_position_size_after_error(token_id)
                sold_total = max(0.0, before_size - after_error_size)
                remain_to_sell = max(0.0, requested_size - sold_total)
                if remain_to_sell <= 1e-6:
                    last_error = None
                break

        after_size = max(0.0, float(self._fetch_single_position_size(token_id)))
        sold_total = max(0.0, before_size - after_size)
        remaining_target = max(0.0, requested_size - sold_total)
        ok = (remaining_target <= 1e-6) and (last_error is None)
        executed_avg_price: Optional[float] = None
        if executed_size > 1e-9 and executed_notional > 0:
            executed_avg_price = executed_notional / executed_size
        elif last_quote_price is not None and sold_total > 1e-9:
            # Fallback to quote used for IOC ladder when exchange response omits fill prices.
            executed_avg_price = float(last_quote_price)
        return {
            "ok": ok,
            "token_id": token_id,
            "requested_size": requested_size,
            "filled_size": sold_total,
            "before_size": before_size,
            "after_size": after_size,
            "remaining_target": remaining_target,
            "attempts": attempts,
            "reason": normalized_reason,
            "error": last_error,
            "executed_avg_price": executed_avg_price,
            "executed_price_source": "response_fill" if executed_size > 1e-9 else ("ioc_quote_fallback" if executed_avg_price is not None else "unknown"),
        }

    def reenter_single_token_taker(
        self,
        autorun: Any,
        token_id: str,
        *,
        target_size: float,
        reference_buy_price: Optional[float] = None,
        reason: str = "",
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        """对单个 token 执行 taker 回补买入。"""
        token_id = str(token_id or "").strip()
        if not token_id:
            return {"ok": False, "error": "missing token_id"}

        client = self._get_cached_client()
        if client is None:
            return {"ok": False, "error": "client init failed", "token_id": token_id}

        before_size = max(0.0, float(self._fetch_single_position_size(token_id)))
        requested_size = max(0.0, float(target_size or 0.0))
        if requested_size <= 0:
            return {
                "ok": False,
                "token_id": token_id,
                "error": "invalid target_size",
                "before_size": before_size,
            }

        attempts = 0
        bought_total = 0.0
        last_error: Optional[str] = None
        remain_to_buy = requested_size
        last_exec_price: Optional[float] = None
        while attempts < max(1, int(max_attempts)) and remain_to_buy > 1e-6:
            attempts += 1
            try:
                bid, ask = self._resolve_bid_ask(autorun, token_id)
                if ask <= 0:
                    last_error = "missing taker ask quote"
                    break
                if not self._is_quote_fresh(autorun, token_id, max_age_sec=30.0):
                    last_error = "stale taker ask quote"
                    break
                if (
                    reference_buy_price is not None
                    and reference_buy_price > 0
                    and ask > float(reference_buy_price) + 1e-12
                ):
                    last_error = (
                        f"ask above reference_buy_price ask={ask:.6f} "
                        f"ref={float(reference_buy_price):.6f}"
                    )
                    break
                base_buy = ask
                self._place_buy_ioc(client, token_id, base_buy, remain_to_buy)
                last_exec_price = base_buy
                after_size = max(0.0, float(self._fetch_single_position_size(token_id)))
                bought_total = max(0.0, after_size - before_size)
                remain_to_buy = max(0.0, requested_size - bought_total)
                if remain_to_buy <= 1e-6:
                    break
            except Exception as exc:
                last_error = str(exc)
                break

        after_size = max(0.0, float(self._fetch_single_position_size(token_id)))
        bought_total = max(0.0, after_size - before_size)
        remaining_target = max(0.0, requested_size - bought_total)
        ok = (remaining_target <= 1e-6) and (last_error is None)
        return {
            "ok": ok,
            "token_id": token_id,
            "requested_size": requested_size,
            "filled_size": bought_total,
            "before_size": before_size,
            "after_size": after_size,
            "remaining_target": remaining_target,
            "attempts": attempts,
            "reason": reason,
            "error": last_error,
            "executed_price": last_exec_price,
        }

    def _liquidate_positions(
        self,
        autorun: Any,
        *,
        prechecked_client: Optional[Any] = None,
        prechecked_token_scope: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        from maker_execution import maker_sell_follow_ask_with_floor_wait

        client = prechecked_client if prechecked_client is not None else self._get_cached_client()
        if client is None:
            return {"liquidated": 0, "maker_count": 0, "taker_count": 0, "errors": ["client init failed"], "aborted": True}

        positions = self._fetch_positions()
        if self._last_positions_fetch_error:
            return {
                "liquidated": 0,
                "maker_count": 0,
                "taker_count": 0,
                "errors": [f"positions fetch failed: {self._last_positions_fetch_error}"],
                "aborted": True,
            }

        allowed_token_ids = prechecked_token_scope
        if self.cfg.token_scope_mode != self._TOKEN_SCOPE_ALL_POSITIONS and allowed_token_ids is None:
            allowed_token_ids = self._load_copytrade_token_scope()

        if self.cfg.token_scope_mode != self._TOKEN_SCOPE_ALL_POSITIONS and not allowed_token_ids:
            return {
                "liquidated": 0,
                "maker_count": 0,
                "taker_count": 0,
                "errors": ["copytrade token scope is empty; skip liquidation for safety"],
                "aborted": True,
            }

        maker_count = 0
        taker_count = 0
        liquidated = 0
        liquidated_tokens: set[str] = set()
        errors: List[str] = []

        for entry in positions:
            token_id = self._extract_token(entry)
            if not token_id:
                continue
            if allowed_token_ids is not None and token_id not in allowed_token_ids:
                continue

            size = self._extract_size(entry)
            if size <= 0:
                continue

            bid, ask = self._resolve_bid_ask(autorun, token_id)
            pos_price = self._extract_price(entry)
            value = self._estimate_value(size=size, pos_price=pos_price, bid=bid, ask=ask)
            if value < self.cfg.position_value_threshold:
                continue

            try:
                # 【修复】清仓前先撤销该token的所有挂单，避免重复挂单
                try:
                    canceled = self._cancel_open_orders_for_token(client, token_id)
                    if canceled:
                        print(f"[GLB_LIQ] 已撤销 {token_id[:20]}... 的 {canceled} 个挂单")
                except Exception as cancel_exc:
                    print(f"[GLB_LIQ][WARN] 撤销 {token_id[:20]}... 挂单失败: {cancel_exc}")
                spread = (ask - bid) if (ask > 0 and bid > 0 and ask >= bid) else 0.0

                quote_fresh = self._is_quote_fresh(autorun, token_id)
                can_use_maker = spread > self.cfg.spread_threshold and ask > 0 and quote_fresh

                if can_use_maker:
                    maker_count += 1

                    def _best_ask_fn() -> Optional[float]:
                        _bid, _ask = self._resolve_bid_ask(autorun, token_id)
                        return _ask if _ask > 0 else None

                    floor_x = self._compute_taker_price(bid=bid, ask=ask)
                    maker_resp = maker_sell_follow_ask_with_floor_wait(
                        client=client,
                        token_id=token_id,
                        position_size=size,
                        floor_X=floor_x,
                        poll_sec=max(1.0, float(getattr(autorun.config, "maker_poll_sec", 10.0))),
                        best_ask_fn=_best_ask_fn,
                        inactive_timeout_sec=max(60.0, self.cfg.maker_timeout_minutes * 60.0),
                        sell_mode="aggressive",
                    )

                    maker_status = str((maker_resp or {}).get("status") or "").upper()
                    if maker_status in {"ABANDONED", "FAILED", "PRICE_TIMEOUT", "STOPPED", "PENDING"}:
                        remain = self._fetch_single_position_size(token_id)
                        if remain > 0:
                            taker_count += 1
                            bid2, ask2 = self._resolve_bid_ask(autorun, token_id)
                            self._place_sell_ioc(
                                client,
                                token_id,
                                self._compute_taker_price(bid=bid2, ask=ask2),
                                remain,
                            )
                else:
                    if spread > self.cfg.spread_threshold and not can_use_maker:
                        quote_state = "fresh" if quote_fresh else "stale"
                        print(
                            f"[GLB_LIQ][QUOTE] token={token_id} spread={spread:.4f} 但报价{quote_state}/ask无效，"
                            "跳过 Maker，直接 Taker IOC"
                        )
                    taker_count += 1
                    self._place_sell_ioc(client, token_id, self._compute_taker_price(bid=bid, ask=ask), size)

                liquidated += 1
                liquidated_tokens.add(token_id)
            except Exception as exc:
                errors.append(f"token={token_id}: {exc}")

        return {
            "liquidated": liquidated,
            "maker_count": maker_count,
            "taker_count": taker_count,
            "liquidated_tokens": sorted(liquidated_tokens),
            "errors": errors,
        }

    def _append_blacklist_tokens(self, token_ids: List[str]) -> int:
        normalized = {str(tid).strip() for tid in token_ids if str(tid).strip()}
        if not normalized:
            return 0

        existing, payload = self._load_blacklist_payload()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rows = payload.get("tokens") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []

        row_map: Dict[str, Dict[str, Any]] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            token_id = item.get("token_id") or item.get("tokenId")
            if token_id is None or not str(token_id).strip():
                continue
            row_map[str(token_id).strip()] = dict(item)

        added = 0
        for token_id in sorted(normalized):
            if token_id in existing:
                continue
            row_map[token_id] = {
                "token_id": token_id,
                "blocked_at": now_iso,
                "source": "total_liquidation",
                "reason": "global-liquidation",
            }
            added += 1

        if added <= 0:
            return 0

        payload = {
            "updated_at": now_iso,
            "tokens": sorted(row_map.values(), key=lambda x: str(x.get("token_id") or "")),
        }
        self._safe_write_json(self.blacklist_path, payload)
        print(f"[GLB_LIQ][BLACKLIST] 新增 {added} 个 token 到黑名单")
        return added

    def _load_blacklist_payload(self) -> Tuple[set[str], Dict[str, Any]]:
        if not self.blacklist_path.exists():
            return set(), {}
        try:
            with self.blacklist_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            rows = payload.get("tokens") if isinstance(payload, dict) else []
            out: set[str] = set()
            if isinstance(rows, list):
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    token_id = item.get("token_id") or item.get("tokenId")
                    if token_id is not None and str(token_id).strip():
                        out.add(str(token_id).strip())
            return out, payload if isinstance(payload, dict) else {}
        except Exception:
            return set(), {}

    @staticmethod
    def _safe_write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            suffix=".tmp",
            prefix=".liq_",
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

    def _reset_copytrade_state_files(self) -> None:
        copytrade_dir = self.project_root.parent / "copytrade"
        copytrade_dir.mkdir(parents=True, exist_ok=True)

        targets = {
            "tokens_from_copytrade.json": {"updated_at": "", "tokens": []},
            "copytrade_sell_signals.json": {"updated_at": "", "sell_tokens": []},
            "copytrade_state.json": {"targets": {}},
            "sell_tokens_from_copytrade.json": {"updated_at": "", "sell_tokens": []},
        }
        for filename, payload in targets.items():
            self._safe_write_json(copytrade_dir / filename, payload)

    def _hard_reset_files(self, autorun: Any) -> None:
        print("[GLB_LIQ] 执行一刀切重置: logs/json")

        if self.cfg.remove_logs and autorun.config.log_dir.exists():
            for path in sorted(autorun.config.log_dir.rglob("*"), reverse=True):
                try:
                    if path.is_file() and path.suffix.lower() in {".log", ".tmp"}:
                        path.unlink(missing_ok=True)
                    elif path.is_dir() and path != autorun.config.log_dir and not any(path.iterdir()):
                        path.rmdir()
                except OSError:
                    continue

        if self.cfg.remove_json_state and autorun.config.data_dir.exists():
            keep_names = {"total_liquidation_state.json", "token_cycle_gate.json"}
            for path in autorun.config.data_dir.glob("*.json"):
                if path.name in keep_names:
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue

        if self.cfg.remove_json_state:
            self._reset_copytrade_state_files()

        autorun.config.log_dir.mkdir(parents=True, exist_ok=True)
        autorun.config.data_dir.mkdir(parents=True, exist_ok=True)
