from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Dict, Optional, Tuple


class ShockPhase(str, Enum):
    NORMAL = "NORMAL"
    HOLDING = "HOLDING"
    RECOVERY_CHECK = "RECOVERY_CHECK"
    BLOCKED = "BLOCKED"


class GateDecision(str, Enum):
    ALLOW = "ALLOW"
    DEFER = "DEFER"
    REJECT = "REJECT"


@dataclass
class RecoveryConfig:
    rebound_pct_min: float = 0.05
    reconfirm_sec: float = 30.0
    spread_cap: Optional[float] = 0.03
    require_conditions: int = 2


@dataclass
class ShockGuardConfig:
    enabled: bool = False
    shock_window_sec: float = 30.0
    shock_drop_pct: float = 0.20
    shock_velocity_pct_per_sec: Optional[float] = None
    shock_abs_floor: Optional[float] = 0.03
    observation_hold_sec: float = 90.0
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    blocked_cooldown_sec: float = 300.0
    max_pending_buy_age_sec: float = 180.0


@dataclass
class GateResult:
    decision: GateDecision
    reason: str
    retry_at_ts: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)


class ShockGuard:
    """前置急跌门禁：检测急跌 -> 冻结观察 -> 恢复确认。"""

    def __init__(self, token_id: str, config: ShockGuardConfig):
        self.token_id = token_id
        self.cfg = config

        history_window = max(
            self.cfg.shock_window_sec,
            self.cfg.recovery.reconfirm_sec,
            self.cfg.observation_hold_sec,
            1.0,
        )
        self._history_window_sec = float(history_window) * 2.0
        self._history: Deque[Tuple[float, float, float]] = deque()  # ts, mid, spread

        self._phase: ShockPhase = ShockPhase.NORMAL
        self._hold_until_ts: Optional[float] = None
        self._blocked_until_ts: Optional[float] = None

        self._shock_trigger_ts: Optional[float] = None
        self._shock_anchor_high: Optional[float] = None
        self._shock_low: Optional[float] = None
        self._last_low_ts: Optional[float] = None
        self._holding_has_shock_evidence: bool = False
        self._last_mid: Optional[float] = None
        self._last_spread: Optional[float] = None

        self._quote_unhealthy_since_ts: Optional[float] = None
        self._quote_unhealthy_reason: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled)

    def status(self, now: Optional[float] = None) -> Dict[str, Any]:
        if now is None:
            now = self._history[-1][0] if self._history else None
        hold_remaining = None
        block_remaining = None
        if now is not None and self._hold_until_ts is not None:
            hold_remaining = max(self._hold_until_ts - now, 0.0)
        if now is not None and self._blocked_until_ts is not None:
            block_remaining = max(self._blocked_until_ts - now, 0.0)

        return {
            "enabled": self.enabled,
            "phase": self._phase.value,
            "hold_until_ts": self._hold_until_ts,
            "blocked_until_ts": self._blocked_until_ts,
            "hold_remaining_sec": hold_remaining,
            "blocked_remaining_sec": block_remaining,
            "shock_trigger_ts": self._shock_trigger_ts,
            "shock_anchor_high": self._shock_anchor_high,
            "shock_low": self._shock_low,
            "last_low_ts": self._last_low_ts,
            "holding_has_shock_evidence": self._holding_has_shock_evidence,
            "last_mid": self._last_mid,
            "last_spread": self._last_spread,
            "quote_unhealthy_since_ts": self._quote_unhealthy_since_ts,
            "quote_unhealthy_reason": self._quote_unhealthy_reason,
        }

    def on_market_snapshot(self, *, bid: Optional[float], ask: Optional[float], ts: float) -> None:
        if not self.enabled:
            return

        bid_ok = self._is_valid_quote_side(bid)
        ask_ok = self._is_valid_quote_side(ask)
        if not (bid_ok and ask_ok):
            if self._quote_unhealthy_since_ts is None:
                self._quote_unhealthy_since_ts = ts
            missing = []
            if not bid_ok:
                missing.append("bid")
            if not ask_ok:
                missing.append("ask")
            self._quote_unhealthy_reason = f"quote side missing/invalid: {','.join(missing)}"
            self._last_spread = None
            self._advance_timers(ts)
            return

        self._quote_unhealthy_since_ts = None
        self._quote_unhealthy_reason = None

        mid = self._calc_mid(bid, ask)
        spread = self._calc_spread(bid, ask)
        if mid is None:
            self._advance_timers(ts)
            return

        self._history.append((ts, mid, spread if spread is not None else 0.0))
        self._trim_history(ts)
        self._last_mid = mid
        self._last_spread = spread

        self._advance_timers(ts)
        if self._phase == ShockPhase.NORMAL:
            detected = self._detect_shock(ts)
            if detected is not None:
                self._enter_holding(ts, detected, has_shock_evidence=True)
        elif self._phase in (ShockPhase.HOLDING, ShockPhase.RECOVERY_CHECK):
            if self._detect_shock(ts) is not None:
                self._holding_has_shock_evidence = True
            if self._shock_low is None or mid < self._shock_low:
                self._shock_low = mid
                self._last_low_ts = ts

    def gate_buy(self, *, ts: float) -> GateResult:
        if not self.enabled:
            return GateResult(GateDecision.ALLOW, "shock_guard disabled")

        self._advance_timers(ts)

        if self._quote_unhealthy_since_ts is not None:
            timeout = max(float(self.cfg.max_pending_buy_age_sec), 0.0)
            unhealthy_age = max(ts - self._quote_unhealthy_since_ts, 0.0)
            detail = {
                "quote_unhealthy_reason": self._quote_unhealthy_reason,
                "quote_unhealthy_since_ts": self._quote_unhealthy_since_ts,
                "quote_unhealthy_age_sec": unhealthy_age,
                "release_timeout_sec": timeout,
            }
            if timeout > 0 and unhealthy_age >= timeout:
                return GateResult(
                    GateDecision.REJECT,
                    "quote unhealthy timeout reached; force release for refill",
                    retry_at_ts=ts,
                    details=detail,
                )
            retry_at = (self._quote_unhealthy_since_ts + timeout) if timeout > 0 else None
            return GateResult(
                GateDecision.DEFER,
                "quote unhealthy; buy blocked by shock gate",
                retry_at_ts=retry_at,
                details=detail,
            )

        if self._phase == ShockPhase.NORMAL:
            detected = self._detect_shock(ts)
            observe_reason = "pre-buy observation window"
            detail: Dict[str, Any] = {"reason": observe_reason}
            has_shock_evidence = False
            if detected is not None:
                observe_reason = f"pre-buy hold with shock evidence: {detected.get('reason', 'detected')}"
                detail.update(detected)
                has_shock_evidence = True
            self._enter_holding(ts, detail, has_shock_evidence=has_shock_evidence)
            return GateResult(
                GateDecision.DEFER,
                observe_reason,
                retry_at_ts=self._hold_until_ts,
                details=detail,
            )

        if self._phase == ShockPhase.HOLDING:
            return GateResult(
                GateDecision.DEFER,
                "observation hold active",
                retry_at_ts=self._hold_until_ts,
            )

        if self._phase == ShockPhase.BLOCKED:
            retry_at = self._blocked_until_ts
            return GateResult(
                GateDecision.REJECT,
                "shock guard blocked cooldown active",
                retry_at_ts=retry_at,
            )

        # RECOVERY_CHECK
        if not self._holding_has_shock_evidence:
            # 纯观察窗口：不应因为恢复条件不满足而阻断买入；仅在仍检测到shock时继续延后
            detected = self._detect_shock(ts)
            if detected is None:
                self._reset_to_normal()
                return GateResult(GateDecision.ALLOW, "pre-buy observation completed", details={"observed": True})
            detail = dict(detected)
            detail["reason"] = f"post-observation shock detected: {detected.get('reason', 'detected')}"
            self._enter_holding(ts, detail, has_shock_evidence=True)
            return GateResult(
                GateDecision.DEFER,
                detail["reason"],
                retry_at_ts=self._hold_until_ts,
                details=detail,
            )

        passed, detail = self._evaluate_recovery(ts)
        if passed:
            # 最终放行前再做一次完整 shock 检测，避免恢复路径绕过 abs_floor/drop/velocity
            post_detect = self._detect_shock(ts)
            if post_detect is not None:
                rebound_detail = dict(detail)
                rebound_detail["post_detect_reason"] = post_detect.get("reason")
                rebound_detail.update(post_detect)
                self._enter_holding(ts, rebound_detail, has_shock_evidence=True)
                return GateResult(
                    GateDecision.DEFER,
                    "recovery passed but shock still detected",
                    retry_at_ts=self._hold_until_ts,
                    details=rebound_detail,
                )
            self._reset_to_normal()
            return GateResult(GateDecision.ALLOW, "recovery validation passed", details=detail)

        self._phase = ShockPhase.BLOCKED
        self._blocked_until_ts = ts + max(float(self.cfg.blocked_cooldown_sec), 0.0)
        return GateResult(
            GateDecision.REJECT,
            "recovery validation failed, enter blocked cooldown",
            retry_at_ts=self._blocked_until_ts,
            details=detail,
        )

    def _advance_timers(self, ts: float) -> None:
        if self._phase == ShockPhase.BLOCKED and self._blocked_until_ts is not None and ts >= self._blocked_until_ts:
            self._reset_to_normal()
        if self._phase == ShockPhase.HOLDING and self._hold_until_ts is not None and ts >= self._hold_until_ts:
            self._phase = ShockPhase.RECOVERY_CHECK

    def _enter_holding(self, ts: float, detail: Dict[str, Any], *, has_shock_evidence: bool) -> None:
        self._phase = ShockPhase.HOLDING
        self._shock_trigger_ts = ts
        self._hold_until_ts = ts + max(float(self.cfg.observation_hold_sec), 0.0)
        self._blocked_until_ts = None
        self._shock_anchor_high = float(detail.get("window_high") or 0.0) or self._last_mid
        self._shock_low = self._last_mid
        self._last_low_ts = ts
        self._holding_has_shock_evidence = bool(has_shock_evidence)

    def _reset_to_normal(self) -> None:
        self._phase = ShockPhase.NORMAL
        self._hold_until_ts = None
        self._blocked_until_ts = None
        self._shock_trigger_ts = None
        self._shock_anchor_high = None
        self._shock_low = None
        self._last_low_ts = None
        self._holding_has_shock_evidence = False

    def _detect_shock(self, ts: float) -> Optional[Dict[str, Any]]:
        if self._last_mid is None:
            return None

        window_start = ts - max(float(self.cfg.shock_window_sec), 1.0)
        highs = [mid for t, mid, _ in self._history if t >= window_start and mid > 0]
        if not highs:
            return None
        window_high = max(highs)
        drop_ratio = 0.0
        if window_high > 0:
            drop_ratio = (window_high - self._last_mid) / window_high

        if drop_ratio >= max(float(self.cfg.shock_drop_pct), 0.0):
            return {
                "reason": f"drop_ratio {drop_ratio:.4f} >= shock_drop_pct {self.cfg.shock_drop_pct:.4f}",
                "drop_ratio": drop_ratio,
                "window_high": window_high,
                "last_mid": self._last_mid,
            }

        if self.cfg.shock_abs_floor is not None and self._last_mid <= float(self.cfg.shock_abs_floor):
            return {
                "reason": f"mid {self._last_mid:.5f} <= shock_abs_floor {float(self.cfg.shock_abs_floor):.5f}",
                "drop_ratio": drop_ratio,
                "window_high": window_high,
                "last_mid": self._last_mid,
            }

        vel_limit = self.cfg.shock_velocity_pct_per_sec
        if vel_limit is not None:
            earliest: Optional[Tuple[float, float]] = None
            for t, mid, _ in self._history:
                if t >= window_start:
                    earliest = (t, mid)
                    break
            if earliest is not None:
                dt = ts - earliest[0]
                if dt > 0 and earliest[1] > 0:
                    velocity = (self._last_mid - earliest[1]) / earliest[1] / dt
                    if velocity <= -abs(float(vel_limit)):
                        return {
                            "reason": f"velocity {velocity:.6f} <= -{abs(float(vel_limit)):.6f}",
                            "drop_ratio": drop_ratio,
                            "window_high": window_high,
                            "last_mid": self._last_mid,
                            "velocity": velocity,
                            "window_dt": dt,
                        }

        return None

    def _evaluate_recovery(self, ts: float) -> Tuple[bool, Dict[str, Any]]:
        cfg = self.cfg.recovery
        details: Dict[str, Any] = {}
        met = 0

        low = self._shock_low
        last_mid = self._last_mid
        rebound_ratio = 0.0
        rebound_ok = False
        if low is not None and low > 0 and last_mid is not None:
            rebound_ratio = (last_mid - low) / low
            rebound_ok = rebound_ratio >= max(float(cfg.rebound_pct_min), 0.0)
        details["rebound_ratio"] = rebound_ratio
        details["rebound_ok"] = rebound_ok
        if rebound_ok:
            met += 1

        reconfirm_ok = True
        if self._last_low_ts is not None:
            reconfirm_ok = (ts - self._last_low_ts) >= max(float(cfg.reconfirm_sec), 0.0)
        details["reconfirm_ok"] = reconfirm_ok
        details["since_last_low_sec"] = (ts - self._last_low_ts) if self._last_low_ts is not None else None
        if reconfirm_ok:
            met += 1

        spread_ok = True
        spread_cap = cfg.spread_cap
        if spread_cap is not None:
            spread_ok = self._last_spread is not None and self._last_spread <= float(spread_cap)
        details["spread_ok"] = spread_ok
        details["last_spread"] = self._last_spread
        if spread_ok:
            met += 1

        require = max(int(cfg.require_conditions), 1)
        details["conditions_met"] = met
        details["conditions_required"] = require
        return met >= require, details

    def _trim_history(self, ts: float) -> None:
        cutoff = ts - self._history_window_sec
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    @staticmethod
    def _is_valid_quote_side(px: Optional[float]) -> bool:
        if px is None:
            return False
        try:
            v = float(px)
        except (TypeError, ValueError):
            return False
        return v > 0

    @staticmethod
    def _calc_mid(bid: float, ask: float) -> Optional[float]:
        if bid is None or ask is None:
            return None
        try:
            b = float(bid)
            a = float(ask)
        except (TypeError, ValueError):
            return None
        if b <= 0 or a <= 0:
            return None
        return (a + b) / 2.0

    @staticmethod
    def _calc_spread(bid: float, ask: float) -> Optional[float]:
        try:
            b = float(bid)
            a = float(ask)
        except (TypeError, ValueError):
            return None
        if a <= 0 or b <= 0:
            return None
        return max(a - b, 0.0)
