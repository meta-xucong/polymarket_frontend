import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AUTORUN_PATH = REPO_ROOT / "POLYMARKET_MAKER_AUTO" / "poly_maker_autorun.py"
STRATEGY_PATH = (
    REPO_ROOT
    / "POLYMARKET_MAKER_AUTO"
    / "POLYMARKET_MAKER"
    / "Volatility_arbitrage_run.py"
)


def _load_autorun_module():
    sys.path.insert(0, str(AUTORUN_PATH.parent))
    spec = importlib.util.spec_from_file_location("pm_auto_test", AUTORUN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_position_resume_build_config_sets_sell_only_and_truth_context():
    mod = _load_autorun_module()
    manager = mod.AutoRunManager.__new__(mod.AutoRunManager)
    manager.run_params_template = {}
    manager.strategy_defaults = {"default": {}, "topics": {}}
    manager.topic_details = {
        "tok": {
            "queue_role": "refill_with_position",
            "resume_state": {
                "has_position": True,
                "position_size": 10.0,
                "skip_buy": True,
            },
        }
    }
    manager.config = mod.GlobalConfig()
    manager._ensure_resume_state_from_live_position = lambda topic_id: None
    manager._has_account_position = lambda topic_id: False
    manager._exit_signal_path = lambda topic_id: Path(f"/tmp/{topic_id}.json")
    manager._get_order_base_volume = lambda: 100.0
    manager._is_aggressive_mode = lambda: False
    manager._topic_price_hint = lambda topic_id, topic_info: None

    cfg = manager._build_run_config("tok")

    assert cfg["force_sell_only_on_startup"] is True
    assert cfg["force_sell_only_reason"] == "position resume"
    assert cfg["position_truth_context"] == {
        "source": "autorun_resume_state",
        "queue_role": "refill_with_position",
        "expected_position_size": 10.0,
        "skip_buy": True,
    }


def test_startup_reconcile_requeue_escalates_to_manual_intervention():
    mod = _load_autorun_module()
    manager = mod.AutoRunManager.__new__(mod.AutoRunManager)
    manager.config = mod.GlobalConfig()
    manager.topic_details = {"tok": {"queue_role": "startup_reconcile_position"}}
    manager.pending_topics = set()
    manager.pending_burst_topics = set()
    manager.pending_exit_topics = set()
    manager._active_unmanaged_rearm_blocked_until = {}
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=False: ([], {"tok": 10.0}, "ok", "live")
    )
    manager._classify_position_truth = lambda token_id, size: "ACTIONABLE"
    manager._get_active_gap_skip_backoff = lambda token_id: None
    manager._remove_pending_topic = lambda token_id: manager.pending_topics.discard(token_id)
    manager._enqueue_pending_topic = lambda token_id: manager.pending_topics.add(token_id) or True
    manager._get_task_run_config = lambda task: {}
    manager._advance_token_cycle_state_on_cleanup = lambda *args, **kwargs: None
    manager._remove_from_handled_topics = lambda *args, **kwargs: None
    manager._mark_token_cycle_closed_runtime = lambda *args, **kwargs: None

    manual_records = []
    exit_records = []
    manager._record_manual_intervention_token = (
        lambda token_id, retry_count, rc, reason="": manual_records.append(
            {
                "token_id": token_id,
                "retry_count": retry_count,
                "rc": rc,
                "reason": reason,
            }
        )
    )
    manager._append_exit_token_record = (
        lambda token_id, reason, exit_data=None, refillable=False: exit_records.append(
            {"reason": reason, "exit_data": dict(exit_data or {})}
        )
    )

    original_terminal = mod.is_position_truth_terminal
    mod.is_position_truth_terminal = lambda truth: truth == "TERMINAL"
    try:
        for idx in range(1, 4):
            task = mod.TopicTask(topic_id="tok")
            assert manager._requeue_position_reconcile_after_clean_exit(task, rc=0) is True
            if idx < 3:
                assert task.status == "pending"
                assert manager.topic_details["tok"]["queue_role"] == "startup_reconcile_position"
            else:
                assert task.status == "manual_intervention"
                assert task.end_reason == "position reconcile manual intervention required"
    finally:
        mod.is_position_truth_terminal = original_terminal

    assert manager.topic_details["tok"]["queue_role"] == "manual_intervention"
    assert manager.topic_details["tok"]["startup_reconcile_requeue_count"] == 3
    assert manager.topic_details["tok"]["force_sell_only_on_startup"] is True
    assert manual_records == [
        {
            "token_id": "tok",
            "retry_count": 3,
            "rc": 0,
            "reason": "POSITION_RECONCILE_MAX_REQUEUES",
        }
    ]
    assert exit_records[-1]["reason"] == "POSITION_RECONCILE_MANUAL_INTERVENTION_REQUIRED"


def test_position_reconcile_finalize_clears_retry_counter():
    mod = _load_autorun_module()
    manager = mod.AutoRunManager.__new__(mod.AutoRunManager)
    manager.topic_details = {
        "tok": {
            "queue_role": "startup_reconcile_position",
            "startup_reconcile_requeue_count": 2,
        }
    }
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh=False: ([], {"tok": 0.0}, "ok", "live")
    )
    manager._classify_position_truth = lambda token_id, size: "TERMINAL"
    manager._append_exit_token_record = lambda *args, **kwargs: None
    manager._get_task_run_config = lambda task: {}
    manager._advance_token_cycle_state_on_cleanup = lambda *args, **kwargs: None
    manager._remove_from_handled_topics = lambda *args, **kwargs: None
    manager._mark_token_cycle_closed_runtime = lambda *args, **kwargs: None

    original_terminal = mod.is_position_truth_terminal
    mod.is_position_truth_terminal = lambda truth: truth == "TERMINAL"
    try:
        task = mod.TopicTask(topic_id="tok")
        assert manager._finalize_position_reconcile_exit_if_flat(task, rc=0) is True
    finally:
        mod.is_position_truth_terminal = original_terminal

    assert "startup_reconcile_requeue_count" not in manager.topic_details["tok"]
    assert task.end_reason == "position reconcile cleanup success"


def test_strategy_source_keeps_skip_buy_latched_until_parent_reconciles():
    source = STRATEGY_PATH.read_text(encoding="utf-8")
    anchor = source.index('[BUY][GUARD][SKIP_BUY]')
    window = source[max(0, anchor - 200) : anchor + 3600]

    assert 'skip_buy_hard_latched = bool(force_sell_only_on_startup)' in source
    assert 'if has_position and skip_buy:' in source
    assert 'skip_buy guard: latched resume path' in window
    assert '[POSITION_TRUTH][DIVERGENCE] parent expected position ' in window
    assert window.find('if skip_buy_hard_latched:') < window.find('skip_buy = False')
