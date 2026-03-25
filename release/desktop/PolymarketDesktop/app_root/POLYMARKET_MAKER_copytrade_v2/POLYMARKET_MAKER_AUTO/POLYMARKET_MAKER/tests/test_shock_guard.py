from shock_guard import (
    GateDecision,
    RecoveryConfig,
    ShockGuard,
    ShockGuardConfig,
)


def _build_guard(**kwargs):
    cfg = ShockGuardConfig(
        enabled=True,
        shock_window_sec=20.0,
        shock_drop_pct=0.20,
        observation_hold_sec=30.0,
        recovery=RecoveryConfig(
            rebound_pct_min=0.05,
            reconfirm_sec=10.0,
            spread_cap=0.03,
            require_conditions=2,
        ),
        blocked_cooldown_sec=40.0,
    )
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return ShockGuard("tok", cfg)


def test_disabled_guard_always_allows():
    guard = ShockGuard("tok", ShockGuardConfig(enabled=False))
    guard.on_market_snapshot(bid=0.5, ask=0.52, ts=1.0)
    result = guard.gate_buy(ts=1.0)
    assert result.decision == GateDecision.ALLOW


def test_pre_buy_observation_always_defers_first_buy():
    guard = _build_guard()
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=1.0)
    result = guard.gate_buy(ts=2.0)
    assert result.decision == GateDecision.DEFER
    assert "pre-buy" in result.reason


def test_recovery_pass_allows_after_hold():
    guard = _build_guard()
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=1.0)
    assert guard.gate_buy(ts=2.0).decision == GateDecision.DEFER

    # hold结束后先出现低点，再等待确认窗口并反弹，满足恢复条件
    guard.on_market_snapshot(bid=0.50, ask=0.52, ts=36.0)
    guard.on_market_snapshot(bid=0.58, ask=0.60, ts=48.0)
    result = guard.gate_buy(ts=48.0)
    assert result.decision == GateDecision.ALLOW


def test_recovery_fail_enters_blocked_and_then_restarts_observation():
    guard = _build_guard()
    guard.cfg.recovery.require_conditions = 3
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=1.0)
    assert guard.gate_buy(ts=2.0).decision == GateDecision.DEFER

    # hold阶段内先出现高位再急跌，形成shock证据；恢复条件要求3项时应fail
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=30.0)
    guard.on_market_snapshot(bid=0.40, ask=0.50, ts=36.0)
    result_fail = guard.gate_buy(ts=36.0)
    assert result_fail.decision == GateDecision.REJECT

    # blocked期间持续拒绝
    blocked_result = guard.gate_buy(ts=50.0)
    assert blocked_result.decision == GateDecision.REJECT

    # cooldown结束回到normal，但由于每次买前强制观察，应再次 DEFER
    guard.on_market_snapshot(bid=0.48, ask=0.50, ts=80.0)
    final_result = guard.gate_buy(ts=80.0)
    assert final_result.decision == GateDecision.DEFER


def test_non_shock_observation_not_blocked_by_wide_spread_recovery_check():
    guard = _build_guard()
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=1.0)
    assert guard.gate_buy(ts=2.0).decision == GateDecision.DEFER

    # 无shock证据的纯观察窗口结束后，即使点差偏大也不应进入blocked
    guard.on_market_snapshot(bid=0.58, ask=0.70, ts=36.0)
    result = guard.gate_buy(ts=36.0)
    assert result.decision == GateDecision.ALLOW


def test_recovery_pass_still_defers_when_abs_floor_hit_before_buy():
    guard = _build_guard(shock_abs_floor=0.55)
    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=1.0)
    assert guard.gate_buy(ts=2.0).decision == GateDecision.DEFER

    # 进入恢复检查前后价格均在绝对阈值下方，放行前应再次触发完整检测并继续DEFER
    guard.on_market_snapshot(bid=0.50, ask=0.52, ts=36.0)
    guard.on_market_snapshot(bid=0.53, ask=0.55, ts=48.0)
    result = guard.gate_buy(ts=48.0)
    assert result.decision == GateDecision.DEFER
    assert "shock" in result.reason.lower()


def test_unhealthy_quote_side_blocks_buy_until_timeout_then_rejects():
    guard = _build_guard(max_pending_buy_age_sec=15.0)

    guard.on_market_snapshot(bid=0.60, ask=None, ts=10.0)
    defer = guard.gate_buy(ts=12.0)
    assert defer.decision == GateDecision.DEFER
    assert "quote unhealthy" in defer.reason

    reject = guard.gate_buy(ts=26.0)
    assert reject.decision == GateDecision.REJECT
    assert "force release" in reject.reason


def test_unhealthy_quote_side_recovers_after_quotes_become_valid():
    guard = _build_guard(max_pending_buy_age_sec=20.0)

    guard.on_market_snapshot(bid=0.60, ask=None, ts=10.0)
    assert guard.gate_buy(ts=12.0).decision == GateDecision.DEFER

    guard.on_market_snapshot(bid=0.60, ask=0.62, ts=13.0)
    result = guard.gate_buy(ts=13.0)
    assert result.decision == GateDecision.DEFER
    assert "pre-buy" in result.reason
