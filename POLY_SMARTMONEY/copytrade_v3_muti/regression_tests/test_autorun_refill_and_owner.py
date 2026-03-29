import time


def test_effective_refill_retry_limit_policy(manager_factory):
    manager = manager_factory()
    manager.config.max_refill_retries = 6

    assert manager._effective_refill_retry_limit("NO_DATA_TIMEOUT") == 2
    assert manager._effective_refill_retry_limit("SHARED_WS_UNAVAILABLE") >= 10**8
    assert manager._effective_refill_retry_limit("LOW_BALANCE_PAUSE") >= 10**8
    assert manager._effective_refill_retry_limit("SELL_ABANDONED") == 6


def test_build_gap_skip_backoff_uses_latest_streak(manager_factory):
    manager = manager_factory()
    manager.config.gap_skip_backoff_enabled = True
    manager.config.gap_skip_backoff_minutes = [5.0, 15.0, 45.0]
    records = [
        {"token_id": "tok", "exit_reason": "POSITION_SYNC_SKIP_GAP", "exit_ts": 300.0},
        {"token_id": "tok", "exit_reason": "REFILL_SKIP_GAP", "exit_ts": 200.0},
        {"token_id": "tok", "exit_reason": "REFILL_SKIP_GAP", "exit_ts": 100.0},
        {"token_id": "other", "exit_reason": "NO_DATA_TIMEOUT", "exit_ts": 400.0},
    ]

    result = manager._build_gap_skip_backoff_seconds_by_token(records)

    assert "tok" in result
    assert result["tok"]["streak"] == 3.0
    assert result["tok"]["seconds"] == 45.0 * 60.0
    assert result["tok"]["latest_exit_ts"] == 300.0
    assert "other" not in result


def test_get_active_gap_skip_backoff_returns_remaining(manager_factory):
    manager = manager_factory()
    manager.config.gap_skip_backoff_enabled = True
    manager.config.gap_skip_backoff_minutes = [5.0]
    now = 1_000.0
    records = [
        {"token_id": "tok", "exit_reason": "REFILL_SKIP_GAP", "exit_ts": now - 60.0},
    ]

    hold = manager._get_active_gap_skip_backoff("tok", exit_records=records, now=now)

    assert hold is not None
    assert hold["seconds"] == 300.0
    assert hold["remaining_seconds"] == 240.0
    assert hold["hold_until_ts"] == (now - 60.0) + 300.0


def test_get_active_gap_skip_backoff_returns_none_when_expired(manager_factory):
    manager = manager_factory()
    manager.config.gap_skip_backoff_enabled = True
    manager.config.gap_skip_backoff_minutes = [5.0]
    now = 2_000.0
    records = [
        {"token_id": "tok", "exit_reason": "POSITION_SYNC_SKIP_GAP", "exit_ts": now - 600.0},
    ]

    hold = manager._get_active_gap_skip_backoff("tok", exit_records=records, now=now)

    assert hold is None


def test_filter_refillable_tokens_sorts_position_first_and_skips_stoploss_owner(
    manager_factory,
):
    manager = manager_factory()
    manager.config.refill_cooldown_minutes_with_position = 0.0
    manager.config.refill_cooldown_minutes_no_position = 0.0
    manager.config.max_refill_retries = 6
    manager._has_account_position = lambda token_id: token_id == "pos_tok"
    manager._stoploss_reentry_states = {
        "stop_tok": {"state": "STOPLOSS_EXITED_WAITING_PROBE"},
    }
    now = time.time()
    records = [
        {
            "token_id": "flat_tok",
            "exit_reason": "NO_DATA_TIMEOUT",
            "exit_ts": now - 500.0,
            "refillable": True,
            "exit_data": {"has_position": False},
        },
        {
            "token_id": "pos_tok",
            "exit_reason": "SELL_ABANDONED",
            "exit_ts": now - 100.0,
            "refillable": True,
            "exit_data": {"has_position": True, "position_size": 10.0},
        },
        {
            "token_id": "stop_tok",
            "exit_reason": "NO_DATA_TIMEOUT",
            "exit_ts": now - 200.0,
            "refillable": True,
            "exit_data": {"has_position": False},
        },
    ]

    refillable = manager._filter_refillable_tokens(records)
    token_order = [row["token_id"] for row in refillable]

    assert token_order == ["pos_tok", "flat_tok"]


def test_filter_refillable_marks_stale_position_record_non_refillable(
    manager_factory,
    autorun_mod,
    monkeypatch,
):
    manager = manager_factory()
    manager.config.refill_cooldown_minutes_with_position = 0.0
    manager.config.refill_cooldown_minutes_no_position = 0.0
    manager.config.max_refill_retries = 6
    manager._has_account_position = lambda token_id: False
    monkeypatch.setattr(autorun_mod, "_atomic_json_write", lambda path, payload: None)

    now = time.time()
    records = [
        {
            "token_id": "tok",
            "exit_reason": "SELL_ABANDONED",
            "exit_ts": now - 100.0,
            "refillable": True,
            "exit_data": {"has_position": True, "position_size": 10.0},
        }
    ]

    refillable = manager._filter_refillable_tokens(records)

    assert refillable == []
    assert records[0]["refillable"] is False
    assert records[0]["stale_position_record"] is True


def test_runtime_owner_resolution_priority(manager_factory):
    manager = manager_factory()
    manager.topic_details = {
        "tok_manual": {"queue_role": "manual_intervention"},
        "tok_startup": {"queue_role": "startup_reconcile_position"},
    }
    manager._stoploss_reentry_states = {
        "tok_stoploss": {"state": "STOPLOSS_EXITED_WAITING_PROBE"},
    }
    orphan_states = {"tok_orphan": {"status": "orphaned"}}

    assert manager._resolve_runtime_owner("tok_manual", orphan_states=orphan_states) == "manual_intervention"
    assert manager._resolve_runtime_owner("tok_stoploss", orphan_states=orphan_states) == "stoploss"
    assert manager._resolve_runtime_owner("tok_orphan", orphan_states=orphan_states) == "orphan_recovery"
    assert manager._resolve_runtime_owner("tok_startup", orphan_states=orphan_states) == "startup_reconcile"
    assert manager._resolve_runtime_owner("unknown", orphan_states=orphan_states) == "none"


def test_has_higher_priority_runtime_owner(manager_factory):
    manager = manager_factory()
    manager.topic_details = {"tok": {"queue_role": "startup_reconcile_position"}}
    manager._stoploss_reentry_states = {"tok": {"state": "STOPLOSS_REENTRY_MAKER_WORKING"}}

    assert manager._has_higher_priority_runtime_owner("tok", "startup_restore") is True
    assert manager._has_higher_priority_runtime_owner("tok", "stoploss") is False
