def test_ensure_resume_state_sets_skip_buy_when_live_position_found(
    manager_factory,
):
    manager = manager_factory()
    manager.topic_details = {"tok": {"queue_role": "refill_with_position"}}
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=False: (
            [{"asset": "tok", "size": 12.0, "avgPrice": 0.41}],
            {"tok": 12.0},
            "ok",
            "live",
        )
    )
    manager._has_actionable_position = lambda token_id, size, row=None: size > 0

    manager._ensure_resume_state_from_live_position("tok")

    detail = manager.topic_details["tok"]
    assert detail["resume_state"]["has_position"] is True
    assert detail["resume_state"]["position_size"] == 12.0
    assert detail["resume_state"]["entry_price"] == 0.41
    assert detail["resume_state"]["skip_buy"] is True
    assert detail["startup_skip_if_open_sell"] is True


def test_ensure_resume_state_uses_stoploss_fallback_entry_price(manager_factory):
    manager = manager_factory()
    manager.topic_details = {
        "tok": {
            "queue_role": "startup_reconcile_position",
            "entry_price": 0.0,
            "last_buy_price": 0.0,
        }
    }
    manager._stoploss_reentry_states = {
        "tok": {"old_entry_price": 0.55, "old_last_buy_price": 0.53}
    }
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=False: ([{"asset": "tok", "size": 10.0}], {"tok": 10.0}, "ok", "live")
    )
    manager._has_actionable_position = lambda token_id, size, row=None: size > 0

    manager._ensure_resume_state_from_live_position("tok")

    # Runtime prefers old_last_buy_price first, then old_entry_price.
    assert manager.topic_details["tok"]["resume_state"]["entry_price"] == 0.53


def test_reconcile_restore_returns_unavailable_when_snapshot_unavailable(
    manager_factory,
):
    manager = manager_factory()
    manager.topic_details = {
        "tok": {
            "queue_role": "startup_reconcile_position",
            "resume_state": {"has_position": True},
        }
    }
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=True: ([], {}, "network_error", "live_error")
    )

    result = manager._reconcile_position_restore_before_start("tok")

    assert result == "position_snapshot_unavailable"
    assert manager.topic_details["tok"]["queue_role"] == "startup_reconcile_position"


def test_reconcile_restore_confirms_live_position_and_keeps_position_path(
    manager_factory,
):
    manager = manager_factory()
    manager.topic_details = {
        "tok": {
            "queue_role": "refill_with_position",
            "resume_state": {"has_position": True},
        }
    }
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=True: ([], {"tok": 8.0}, "ok", "live")
    )
    manager._has_actionable_position = lambda token_id, size, row=None: size > 0
    called = {"count": 0}
    manager._ensure_resume_state_from_live_position = (
        lambda token_id: called.__setitem__("count", called["count"] + 1)
    )

    result = manager._reconcile_position_restore_before_start("tok")

    assert result == "position_confirmed"
    assert called["count"] == 1
    assert manager.topic_details["tok"]["queue_role"] == "refill_with_position"


def test_reconcile_restore_downgrades_startup_reconcile_position_to_buy(
    manager_factory,
):
    manager = manager_factory()
    manager.topic_details = {
        "tok": {
            "queue_role": "startup_reconcile_position",
            "resume_state": {"has_position": True},
            "entry_price": 0.42,
            "last_buy_price": 0.42,
            "floor_price": 0.43,
            "sell_trigger_price": 0.44,
            "startup_reconcile_requeue_count": 2,
        }
    }
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=True: ([], {"tok": 0.0}, "ok", "live")
    )
    manager._has_actionable_position = lambda token_id, size, row=None: size > 0

    result = manager._reconcile_position_restore_before_start("tok")

    detail = manager.topic_details["tok"]
    assert result == "downgraded_to_buy"
    assert detail["queue_role"] == "startup_reconcile_buy"
    assert "resume_state" not in detail
    assert "startup_reconcile_requeue_count" not in detail
    assert "entry_price" not in detail
    assert "sell_trigger_price" not in detail


def test_reconcile_restore_downgrades_refill_with_position_to_refill_buy(
    manager_factory,
):
    manager = manager_factory()
    manager.topic_details = {
        "tok": {
            "queue_role": "refill_with_position",
            "resume_state": {"has_position": True},
        }
    }
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=True: ([], {"tok": 0.0}, "ok", "live")
    )
    manager._has_actionable_position = lambda token_id, size, row=None: size > 0

    result = manager._reconcile_position_restore_before_start("tok")

    assert result == "downgraded_to_refill_buy"
    assert manager.topic_details["tok"]["queue_role"] == "refill_buy"


def test_reconcile_restore_drops_stale_restored_token(manager_factory):
    manager = manager_factory()
    manager.topic_details = {
        "tok": {
            "queue_role": "restored_token",
            "resume_state": {"has_position": True},
        }
    }
    manager.tasks = {"tok": type("Task", (), {"is_running": lambda self: False})()}
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=True: ([], {"tok": 0.0}, "ok", "live")
    )
    manager._has_actionable_position = lambda token_id, size, row=None: size > 0
    recorded = []
    purged = []
    removed = []
    manager._append_exit_token_record = (
        lambda token_id, reason, exit_data=None, refillable=False: recorded.append(
            {"token_id": token_id, "reason": reason}
        )
    )
    manager._remove_pending_topic = lambda token_id: removed.append(token_id)
    manager._purge_token_runtime_state = lambda token_id: purged.append(token_id)
    manager._remove_from_handled_topics = lambda token_id: removed.append(f"handled:{token_id}")

    result = manager._reconcile_position_restore_before_start("tok")

    assert result == "dropped_restored_token"
    assert recorded and recorded[-1]["reason"] == "RESTORE_RUNTIME_NO_POSITION"
    assert purged == ["tok"]
    assert "tok" not in manager.tasks
