def test_default_stoploss_reentry_state_applies_min_threshold(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_base_drawdown_pct = 0.0

    state = manager._default_stoploss_reentry_state("tok")

    assert state["token_id"] == "tok"
    assert state["version"] == 4
    assert state["state"] == "NORMAL_MAKER"
    assert state["next_stoploss_threshold_pct"] == 0.001


def test_normalize_stoploss_state_clamps_values_and_cleans_release_fields(
    manager_factory,
):
    manager = manager_factory()
    raw = {
        "state": "NORMAL_MAKER",
        "stoploss_cycle_count": -5,
        "stoploss_confirm_hits": -3,
        "daily_stoploss_full_clear_count": -2,
        "next_stoploss_threshold_pct": -0.2,
        "reentry_quote_missing_hits": -1,
        "market_status_last": "source_detached_guard_hold",
        "last_error": "source_detached guard hold (within grace)",
        "pending_stoploss_before_size": 3.0,
        "pending_stoploss_after_size": 1.0,
    }

    state = manager._normalize_stoploss_reentry_state_record("tok", raw)

    assert state["stoploss_cycle_count"] == 0
    assert state["stoploss_confirm_hits"] == 0
    assert state["daily_stoploss_full_clear_count"] == 0
    assert state["next_stoploss_threshold_pct"] >= 0.001
    assert state["reentry_quote_missing_hits"] == 0
    assert state["market_status_last"] == "source_detached"
    assert state["last_error"] == ""
    assert "pending_stoploss_before_size" not in state
    assert "pending_stoploss_after_size" not in state
    assert state["reentry_target_size"] == 0.0
    assert state["reentry_maker_order_id"] == ""


def test_rollover_stoploss_daily_fields(autorun_mod):
    state = {
        "loss_date_utc": "2026-03-27",
        "daily_stoploss_full_clear_count": 3,
        "today_realized_loss_pct": 0.12,
        "reentry_paused_for_day": True,
    }

    assert autorun_mod.AutoRunManager._rollover_stoploss_daily_fields(state, "2026-03-28") is True
    assert state["loss_date_utc"] == "2026-03-28"
    assert state["daily_stoploss_full_clear_count"] == 0
    assert state["today_realized_loss_pct"] == 0.0
    assert state["reentry_paused_for_day"] is False
    assert autorun_mod.AutoRunManager._rollover_stoploss_daily_fields(state, "2026-03-28") is False


def test_sync_stoploss_pause_status_fields(autorun_mod):
    state = {"reentry_paused_for_day": True, "market_status_last": ""}
    autorun_mod.AutoRunManager._sync_stoploss_pause_status_fields(state)
    assert state["market_status_last"] == "reentry_paused_for_day"

    state = {"reentry_paused_for_day": False, "market_status_last": "reentry_paused_for_day"}
    autorun_mod.AutoRunManager._sync_stoploss_pause_status_fields(state)
    assert state["market_status_last"] == ""


def test_stoploss_threshold_ticks_increase_with_cycle_count(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_reference_tick_size = 0.01
    manager.config.stoploss_drawdown_step_per_cycle_ticks = 1

    base = manager._stoploss_threshold_ticks(
        anchor_price=0.5,
        threshold_pct=0.05,
        tick=0.01,
        cycle_count=0,
    )
    higher = manager._stoploss_threshold_ticks(
        anchor_price=0.5,
        threshold_pct=0.05,
        tick=0.01,
        cycle_count=3,
    )

    assert base >= 1
    assert higher > base


def test_stoploss_spread_extra_ticks_applies_tiers(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_spread_extra_tick_tier_1_pct = 0.03
    manager.config.stoploss_spread_extra_tick_tier_1_ticks = 1
    manager.config.stoploss_spread_extra_tick_tier_2_pct = 0.05
    manager.config.stoploss_spread_extra_tick_tier_2_ticks = 2
    manager.config.stoploss_spread_extra_tick_tier_3_pct = 0.08
    manager.config.stoploss_spread_extra_tick_tier_3_ticks = 4

    assert manager._stoploss_spread_extra_ticks(0.02) == 0
    assert manager._stoploss_spread_extra_ticks(0.03) == 1
    assert manager._stoploss_spread_extra_ticks(0.06) == 2
    assert manager._stoploss_spread_extra_ticks(0.09) == 4


def test_build_stoploss_reentry_band_is_monotonic(manager_factory):
    manager = manager_factory()
    manager.config.stoploss_reference_tick_size = 0.01
    manager._estimate_token_tick_size = lambda token_id, position_row=None: 0.01

    line, lower, probe = manager._build_stoploss_reentry_band(
        token_id="tok",
        exec_price=0.60,
        line_ticks=2,
        zone_lower_pct=0.02,
        probe_break_pct=0.05,
    )

    assert 0.0 <= probe < lower < line < 0.60


def test_stoploss_market_closed_detection(manager_factory):
    manager = manager_factory()
    manager._ws_cache = {"tok": {"is_closed": True}, "tok2": {"closed": False}}

    assert manager._stoploss_is_market_closed("tok") is True
    assert manager._stoploss_is_market_closed("tok2") is False
