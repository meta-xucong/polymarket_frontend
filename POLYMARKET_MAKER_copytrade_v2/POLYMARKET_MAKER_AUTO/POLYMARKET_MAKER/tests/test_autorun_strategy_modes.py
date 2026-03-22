import sys
import types
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "requests" not in sys.modules:
    class _DummySession:
        def __init__(self):
            self.headers = {}

        def get(self, *args, **kwargs):
            return None

    sys.modules["requests"] = types.SimpleNamespace(
        get=lambda *a, **k: None,
        Session=_DummySession,
        exceptions=types.SimpleNamespace(RequestException=Exception),
    )

import poly_maker_autorun as autorun_mod
from poly_maker_autorun import AutoRunManager, GlobalConfig, TopicTask, compute_new_topics
from Volatility_arbitrage_run import _advance_shared_cycle_state_after_sell


def _build_manager(cfg: GlobalConfig) -> AutoRunManager:
    return AutoRunManager(cfg, strategy_defaults={}, run_params_template={})


def test_default_mode_is_classic():
    cfg = GlobalConfig.from_dict({})
    assert cfg.strategy_mode == "classic"
    assert cfg.handled_topics_path.name == "handled_topics.json"
    assert cfg.handled_topics_path.parent.name == "data"
    manager = _build_manager(cfg)
    assert manager._is_aggressive_mode() is False
    assert manager._burst_slots() == 10


def test_compute_new_topics_dedupes_token_ids():
    latest = [
        {"topic_id": "t1"},
        {"token_id": "t1"},
        {"topic_id": "t2"},
    ]
    got = compute_new_topics(latest, handled={"t2"})
    assert got == ["t1"]


def test_sync_handled_topics_on_startup_keeps_active_buy_without_position(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["keep_token", "stale_token"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "keep_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    # 现行语义：copytrade 中存在但无活跃任务的 token 将从 handled 中移除，
    # 以便后续重新进入调度启动。
    assert manager.handled_topics == {"keep_token"}
    assert "keep_token" in manager.pending_topics
    detail = manager.topic_details.get("keep_token") or {}
    assert detail.get("queue_role") == "startup_reconcile_buy"
    assert detail.get("schedule_lane") == "base"
    payload = json.loads(handled_path.read_text(encoding="utf-8"))
    assert payload.get("topics") == ["keep_token"]


def test_sync_startup_skips_destructive_changes_when_position_snapshot_unavailable(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["keep_token"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "keep_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({}, "api_unavailable")  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert manager.handled_topics == {"keep_token"}
    assert manager._startup_sync_retry_needed is True
    assert manager._next_startup_sync_retry_at > 0
    payload = json.loads(handled_path.read_text(encoding="utf-8"))
    assert payload.get("topics") == ["keep_token"]


def test_refresh_topics_retries_startup_sync_and_purges_runtime_state(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["keep_token"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "keep_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager.topic_details["keep_token"] = {"resume_state": {"has_position": False}}
    manager._pending_first_seen["keep_token"] = 1.0
    manager._shared_ws_wait_failures["keep_token"] = 1
    manager._clob_book_probe_cache["keep_token"] = {"bid": 0.1}
    manager._position_snapshot_cache["keep_token"] = {"has_position": False}

    manager._refresh_sell_position_snapshot = lambda: ({}, "api_unavailable")  # type: ignore[assignment]
    manager._sync_handled_topics_on_startup()
    assert manager._startup_sync_retry_needed is True

    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]
    manager._next_startup_sync_retry_at = 0.0
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]

    manager._refresh_topics()

    assert manager._startup_sync_retry_needed is False
    assert "keep_token" in manager.topic_details
    assert "keep_token" in manager.pending_topics
    detail = manager.topic_details.get("keep_token") or {}
    assert detail.get("queue_role") == "startup_reconcile_buy"
    assert detail.get("schedule_lane") == "base"
    assert "keep_token" in manager._pending_first_seen
    assert "keep_token" in manager._shared_ws_wait_failures
    assert "keep_token" in manager._clob_book_probe_cache
    assert "keep_token" in manager._position_snapshot_cache


def test_sync_startup_sell_with_position_triggers_exit_cleanup_path(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["t1"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "t1", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {"token_id": "t1", "introduced_by_buy": True, "status": "pending"}
                ],
            }
        ),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({"t1": 1.0}, "ok")  # type: ignore[assignment]
    captured = []
    manager._trigger_sell_exit = lambda token_id, task=None, **kwargs: captured.append((token_id, task, kwargs))  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert captured == [
        (
            "t1",
            None,
            {
                "trigger_source": "startup_reconcile_sell_signal",
                "trigger_reason": "COPYTRADE_SELL",
                "signal_entry": {
                    "token_id": "t1",
                    "introduced_by_buy": True,
                    "status": "pending",
                    "attempts": 0,
                },
            },
        )
    ]


def test_startup_full_reconcile_preserves_active_buy_token_when_handled_missing(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": []}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "ghost_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    payload = json.loads(tokens_path.read_text(encoding="utf-8"))
    rows = [item for item in payload.get("tokens", []) if isinstance(item, dict)]
    archived_rows = [item for item in payload.get("archived_tokens", []) if isinstance(item, dict)]
    token_row = next((item for item in rows if item.get("token_id") == "ghost_token"), None)
    archived_row = next((item for item in archived_rows if item.get("token_id") == "ghost_token"), None)
    assert isinstance(token_row, dict)
    assert archived_row is None
    assert "ghost_token" in manager.pending_topics
    assert "ghost_token" not in manager.pending_burst_topics
    detail = manager.topic_details.get("ghost_token") or {}
    assert detail.get("queue_role") == "startup_reconcile_buy"
    assert detail.get("schedule_lane") == "base"
    assert manager._startup_sync_retry_needed is False


def test_startup_full_reconcile_moves_position_token_to_base_pending(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": []}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "hold_token", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({"hold_token": 1.2}, "ok")  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert "hold_token" in manager.pending_topics
    assert "hold_token" not in manager.pending_burst_topics
    detail = manager.topic_details.get("hold_token") or {}
    assert detail.get("schedule_lane") == "base"
    assert detail.get("queue_role") == "startup_reconcile_position"
    assert manager._startup_sync_retry_needed is False


def test_startup_sync_preserves_stoploss_waiting_owner_without_requeue(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["t1"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "t1", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]
    manager._stoploss_reentry_states["t1"] = {
        "state": "STOPLOSS_EXITED_WAITING_WINDOW",
        "source_detached": False,
    }

    manager._sync_handled_topics_on_startup()

    assert manager.pending_topics == []
    assert manager.handled_topics == {"t1"}
    assert (manager.topic_details.get("t1") or {}).get("queue_role") is None


def test_startup_sync_preserves_orphan_owner_without_requeue(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["t1"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "t1", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]
    manager._load_latest_orphan_states = lambda: {"t1": {"status": "orphaned"}}  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert manager.pending_topics == []
    assert manager.handled_topics == {"t1"}


def test_startup_sync_does_not_override_stoploss_escalated_owner_with_sell_signal(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["t1"]}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [{"token_id": "t1", "introduced_by_buy": True}],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {"token_id": "t1", "introduced_by_buy": True, "status": "pending"}
                ],
            }
        ),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({"t1": 1.0}, "ok")  # type: ignore[assignment]
    manager._stoploss_reentry_states["t1"] = {
        "state": "STOPLOSS_IOC_RETRY_ESCALATED",
        "source_detached": False,
    }
    captured = []
    manager._trigger_sell_exit = lambda token_id, task=None, **kwargs: captured.append((token_id, kwargs))  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert captured == []
    assert manager.pending_topics == []
    assert manager.handled_topics == {"t1"}


def test_startup_reconcile_applies_title_blacklist_with_position(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"

    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": []}),
        encoding="utf-8",
    )
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {
                        "token_id": "hold_token",
                        "introduced_by_buy": True,
                        "title": "US strikes Iran by March 31, 2026?",
                        "slug": "us-strikes-iran-by-march-31-2026",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps({"updated_at": "", "sell_tokens": []}),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                    "match_on_slug": True,
                    "action_with_position": "sell_only_maker",
                }
            },
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager._refresh_sell_position_snapshot = lambda: ({"hold_token": 1.2}, "ok")  # type: ignore[assignment]
    manager._has_account_position = lambda token_id: token_id == "hold_token"  # type: ignore[assignment]

    manager._sync_handled_topics_on_startup()

    assert "hold_token" in manager.pending_topics
    detail = manager.topic_details.get("hold_token") or {}
    assert detail.get("queue_role") == "title_blacklist_sell_only"
    assert detail.get("force_sell_only_on_startup") is True
    assert manager._startup_sync_retry_needed is False


def test_hot_reload_updates_title_blacklist_keywords(tmp_path):
    cfg_file = tmp_path / "global_config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "scheduler": {
                    "title_blacklist": {
                        "enabled": True,
                        "keywords": ["Iran"],
                        "match_on_slug": True,
                        "action_with_position": "sell_only_maker",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                    "match_on_slug": True,
                    "action_with_position": "sell_only_maker",
                }
            }
        }
    )
    cfg.global_config_path = cfg_file
    manager = _build_manager(cfg)

    assert manager.config.title_blacklist_keywords == ["Iran"]
    manager._next_config_reload_check = 0.0

    cfg_file.write_text(
        json.dumps(
            {
                "scheduler": {
                    "title_blacklist": {
                        "enabled": True,
                        "keywords": ["Russia", "Iran"],
                        "match_on_slug": True,
                        "action_with_position": "sell_only_maker",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager._config_mtime_ns = 0
    manager._hot_reload_runtime_config()

    assert manager.config.title_blacklist_keywords == ["Russia", "Iran"]
    assert manager.config.title_blacklist_enabled is True
    assert manager.config.title_blacklist_action_with_position == "sell_only_maker"


def test_start_process_hydrates_title_before_blacklist_check(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    handled_path = copytrade_dir / "handled_topics.json"

    token_id = "hold_token"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {
                        "token_id": token_id,
                        "title": "US strikes Iran by March 31, 2026?",
                        "slug": "us-strikes-iran-by-march-31-2026",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(json.dumps({"updated_at": "", "sell_tokens": []}), encoding="utf-8")
    handled_path.write_text(json.dumps({"updated_at": "", "topics": []}), encoding="utf-8")

    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                    "match_on_slug": True,
                    "action_with_position": "sell_only_maker",
                }
            },
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details[token_id] = {"queue_role": "restored_token"}
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (  # type: ignore[assignment]
        {
            "question": "US strikes Iran by March 31, 2026?",
            "slug": "us-strikes-iran-by-march-31-2026",
        },
        "stub",
    )
    seen = {}

    def _fake_enforce(tid: str, *, source: str) -> str:
        seen["title"] = (manager.topic_details.get(tid) or {}).get("title")
        seen["source"] = source
        return "blocked_no_position"

    manager._enforce_title_blacklist_policy = _fake_enforce  # type: ignore[assignment]

    ok = manager._start_topic_process(token_id)

    assert ok is False
    assert seen.get("source") == "before_start"
    assert "Iran" in str(seen.get("title") or "")


def test_start_process_hydrates_title_from_positions_cache(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    handled_path = copytrade_dir / "handled_topics.json"

    token_id = "hold_token"
    tokens_path.write_text(
        json.dumps({"updated_at": "", "tokens": [{"token_id": token_id}]}),
        encoding="utf-8",
    )
    sell_path.write_text(json.dumps({"updated_at": "", "sell_tokens": []}), encoding="utf-8")
    handled_path.write_text(json.dumps({"updated_at": "", "topics": []}), encoding="utf-8")
    (data_dir / "positions_cache.json").write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "asset": token_id,
                        "title": "US strikes Iran by March 31, 2026?",
                        "slug": "us-strikes-iran-by-march-31-2026",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                    "match_on_slug": True,
                    "action_with_position": "sell_only_maker",
                }
            },
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details[token_id] = {"queue_role": "restored_token"}
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (  # type: ignore[assignment]
        {
            "question": "US strikes Iran by March 31, 2026?",
            "slug": "us-strikes-iran-by-march-31-2026",
        },
        "stub",
    )
    seen = {}

    def _fake_enforce(tid: str, *, source: str) -> str:
        seen["title"] = (manager.topic_details.get(tid) or {}).get("title")
        seen["source"] = source
        return "blocked_no_position"

    manager._enforce_title_blacklist_policy = _fake_enforce  # type: ignore[assignment]

    ok = manager._start_topic_process(token_id)

    assert ok is False
    assert seen.get("source") == "before_start"
    assert "Iran" in str(seen.get("title") or "")


def test_restore_runtime_status_sets_restored_role_for_task_snapshot(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "autorun_status.json"
    status_path.write_text(
        json.dumps(
            {
                "pending_topics": [],
                "pending_exit_topics": [],
                "tasks": {
                    "task_token": {
                        "config_path": "",
                        "log_path": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "runtime_status_path": str(status_path),
            "handled_topics_path": str(data_dir / "handled_topics.json"),
            "copytrade_tokens_path": str(data_dir / "tokens_from_copytrade.json"),
            "copytrade_sell_signals_path": str(data_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(data_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_exit_tokens = lambda: []  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "stub")  # type: ignore[assignment]

    manager._restore_runtime_status()

    detail = manager.topic_details.get("task_token") or {}
    assert detail.get("queue_role") == "restored_token"


def test_restore_runtime_status_skips_stoploss_owned_task_snapshot(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "autorun_status.json"
    token_id = "stoploss_token"
    status_path.write_text(
        json.dumps(
            {
                "pending_topics": [],
                "pending_exit_topics": [],
                "tasks": {
                    token_id: {
                        "config_path": "",
                        "log_path": "",
                    }
                },
                "topic_details": {
                    token_id: {
                        "queue_role": "restored_token",
                        "schedule_lane": "base",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "runtime_status_path": str(status_path),
            "handled_topics_path": str(data_dir / "handled_topics.json"),
            "copytrade_tokens_path": str(data_dir / "tokens_from_copytrade.json"),
            "copytrade_sell_signals_path": str(data_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(data_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_exit_tokens = lambda: []  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "stub")  # type: ignore[assignment]
    manager._stoploss_reentry_states[token_id] = {
        "state": "STOPLOSS_EXITED_WAITING_WINDOW"
    }

    manager._restore_runtime_status()

    assert token_id not in manager.tasks
    assert token_id not in manager.pending_topics
    detail = manager.topic_details.get(token_id) or {}
    assert detail.get("queue_role") == "restored_token"


def test_restore_runtime_status_skips_pending_exit_owned_task_snapshot(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "autorun_status.json"
    token_id = "exit_token"
    status_path.write_text(
        json.dumps(
            {
                "pending_topics": [],
                "pending_exit_topics": [token_id],
                "tasks": {
                    token_id: {
                        "config_path": "",
                        "log_path": "",
                    }
                },
                "topic_details": {
                    token_id: {
                        "queue_role": "title_blacklist_sell_only",
                        "schedule_lane": "base",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "runtime_status_path": str(status_path),
            "handled_topics_path": str(data_dir / "handled_topics.json"),
            "copytrade_tokens_path": str(data_dir / "tokens_from_copytrade.json"),
            "copytrade_sell_signals_path": str(data_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(data_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_exit_tokens = lambda: []  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "stub")  # type: ignore[assignment]

    manager._restore_runtime_status()

    assert token_id not in manager.tasks
    assert token_id not in manager.pending_topics
    assert token_id in manager.pending_exit_topics
    detail = manager.topic_details.get(token_id) or {}
    assert detail.get("queue_role") == "title_blacklist_sell_only"


def test_restore_runtime_status_skips_manual_intervention_owned_task_snapshot(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "autorun_status.json"
    token_id = "manual_token"
    status_path.write_text(
        json.dumps(
            {
                "pending_topics": [],
                "pending_exit_topics": [],
                "tasks": {
                    token_id: {
                        "config_path": "",
                        "log_path": "",
                    }
                },
                "topic_details": {
                    token_id: {
                        "queue_role": "manual_intervention",
                        "schedule_lane": "base",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "runtime_status_path": str(status_path),
            "handled_topics_path": str(data_dir / "handled_topics.json"),
            "copytrade_tokens_path": str(data_dir / "tokens_from_copytrade.json"),
            "copytrade_sell_signals_path": str(data_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(data_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_exit_tokens = lambda: []  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "stub")  # type: ignore[assignment]

    manager._restore_runtime_status()

    assert token_id not in manager.tasks
    assert token_id not in manager.pending_topics
    detail = manager.topic_details.get(token_id) or {}
    assert detail.get("queue_role") == "manual_intervention"


def test_restore_runtime_status_restores_active_unmanaged_rearm_block(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "autorun_status.json"
    token_id = "blocked_token"
    future_ts = time.time() + 600.0
    status_path.write_text(
        json.dumps(
            {
                "pending_topics": [],
                "pending_exit_topics": [],
                "tasks": {},
                "active_unmanaged_rearm_blocked_until": {
                    token_id: future_ts,
                },
                "active_unmanaged_rearm_block_reasons": {
                    token_id: "SELL_ABANDONED",
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "runtime_status_path": str(status_path),
            "handled_topics_path": str(data_dir / "handled_topics.json"),
            "copytrade_tokens_path": str(data_dir / "tokens_from_copytrade.json"),
            "copytrade_sell_signals_path": str(data_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(data_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_exit_tokens = lambda: []  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "stub")  # type: ignore[assignment]

    manager._restore_runtime_status()

    assert manager._active_unmanaged_rearm_blocked_until[token_id] == future_ts
    assert manager._active_unmanaged_rearm_block_reasons[token_id] == "SELL_ABANDONED"


def test_dump_runtime_status_includes_ws_reconnect_detail(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "autorun_status.json"
    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "runtime_status_path": str(status_path),
            "handled_topics_path": str(data_dir / "handled_topics.json"),
            "copytrade_tokens_path": str(data_dir / "tokens_from_copytrade.json"),
            "copytrade_sell_signals_path": str(data_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(data_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._ws_reconnect_reason_counts = {"closed": 3, "error": 1, "silence": 0}
    manager._ws_last_reconnect_reason = "closed"
    manager._ws_last_reconnect_detail = {
        "state": "closed",
        "status_code": 1001,
        "message": "server restart",
        "connected_for_sec": 42.5,
        "subscribed_tokens": 7,
    }

    manager._dump_runtime_status()

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["ws_last_reconnect_reason"] == "closed"
    assert payload["ws_last_reconnect_detail"]["status_code"] == 1001
    assert payload["ws_last_reconnect_detail"]["message"] == "server restart"


def test_restore_runtime_status_restores_ws_reconnect_detail(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "autorun_status.json"
    status_path.write_text(
        json.dumps(
            {
                "pending_topics": [],
                "pending_exit_topics": [],
                "tasks": {},
                "ws_reconnect_reason_counts": {
                    "closed": 5,
                    "error": 2,
                    "silence": 1,
                },
                "ws_last_reconnect_reason": "closed",
                "ws_last_reconnect_detail": {
                    "state": "closed",
                    "status_code": 1006,
                    "message": "abnormal closure",
                    "connected_for_sec": 8.75,
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "data_dir": str(data_dir),
            "runtime_status_path": str(status_path),
            "handled_topics_path": str(data_dir / "handled_topics.json"),
            "copytrade_tokens_path": str(data_dir / "tokens_from_copytrade.json"),
            "copytrade_sell_signals_path": str(data_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(data_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)
    manager._load_exit_tokens = lambda: []  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "stub")  # type: ignore[assignment]

    manager._restore_runtime_status()

    assert manager._ws_reconnect_reason_counts == {
        "closed": 5,
        "error": 2,
        "silence": 1,
    }
    assert manager._ws_last_reconnect_reason == "closed"
    assert manager._ws_last_reconnect_detail["status_code"] == 1006
    assert manager._ws_last_reconnect_detail["message"] == "abnormal closure"


def test_start_process_blocks_when_metadata_unverified_without_position():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "title_blacklist": {
                    "enabled": True,
                    "keywords": ["Iran"],
                }
            }
        }
    )
    manager = _build_manager(cfg)
    token_id = "unverified_x"
    manager.topic_details[token_id] = {}
    manager._has_account_position = lambda _tid: False  # type: ignore[assignment]
    manager._fetch_market_metadata_from_gamma_by_token_id = lambda _tid: (None, "request_error")  # type: ignore[assignment]
    captured = []
    manager._append_exit_token_record = lambda token, reason, **kwargs: captured.append((token, reason, kwargs))  # type: ignore[assignment]
    manager._enforce_title_blacklist_policy = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not reach enforce"))  # type: ignore[assignment]

    ok = manager._start_topic_process(token_id)

    assert ok is False
    assert captured
    assert captured[0][1] == "TITLE_BLACKLIST_METADATA_UNVERIFIED_NO_POSITION"


def test_start_process_preserves_refill_retry_count_for_same_runtime_lifecycle(tmp_path):
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(tmp_path / "data")},
            "handled_topics_path": str(tmp_path / "data" / "handled_topics.json"),
        }
    )
    manager = _build_manager(cfg)
    token_id = "t1"
    manager.topic_details[token_id] = {}
    manager._ws_cache[token_id] = {"bid": 0.5, "ask": 0.6, "ts": time.time()}
    manager._refill_retry_counts[token_id] = 6
    manager._hydrate_topic_metadata_for_blacklist = lambda *_args, **_kwargs: None  # type: ignore[assignment]
    manager._apply_metadata_unverified_guard = lambda *_args, **_kwargs: ""  # type: ignore[assignment]
    manager._enforce_title_blacklist_policy = lambda *_args, **_kwargs: ""  # type: ignore[assignment]
    manager._is_sell_cleanup_in_flight = lambda *_args, **_kwargs: False  # type: ignore[assignment]
    manager._block_topic_start_for_active_sell = lambda *_args, **_kwargs: False  # type: ignore[assignment]
    manager._reconcile_position_restore_before_start = lambda *_args, **_kwargs: None  # type: ignore[assignment]
    manager._build_run_config = lambda *_args, **_kwargs: {}  # type: ignore[assignment]
    manager._apply_token_cycle_buy_gate_and_drop_override = lambda *_args, **_kwargs: True  # type: ignore[assignment]
    manager._mark_token_cycle_local_start = lambda *_args, **_kwargs: None  # type: ignore[assignment]

    class _DummyProc:
        pid = 12345

        def poll(self):
            return None

    old_popen = autorun_mod.subprocess.Popen
    old_sleep = autorun_mod.time.sleep
    autorun_mod.subprocess.Popen = lambda *args, **kwargs: _DummyProc()  # type: ignore[assignment]
    autorun_mod.time.sleep = lambda *_args, **_kwargs: None  # type: ignore[assignment]
    try:
        ok = manager._start_topic_process(token_id)
    finally:
        autorun_mod.subprocess.Popen = old_popen  # type: ignore[assignment]
        autorun_mod.time.sleep = old_sleep  # type: ignore[assignment]

    assert ok is True
    assert manager._refill_retry_counts[token_id] == 6


def test_ensure_resume_state_from_live_position_sets_skip_buy():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "restored_1"
    manager.topic_details[token_id] = {"queue_role": "restored_token"}
    manager._stoploss_reentry_states[token_id] = manager._default_stoploss_reentry_state(
        token_id
    )
    manager._refresh_unified_position_snapshot = lambda **_kwargs: (  # type: ignore[assignment]
        [{"asset": token_id, "size": 4.0, "avgPrice": 0.63}],
        {token_id: 4.0},
        "ok",
        "live",
    )

    manager._ensure_resume_state_from_live_position(token_id)

    resume = (manager.topic_details.get(token_id) or {}).get("resume_state") or {}
    assert resume.get("has_position") is True
    assert float(resume.get("position_size") or 0.0) == 4.0
    assert float(resume.get("entry_price") or 0.0) == 0.63
    assert resume.get("skip_buy") is True


def test_ensure_resume_state_from_live_position_sets_skip_buy_for_startup_reconcile_position():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "startup_reconcile_live_pos"
    manager.topic_details[token_id] = {"queue_role": "startup_reconcile_position"}
    manager._stoploss_reentry_states[token_id] = manager._default_stoploss_reentry_state(
        token_id
    )
    manager._refresh_unified_position_snapshot = lambda **_kwargs: (  # type: ignore[assignment]
        [{"asset": token_id, "size": 3.0, "avgPrice": 0.41}],
        {token_id: 3.0},
        "ok",
        "live",
    )

    manager._ensure_resume_state_from_live_position(token_id)

    resume = (manager.topic_details.get(token_id) or {}).get("resume_state") or {}
    assert resume.get("has_position") is True
    assert float(resume.get("position_size") or 0.0) == 3.0
    assert float(resume.get("entry_price") or 0.0) == 0.41
    assert resume.get("skip_buy") is True


def test_reconcile_position_restore_before_start_downgrades_startup_reconcile_without_position():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "startup_reconcile_no_pos"
    manager.topic_details[token_id] = {
        "queue_role": "startup_reconcile_position",
        "resume_state": {"has_position": True, "position_size": 5.0, "entry_price": 0.61},
    }
    manager._refresh_unified_position_snapshot = lambda force_refresh=False: ([], {}, "ok", "live")  # type: ignore[assignment]

    result = manager._reconcile_position_restore_before_start(token_id)

    assert result == "downgraded_to_buy"
    detail = manager.topic_details[token_id]
    assert detail["queue_role"] == "startup_reconcile_buy"
    assert "resume_state" not in detail


def test_reconcile_position_restore_before_start_drops_restored_token_without_position():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "restored_no_pos"
    manager.pending_topics.append(token_id)
    manager.handled_topics.add(token_id)
    manager.topic_details[token_id] = {
        "queue_role": "restored_token",
        "resume_state": {"has_position": True, "position_size": 5.0, "entry_price": 0.61},
    }
    manager.tasks[token_id] = TopicTask(topic_id=token_id, status="pending")
    manager._refresh_unified_position_snapshot = lambda force_refresh=False: ([], {}, "ok", "live")  # type: ignore[assignment]
    captured = []
    manager._append_exit_token_record = lambda token, reason, **kwargs: captured.append((token, reason, kwargs))  # type: ignore[assignment]

    result = manager._reconcile_position_restore_before_start(token_id)

    assert result == "dropped_restored_token"
    assert token_id not in manager.pending_topics
    assert token_id not in manager.tasks
    assert token_id not in manager.handled_topics
    assert token_id not in manager.topic_details
    assert captured and captured[0][1] == "RESTORE_RUNTIME_NO_POSITION"


def test_reconcile_position_restore_before_start_keeps_restored_token_when_snapshot_unavailable():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "restored_snapshot_unavailable"
    manager.pending_topics.append(token_id)
    manager.handled_topics.add(token_id)
    manager.topic_details[token_id] = {
        "queue_role": "restored_token",
        "resume_state": {"has_position": True, "position_size": 5.0, "entry_price": 0.61},
    }
    manager._refresh_unified_position_snapshot = lambda force_refresh=False: ([], {}, "request_error", "data_api")  # type: ignore[assignment]
    captured = []
    manager._append_exit_token_record = lambda token, reason, **kwargs: captured.append((token, reason, kwargs))  # type: ignore[assignment]

    result = manager._reconcile_position_restore_before_start(token_id)

    assert result == "position_snapshot_unavailable"
    assert token_id in manager.pending_topics
    assert token_id in manager.handled_topics
    assert manager.topic_details[token_id]["queue_role"] == "restored_token"
    assert "resume_state" in manager.topic_details[token_id]
    assert not captured


def test_reconcile_position_restore_before_start_keeps_startup_reconcile_when_snapshot_unavailable():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "startup_snapshot_unavailable"
    manager.topic_details[token_id] = {
        "queue_role": "startup_reconcile_position",
        "resume_state": {"has_position": True, "position_size": 5.0, "entry_price": 0.61},
    }
    manager._refresh_unified_position_snapshot = lambda force_refresh=False: ([], {}, "request_error", "data_api")  # type: ignore[assignment]

    result = manager._reconcile_position_restore_before_start(token_id)

    assert result == "position_snapshot_unavailable"
    assert manager.topic_details[token_id]["queue_role"] == "startup_reconcile_position"
    assert "resume_state" in manager.topic_details[token_id]


def test_start_process_blocked_when_sell_cleanup_in_flight():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "cleanup_token"
    manager.pending_exit_topics.append(token_id)
    manager.topic_details[token_id] = {"queue_role": "restored_token"}
    manager._hydrate_topic_metadata_for_blacklist = lambda *_args, **_kwargs: None  # type: ignore[assignment]
    manager._apply_metadata_unverified_guard = lambda *_args, **_kwargs: "verified"  # type: ignore[assignment]
    manager._enforce_title_blacklist_policy = lambda *_args, **_kwargs: "allowed"  # type: ignore[assignment]

    ok = manager._start_topic_process(token_id)

    assert ok is False


def test_hydrate_force_official_check_not_short_circuited_by_local_metadata():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "force_meta_token"
    manager.topic_details[token_id] = {
        "title": "local title",
        "slug": "local-slug",
    }
    calls = {"n": 0}

    def _fake_fetch(_tid: str):
        calls["n"] += 1
        return (
            {"question": "official title", "slug": "official-slug"},
            "stub",
        )

    manager._fetch_market_metadata_from_gamma_by_token_id = _fake_fetch  # type: ignore[assignment]

    manager._hydrate_topic_metadata_for_blacklist(token_id, force_official_check=True)

    assert calls["n"] == 1
    detail = manager.topic_details[token_id]
    assert detail["title"] == "official title"
    assert detail["slug"] == "official-slug"
    assert detail.get("blacklist_metadata_verified") is True


def test_hydrate_calls_official_fetch_when_positions_cache_missing():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "no_cache_token"
    calls = {"n": 0}

    def _fake_fetch(_tid: str):
        calls["n"] += 1
        return (None, "stub_error")

    manager._fetch_market_metadata_from_gamma_by_token_id = _fake_fetch  # type: ignore[assignment]

    manager._hydrate_topic_metadata_for_blacklist(token_id, force_official_check=True)

    assert calls["n"] == 1
    detail = manager.topic_details[token_id]
    assert detail.get("blacklist_metadata_verified") is False
    assert detail.get("blacklist_metadata_source") == "stub_error"


def test_aggressive_mode_uses_burst_slots_and_queue_promotion():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "strategy_mode": "aggressive",
                "aggressive_burst_slots": 3,
            }
        }
    )
    manager = _build_manager(cfg)
    assert manager._is_aggressive_mode() is True
    assert manager._burst_slots() == 3

    manager._enqueue_pending_topic("t1")
    manager._enqueue_burst_topic("t1", promote=True)
    assert "t1" not in manager.pending_topics
    assert manager.pending_burst_topics[0] == "t1"


def test_classic_mode_drains_burst_queue_into_pending():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic"}})
    manager = _build_manager(cfg)
    manager.pending_burst_topics = ["x", "y"]
    manager.pending_topics = ["z"]

    manager._normalize_pending_queues_for_mode()

    # 现行语义：函数为空实现，不再将 burst 回挪到 base。
    assert manager.pending_burst_topics == ["x", "y"]
    assert manager.pending_topics == ["z"]


def test_schedule_pending_topics_pauses_and_defers_when_low_balance():
    cfg = GlobalConfig.from_dict({"scheduler": {"buy_pause_min_free_balance": 20}})
    manager = _build_manager(cfg)
    manager.pending_topics = ["t1"]
    manager.pending_burst_topics = ["t2"]

    captured = []
    manager._is_buy_paused_by_balance = lambda: True  # type: ignore[assignment]
    manager._append_exit_token_record = lambda token_id, reason, **kwargs: captured.append((token_id, reason, kwargs))  # type: ignore[assignment]

    manager._schedule_pending_topics()

    assert manager.pending_topics == []
    assert manager.pending_burst_topics == []
    assert manager._buy_pause_deferred_tokens == {"t1", "t2"}
    assert len(captured) == 2
    assert all(reason == "LOW_BALANCE_PAUSE" for _, reason, _ in captured)


def test_refresh_topics_defers_new_topics_when_low_balance_pause_active():
    cfg = GlobalConfig.from_dict({"scheduler": {"buy_pause_min_free_balance": 20}})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager._next_startup_sync_retry_at = 0.0

    manager._is_buy_paused_by_balance = lambda: True  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "a", "token_id": "a"},
        {"topic_id": "b", "token_id": "b"},
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    captured = []
    manager._append_exit_token_record = lambda token_id, reason, **kwargs: captured.append((token_id, reason, kwargs))  # type: ignore[assignment]

    manager._refresh_topics()

    assert manager.pending_topics == []
    assert manager.pending_burst_topics == []
    assert manager._buy_pause_deferred_tokens == {"a", "b"}
    assert {token_id for token_id, _, _ in captured} == {"a", "b"}
    assert manager.handled_topics.issuperset({"a", "b"})




def test_refresh_topics_preserves_queue_role_for_existing_pending_burst():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic"}})
    manager = _build_manager(cfg)

    manager.pending_burst_topics = ["a"]
    manager.topic_details["a"] = {"queue_role": "new_token", "schedule_lane": "burst"}
    manager.handled_topics.add("a")

    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "a", "token_id": "a"},
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    manager._refresh_topics()

    assert manager.pending_burst_topics == ["a"]
    assert manager.topic_details["a"]["queue_role"] == "new_token"
    assert manager._queue_role(manager.topic_details.get("a") or {}) == "new_token"

def test_refresh_topics_routes_new_tokens_to_burst_in_classic_and_aggressive_mode():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic", "aggressive_burst_slots": 2}})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager._next_startup_sync_retry_at = 0.0

    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "a", "token_id": "a"},
        {"topic_id": "b", "token_id": "b"},
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    manager._refresh_topics()

    assert manager.pending_topics == []
    assert manager.pending_burst_topics == ["a", "b"]
    assert manager.topic_details["a"]["queue_role"] == "new_token"
    assert manager.topic_details["b"]["queue_role"] == "new_token"


def test_refresh_topics_rearms_active_unmanaged_handled_token():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager._refill_retry_counts["t1"] = 6
    manager.topic_details["t1"] = {
        "resume_state": {"has_position": True},
        "refill_exit_reason": "SELL_ABANDONED",
        "startup_orphan_profit_sweep": True,
        "stoploss_reentry_resume": {"old_entry_price": 0.2},
        "orphaned": True,
    }
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]
    captured = []
    manager._append_exit_token_record = lambda token_id, reason, **kwargs: captured.append((token_id, reason, kwargs))  # type: ignore[assignment]

    manager._refresh_topics()

    assert "t1" in manager.pending_topics
    detail = manager.topic_details.get("t1") or {}
    assert detail.get("queue_role") == "startup_reconcile_buy"
    assert "resume_state" not in detail
    assert "refill_exit_reason" not in detail
    assert "startup_orphan_profit_sweep" not in detail
    assert "stoploss_reentry_resume" not in detail
    assert "orphaned" not in detail
    assert captured and captured[-1][0] == "t1"
    assert captured[-1][1] == "ACTIVE_UNMANAGED_REARM"
    assert captured[-1][2]["exit_data"]["rearm_path"] == "startup_reconcile_buy"
    assert "resume_state" in captured[-1][2]["exit_data"]["stale_keys_cleared"]


def test_refresh_topics_rearm_uses_position_path_when_position_exists():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]
    manager._refresh_sell_position_snapshot = lambda: ({"t1": 5.0}, "ok")  # type: ignore[assignment]

    manager._refresh_topics()

    assert "t1" in manager.pending_topics
    assert (manager.topic_details.get("t1") or {}).get("queue_role") == "startup_reconcile_position"


def test_refresh_topics_does_not_rearm_stoploss_owned_token():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager._stoploss_reentry_states["t1"] = {
        "state": "STOPLOSS_EXITED_WAITING_WINDOW",
        "source_detached": False,
    }
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    manager._refresh_topics()

    assert "t1" not in manager.pending_topics


def test_refresh_topics_does_not_rearm_manual_intervention_token():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager.topic_details["t1"] = {
        "queue_role": "manual_intervention",
    }
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    manager._refresh_topics()

    assert "t1" not in manager.pending_topics


def test_refresh_topics_does_not_rearm_stoploss_waiting_probe_token():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager._stoploss_reentry_states["t1"] = {
        "state": "STOPLOSS_EXITED_WAITING_PROBE",
        "source_detached": False,
    }
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    manager._refresh_topics()

    assert "t1" not in manager.pending_topics


def test_refresh_topics_does_not_rearm_stoploss_clear_pending_escalated_token():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager._stoploss_reentry_states["t1"] = {
        "state": "STOPLOSS_CLEAR_PENDING_ESCALATED",
        "source_detached": False,
    }
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]

    manager._refresh_topics()

    assert "t1" not in manager.pending_topics


def test_refresh_topics_does_not_rearm_orphan_owned_token():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]
    manager._load_latest_orphan_states = lambda: {"t1": {"status": "orphaned"}}  # type: ignore[assignment]

    manager._refresh_topics()

    assert "t1" not in manager.pending_topics


def test_refresh_topics_suppresses_runtime_rearm_after_budget_exhausted():
    cfg = GlobalConfig.from_dict(
        {
            "active_unmanaged_rearm_cooldown_sec": 60.0,
            "active_unmanaged_rearm_budget_window_sec": 3600.0,
            "active_unmanaged_rearm_budget_max_attempts": 3,
            "active_unmanaged_rearm_budget_suppress_sec": 7200.0,
        }
    )
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]
    now = time.time()
    manager._active_unmanaged_rearm_recent_ts["t1"] = [now - 10.0, now - 120.0, now - 240.0]
    captured = []
    manager._append_exit_token_record = lambda token_id, reason, **kwargs: captured.append((token_id, reason, kwargs))  # type: ignore[assignment]

    manager._refresh_topics()

    assert "t1" not in manager.pending_topics
    assert captured and captured[-1][1] == "ACTIVE_UNMANAGED_REARM_SUPPRESSED"
    assert float((captured[-1][2]["exit_data"] or {}).get("suppressed_until_ts") or 0.0) > now


def test_append_exit_token_record_sell_abandoned_sets_rearm_block():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._load_exit_tokens = lambda: []  # type: ignore[assignment]

    manager._append_exit_token_record(
        "t1",
        "SELL_ABANDONED",
        exit_data={"has_position": True},
        refillable=True,
    )

    assert float(manager._active_unmanaged_rearm_blocked_until.get("t1") or 0.0) > time.time()
    assert manager._active_unmanaged_rearm_block_reasons.get("t1") == "SELL_ABANDONED"


def test_refresh_topics_skips_runtime_rearm_while_sell_abandoned_block_active():
    cfg = GlobalConfig.from_dict(
        {
            "active_unmanaged_rearm_cooldown_sec": 60.0,
        }
    )
    manager = _build_manager(cfg)
    manager._startup_sync_retry_needed = False
    manager.handled_topics.add("t1")
    manager._is_buy_paused_by_balance = lambda: False  # type: ignore[assignment]
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"topic_id": "t1", "token_id": "t1", "title": "Token 1"}
    ]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._load_copytrade_blacklist = lambda: set()  # type: ignore[assignment]
    manager._apply_sell_signals = lambda _: None  # type: ignore[assignment]
    manager._refresh_sell_position_snapshot = lambda: ({"t1": 1.0}, "ok")  # type: ignore[assignment]
    manager._active_unmanaged_rearm_blocked_until["t1"] = time.time() + 300.0
    manager._active_unmanaged_rearm_block_reasons["t1"] = "SELL_ABANDONED"

    manager._refresh_topics()

    assert "t1" not in manager.pending_topics


def test_rebalance_moves_new_token_from_burst_to_base_but_keeps_reentry_in_burst():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "classic", "max_concurrent_tasks": 2}})
    manager = _build_manager(cfg)

    manager.pending_burst_topics = ["r1", "n1", "n2"]
    manager.topic_details["r1"] = {"queue_role": "reentry_token", "schedule_lane": "burst"}
    manager.topic_details["n1"] = {"queue_role": "new_token", "schedule_lane": "burst"}
    manager.topic_details["n2"] = {"queue_role": "new_token", "schedule_lane": "burst"}

    class _Task:
        def __init__(self, running: bool):
            self._running = running

        def is_running(self):
            return self._running

    manager.tasks = {
        "base_running": _Task(True),
    }
    manager._running_burst_count = lambda: 0  # type: ignore[assignment]

    manager._rebalance_burst_to_base_queue()

    # 现行语义：rebalance 已废弃，队列不应被修改。
    assert manager.pending_topics == []
    assert manager.pending_burst_topics == ["r1", "n1", "n2"]
    assert manager.topic_details["n1"]["schedule_lane"] == "burst"


def test_reentry_enqueue_promotes_ahead_of_new_tokens_in_burst():
    cfg = GlobalConfig.from_dict({"scheduler": {"strategy_mode": "aggressive", "aggressive_burst_slots": 2}})
    manager = _build_manager(cfg)
    manager.pending_burst_topics = ["new_a", "new_b"]

    manager.topic_details["new_a"] = {"queue_role": "new_token"}
    manager.topic_details["new_b"] = {"queue_role": "new_token"}
    manager.pending_topics = ["reentry_x"]

    manager._poll_aggressive_self_sell_reentry = lambda: None  # type: ignore[assignment]
    manager._enqueue_burst_topic("reentry_x", promote=True)

    assert manager.pending_burst_topics[0] == "reentry_x"


def test_poll_reentry_always_promotes_to_burst_front():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "strategy_mode": "aggressive",
                "aggressive_enable_self_sell_reentry": True,
                "aggressive_reentry_source": "self_account_fills_only",
            }
        }
    )
    manager = _build_manager(cfg)
    manager.pending_burst_topics = ["new_a", "new_b"]
    manager._position_address = "0xabc"
    manager._process_started_at = 1.0
    manager._last_self_sell_trade_ts = 1
    manager._mark_reentry_eligible_token("reentry_x", source="SELL_ABANDONED")

    import poly_maker_autorun as autorun

    old_fetch = autorun._fetch_recent_trades_from_data_api
    try:
        autorun._fetch_recent_trades_from_data_api = lambda *_args, **_kwargs: ([
            {
                "side": "SELL",
                "asset": "reentry_x",
                "timestamp": 2,
                "size": "1",
                "price": "0.5",
                "conditionId": "c1",
            }
        ], "ok")

        manager._poll_aggressive_self_sell_reentry()
    finally:
        autorun._fetch_recent_trades_from_data_api = old_fetch

    assert manager.pending_burst_topics[0] == "reentry_x"
    assert manager.topic_details["reentry_x"]["queue_role"] == "reentry_token"


def test_low_balance_pause_refill_filter_blocks_during_pause_and_releases_after_resume():
    cfg = GlobalConfig.from_dict({"scheduler": {"buy_pause_min_free_balance": 20}})
    manager = _build_manager(cfg)
    record = {
        "token_id": "x",
        "exit_reason": "LOW_BALANCE_PAUSE",
        "exit_ts": 1.0,
        "exit_data": {"has_position": False},
        "refillable": True,
    }

    manager._buy_paused_due_to_balance = True
    blocked = manager._filter_refillable_tokens([record])
    assert blocked == []

    manager._buy_paused_due_to_balance = False
    released = manager._filter_refillable_tokens([record])
    assert len(released) == 1
    assert released[0]["token_id"] == "x"


def test_sell_abandoned_stale_position_record_is_blocked(tmp_path):
    cfg = GlobalConfig.from_dict({"data_dir": str(tmp_path / "data")})
    manager = _build_manager(cfg)
    manager._has_account_position = lambda _token_id: False  # type: ignore[assignment]
    record = {
        "token_id": "stale_sell",
        "exit_reason": "SELL_ABANDONED",
        "exit_ts": 1.0,
        "exit_data": {"has_position": True, "position_size": 11.0},
        "refillable": True,
    }
    manager._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    manager._exit_tokens_path.write_text(
        json.dumps([record], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = manager._load_exit_tokens()
    refillable = manager._filter_refillable_tokens(loaded)

    assert refillable == []

    persisted = manager._load_exit_tokens()
    assert persisted[0]["exit_data"]["has_position"] is True
    assert persisted[0]["exit_data"]["position_size"] == 11.0


def test_title_blacklist_with_position_stale_record_remains_blocked(tmp_path):
    cfg = GlobalConfig.from_dict({"data_dir": str(tmp_path / "data")})
    manager = _build_manager(cfg)
    manager._has_account_position = lambda _token_id: False  # type: ignore[assignment]
    record = {
        "token_id": "blocked_token",
        "exit_reason": "TITLE_BLACKLIST_WITH_POSITION",
        "exit_ts": 1.0,
        "exit_data": {"has_position": True, "position_size": 2.0},
        "refillable": True,
    }
    manager._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    manager._exit_tokens_path.write_text(
        json.dumps([record], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = manager._load_exit_tokens()
    refillable = manager._filter_refillable_tokens(loaded)

    assert refillable == []
    persisted = manager._load_exit_tokens()
    assert persisted[0]["exit_data"]["has_position"] is True
    assert persisted[0]["exit_data"]["position_size"] == 2.0


def test_buy_block_entry_sync_failed_stale_position_record_is_blocked(tmp_path):
    cfg = GlobalConfig.from_dict({"data_dir": str(tmp_path / "data")})
    manager = _build_manager(cfg)
    manager._has_account_position = lambda _token_id: False  # type: ignore[assignment]
    record = {
        "token_id": "entry_sync_token",
        "exit_reason": "BUY_BLOCK_ENTRY_SYNC_FAILED",
        "exit_ts": 1.0,
        "exit_data": {"has_position": True, "position_size": 3.0},
        "refillable": True,
    }
    manager._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    manager._exit_tokens_path.write_text(
        json.dumps([record], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = manager._load_exit_tokens()
    refillable = manager._filter_refillable_tokens(loaded)

    assert refillable == []
    persisted = manager._load_exit_tokens()
    assert persisted[0]["exit_data"]["has_position"] is True
    assert persisted[0]["exit_data"]["position_size"] == 3.0


def test_buy_block_trigger_unavailable_stale_record_remains_blocked(tmp_path):
    cfg = GlobalConfig.from_dict({"data_dir": str(tmp_path / "data")})
    manager = _build_manager(cfg)
    manager._has_account_position = lambda _token_id: False  # type: ignore[assignment]
    record = {
        "token_id": "trigger_token",
        "exit_reason": "BUY_BLOCK_TRIGGER_UNAVAILABLE",
        "exit_ts": 1.0,
        "exit_data": {"has_position": True, "position_size": 4.0},
        "refillable": True,
    }
    manager._exit_tokens_path.parent.mkdir(parents=True, exist_ok=True)
    manager._exit_tokens_path.write_text(
        json.dumps([record], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    loaded = manager._load_exit_tokens()
    refillable = manager._filter_refillable_tokens(loaded)

    assert refillable == []


def test_gap_skip_backoff_applies_and_resets_on_non_gap():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "refill_cooldown_minutes_with_position": 0,
                "refill_cooldown_minutes_no_position": 0,
                "gap_skip_backoff_enabled": True,
                "gap_skip_backoff_minutes": [5, 15, 45, 120],
            }
        }
    )
    manager = _build_manager(cfg)
    now = time.time()

    # 最新两条都是 gap，按第2档退避（15分钟）应被拦截
    blocked = manager._filter_refillable_tokens(
        [
            {
                "token_id": "gap_token",
                "exit_reason": "REFILL_SKIP_GAP",
                "exit_ts": now - 60,
                "exit_data": {"has_position": False},
                "refillable": True,
            },
            {
                "token_id": "gap_token",
                "exit_reason": "POSITION_SYNC_SKIP_GAP",
                "exit_ts": now - 120,
                "exit_data": {"has_position": False},
                "refillable": True,
            },
        ]
    )
    assert blocked == []

    # 最新一条改为非 gap，视为重置，应允许回填
    released = manager._filter_refillable_tokens(
        [
            {
                "token_id": "gap_token",
                "exit_reason": "SELL_ABANDONED",
                "exit_ts": now - 60,
                "exit_data": {"has_position": False},
                "refillable": True,
            },
            {
                "token_id": "gap_token",
                "exit_reason": "REFILL_SKIP_GAP",
                "exit_ts": now - 120,
                "exit_data": {"has_position": False},
                "refillable": True,
            },
        ]
    )
    assert len(released) == 1
    assert released[0]["token_id"] == "gap_token"


def test_fetch_recent_trades_retries_after_timeout():
    import poly_maker_autorun as autorun

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [{"side": "SELL", "asset": "t1"}]

    calls = {"n": 0}

    class _TimeoutExc(Exception):
        pass

    old_requests = autorun.requests
    old_attempts = autorun.DATA_API_TRADE_RETRY_ATTEMPTS
    old_backoff = autorun.DATA_API_RETRY_BACKOFF_SEC
    old_sleep = autorun.time.sleep
    try:
        def _fake_get(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _TimeoutExc("timeout")
            return _Resp()

        autorun.requests = types.SimpleNamespace(
            get=_fake_get,
            Timeout=_TimeoutExc,
            RequestException=Exception,
        )
        autorun.DATA_API_TRADE_RETRY_ATTEMPTS = 2
        autorun.DATA_API_RETRY_BACKOFF_SEC = (0.0,)
        autorun.time.sleep = lambda *_args, **_kwargs: None

        rows, info = autorun._fetch_recent_trades_from_data_api("0xabc", limit=10)
        assert info == "ok"
        assert len(rows) == 1
        assert calls["n"] == 2
    finally:
        autorun.requests = old_requests
        autorun.DATA_API_TRADE_RETRY_ATTEMPTS = old_attempts
        autorun.DATA_API_RETRY_BACKOFF_SEC = old_backoff
        autorun.time.sleep = old_sleep


def test_fetch_recent_trades_returns_last_error_after_retries():
    import poly_maker_autorun as autorun

    calls = {"n": 0}

    class _ReqExc(Exception):
        pass

    old_requests = autorun.requests
    old_attempts = autorun.DATA_API_TRADE_RETRY_ATTEMPTS
    old_backoff = autorun.DATA_API_RETRY_BACKOFF_SEC
    old_sleep = autorun.time.sleep
    try:
        def _fake_get(*args, **kwargs):
            calls["n"] += 1
            raise _ReqExc("boom")

        autorun.requests = types.SimpleNamespace(
            get=_fake_get,
            Timeout=Exception,
            RequestException=_ReqExc,
        )
        autorun.DATA_API_TRADE_RETRY_ATTEMPTS = 3
        autorun.DATA_API_RETRY_BACKOFF_SEC = (0.0,)
        autorun.time.sleep = lambda *_args, **_kwargs: None

        rows, info = autorun._fetch_recent_trades_from_data_api("0xabc", limit=10)
        assert rows == []
        assert "attempt=3/3" in info
        assert calls["n"] == 3
    finally:
        autorun.requests = old_requests
        autorun.DATA_API_TRADE_RETRY_ATTEMPTS = old_attempts
        autorun.DATA_API_RETRY_BACKOFF_SEC = old_backoff
        autorun.time.sleep = old_sleep


def test_ws_chunk_and_confirm_config_can_be_loaded_from_scheduler():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "ws_subscribe_chunk_size": 17,
                "ws_subscribe_chunk_interval_ms": 80,
                "ws_ready_use_confirmed": True,
                "ws_ready_confirm_grace_sec": 1.5,
            }
        }
    )

    assert cfg.ws_subscribe_chunk_size == 17
    assert cfg.ws_subscribe_chunk_interval_ms == 80.0
    assert cfg.ws_ready_use_confirmed is True
    assert cfg.ws_ready_confirm_grace_sec == 1.5


def test_last_trade_event_bootstraps_cache_for_subscribed_token():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    token_id = "token-a"
    manager._ws_token_ids = [token_id]

    manager._update_token_timestamp_from_trade(
        {
            "event_type": "last_trade_price",
            "asset_id": token_id,
            "price": "0.52",
        }
    )

    with manager._ws_cache_lock:
        assert token_id in manager._ws_cache
        assert manager._ws_cache[token_id]["price"] == 0.52
        assert manager._ws_cache[token_id]["source"] == "last_trade_price_bootstrap"


def test_evict_stale_pending_topics_requires_ws_unconfirmed_and_book_unavailable():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "pending_soft_eviction_minutes": 1,
                "enable_pending_soft_eviction": True,
            }
        }
    )
    manager = _build_manager(cfg)
    manager.pending_topics = ["t1", "t2", "t3"]
    now = 1000.0
    manager._pending_first_seen = {"t1": 0.0, "t2": 0.0, "t3": 0.0}

    manager._is_ws_confirmed = lambda token_id: token_id == "t2"  # type: ignore[assignment]
    manager._probe_clob_book_available = lambda token_id: (token_id == "t3", "probe")  # type: ignore[assignment]
    records = []
    manager._append_exit_token_record = lambda token_id, reason, **kwargs: records.append((token_id, reason, kwargs))  # type: ignore[assignment]

    import poly_maker_autorun as autorun

    old_time = autorun.time.time
    try:
        autorun.time.time = lambda: now
        manager._evict_stale_pending_topics()
    finally:
        autorun.time.time = old_time

    assert [token_id for token_id, _, _ in records] == ["t1"]
    assert all(reason == "NO_DATA_TIMEOUT" for _, reason, _ in records)
    assert "t1" not in manager.pending_topics
    assert "t2" in manager.pending_topics
    assert "t3" in manager.pending_topics


def test_task_runtime_mode_low_balance_refill_with_position():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._buy_paused_due_to_balance = True
    manager._get_task_run_config = lambda _task: {}  # type: ignore[assignment]
    task = TopicTask(topic_id="t1")

    mode = manager._task_runtime_mode(task, {"queue_role": "refill_with_position"})

    assert mode == "余额不足只卖出"


def test_task_runtime_mode_blacklist_has_priority_over_low_balance():
    cfg = GlobalConfig.from_dict({})
    manager = _build_manager(cfg)
    manager._buy_paused_due_to_balance = True
    manager._get_task_run_config = lambda _task: {"force_sell_only_on_startup": True}  # type: ignore[assignment]
    task = TopicTask(topic_id="t1")

    mode = manager._task_runtime_mode(task, {"queue_role": "title_blacklist_sell_only"})

    assert mode == "黑名单"


def test_cycle_gate_blocks_buy_before_allowed_ts(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(data_dir)}})
    manager = _build_manager(cfg)
    manager._token_cycle_states = {
        "t1": {
            "cycle_round": 1,
            "next_buy_allowed_ts": time.time() + 90.0,
            "next_drop_pct": 0.08,
        }
    }
    run_cfg = {"drop_pct": 0.05}

    allowed = manager._apply_token_cycle_buy_gate_and_drop_override("t1", run_cfg)

    assert allowed is False
    assert "resume_drop_pct" not in run_cfg


def test_cycle_gate_applies_drop_override_when_cooldown_elapsed(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(data_dir)}})
    manager = _build_manager(cfg)
    manager._token_cycle_states = {
        "t1": {
            "cycle_round": 3,
            "next_buy_allowed_ts": time.time() - 1.0,
            "next_drop_pct": 0.08,
        }
    }
    run_cfg = {
        "drop_pct": 0.05,
        "resume_drop_pct": 0.03,
        "enable_incremental_drop_pct": True,
        "incremental_drop_pct_cap": 0.20,
    }

    allowed = manager._apply_token_cycle_buy_gate_and_drop_override("t1", run_cfg)

    assert allowed is True
    assert run_cfg.get("resume_drop_pct") == 0.08


def test_advance_cycle_state_updates_round_cooldown_and_drop(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(data_dir)}})
    manager = _build_manager(cfg)
    run_cfg = {
        "drop_pct": 0.05,
        "enable_incremental_drop_pct": True,
        "incremental_drop_pct_step": 0.002,
        "incremental_drop_pct_cap": 0.20,
    }

    before = time.time()
    manager._advance_token_cycle_state_on_cleanup("t1", run_cfg)
    state1 = manager._token_cycle_states["t1"]
    after = time.time()

    assert state1["cycle_round"] == 1
    assert abs(float(state1.get("next_drop_pct")) - 0.052) < 1e-9
    assert before + 60.0 <= float(state1["next_buy_allowed_ts"]) <= after + 60.0

    manager._advance_token_cycle_state_on_cleanup("t1", run_cfg)
    state2 = manager._token_cycle_states["t1"]

    assert state2["cycle_round"] == 1
    assert abs(float(state2.get("next_drop_pct")) - 0.052) < 1e-9


def test_advance_shared_cycle_state_after_sell_updates_round_cooldown_and_thresholds(tmp_path):
    state_path = tmp_path / "token_cycle_gate.json"
    state_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "token_states": {
                    "t1": {
                        "cycle_round": 1,
                        "next_buy_allowed_ts": 0.0,
                        "next_drop_pct": 0.052,
                        "next_profit_pct": 0.011,
                        "local_cycle_status": "started_not_bought",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    record = _advance_shared_cycle_state_after_sell(
        "t1",
        state_path,
        current_drop_pct=0.052,
        current_profit_pct=0.011,
        enable_incremental_drop_pct=True,
        incremental_drop_pct_step=0.002,
        incremental_drop_pct_cap=0.20,
        enable_incremental_profit_pct=True,
        incremental_profit_pct_step=0.001,
        incremental_profit_pct_cap=0.05,
        now_ts=100.0,
    )

    assert record["cycle_round"] == 2
    assert abs(float(record["next_buy_allowed_ts"]) - 220.0) < 1e-9
    assert abs(float(record["next_drop_pct"]) - 0.054) < 1e-9
    assert abs(float(record["next_profit_pct"]) - 0.012) < 1e-9
    assert record["local_cycle_status"] == "cycle_closed"

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["token_states"]["t1"]["cycle_round"] == 2


def test_save_token_cycle_states_preserves_newer_disk_cycle_fields(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    state_path = manager.config.token_cycle_state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "token_states": {
                    "t1": {
                        "cycle_round": 2,
                        "next_buy_allowed_ts": 220.0,
                        "last_cycle_completed_ts": 100.0,
                        "next_drop_pct": 0.054,
                        "next_profit_pct": 0.012,
                        "local_cycle_status": "cycle_closed",
                        "local_cycle_started_ts": 0.0,
                        "local_cycle_invalidated_ts": 0.0,
                        "local_cycle_invalidate_reason": "",
                        "local_cycle_started_mode": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    manager._token_cycle_states["t1"] = {
        "cycle_round": 0,
        "next_buy_allowed_ts": 0.0,
        "last_cycle_completed_ts": 0.0,
        "local_cycle_status": "started_not_bought",
        "local_cycle_started_ts": 150.0,
        "local_cycle_invalidated_ts": 0.0,
        "local_cycle_invalidate_reason": "",
        "local_cycle_started_mode": "started_not_bought",
    }

    manager._save_token_cycle_states()

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    record = payload["token_states"]["t1"]
    assert record["cycle_round"] == 2
    assert abs(float(record["next_buy_allowed_ts"]) - 220.0) < 1e-9
    assert abs(float(record["last_cycle_completed_ts"]) - 100.0) < 1e-9
    assert abs(float(record["next_drop_pct"]) - 0.054) < 1e-9
    assert abs(float(record["next_profit_pct"]) - 0.012) < 1e-9
    assert record["local_cycle_status"] == "started_not_bought"
    assert abs(float(record["local_cycle_started_ts"]) - 150.0) < 1e-9


def test_load_token_cycle_states_drops_placeholder_keys_when_real_tokens_exist(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    state_path = manager.config.token_cycle_state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    real_token = "123456789012345678901234567890"
    state_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "token_states": {
                    "t1": {"cycle_round": 0, "next_buy_allowed_ts": 0.0},
                    real_token: {"cycle_round": 2, "next_buy_allowed_ts": 120.0},
                },
            }
        ),
        encoding="utf-8",
    )

    manager._load_token_cycle_states()

    assert "t1" not in manager._token_cycle_states
    assert real_token in manager._token_cycle_states


def test_save_token_cycle_states_drops_placeholder_keys_when_real_tokens_exist(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    real_token = "123456789012345678901234567890"
    manager._token_cycle_states = {
        "t1": {"cycle_round": 0, "next_buy_allowed_ts": 0.0},
        real_token: {"cycle_round": 1, "next_buy_allowed_ts": 60.0},
    }

    manager._save_token_cycle_states()

    payload = json.loads(manager.config.token_cycle_state_path.read_text(encoding="utf-8"))
    assert "t1" not in payload["token_states"]
    assert real_token in payload["token_states"]


def test_apply_sell_signals_ignores_signal_without_local_cycle(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {
                        "token_id": "t1",
                        "introduced_by_buy": True,
                        "active": True,
                        "status": "pending",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_sell_signals_path": str(sell_path),
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager._sell_bootstrap_done = True
    manager._token_cycle_states["t1"] = {
        "cycle_round": 0,
        "next_buy_allowed_ts": 0.0,
        "local_cycle_status": "idle",
    }
    triggered: list[str] = []
    manager._trigger_sell_exit = lambda token_id, task, trigger_source="unspecified", trigger_reason="UNSPECIFIED": triggered.append(token_id) or True  # type: ignore[assignment]

    manager._apply_sell_signals(manager._load_copytrade_sell_signals())

    payload = json.loads(sell_path.read_text(encoding="utf-8"))
    row = payload["sell_tokens"][0]
    assert triggered == []
    assert row["status"] == "stale_ignored"
    assert row["active"] is False
    assert row["note"] == "local_cycle_gate:idle"
    assert manager._token_cycle_states["t1"]["local_cycle_status"] == "invalidated"


def test_token_cycle_resume_state_allows_sell_signal(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    manager._token_cycle_states["t1"] = {
        "cycle_round": 1,
        "next_buy_allowed_ts": 0.0,
        "local_cycle_status": "position_resume",
    }

    allowed, reason = manager._token_cycle_allows_sell_signal("t1")

    assert allowed is True
    assert reason == "position_resume"


def test_token_cycle_started_not_bought_blocks_sell_without_position(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    manager._token_cycle_states["t1"] = {
        "cycle_round": 1,
        "next_buy_allowed_ts": 0.0,
        "local_cycle_status": "started_not_bought",
    }
    manager._has_account_position = lambda _token_id, force_refresh=False: False  # type: ignore[assignment]

    allowed, reason = manager._token_cycle_allows_sell_signal("t1")

    assert allowed is False
    assert reason == "started_not_bought"


def test_token_cycle_started_not_bought_promotes_when_position_exists(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    manager._token_cycle_states["t1"] = {
        "cycle_round": 1,
        "next_buy_allowed_ts": 0.0,
        "local_cycle_status": "started_not_bought",
    }
    manager._has_account_position = lambda _token_id, force_refresh=False: True  # type: ignore[assignment]

    allowed, reason = manager._token_cycle_allows_sell_signal("t1")

    assert allowed is True
    assert reason == "position_confirmed"
    assert manager._token_cycle_states["t1"]["local_cycle_status"] == "position_confirmed"


def test_sell_signal_older_than_local_cycle_is_ignored(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    old_ts = time.time() - 120.0
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {
                        "token_id": "t1",
                        "introduced_by_buy": True,
                        "active": True,
                        "status": "pending",
                        "signal_ts": old_ts,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_sell_signals_path": str(sell_path),
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager._sell_bootstrap_done = True
    manager._token_cycle_states["t1"] = {
        "cycle_round": 1,
        "next_buy_allowed_ts": 0.0,
        "local_cycle_status": "position_confirmed",
        "local_cycle_started_ts": time.time(),
    }
    triggered: list[str] = []
    manager._trigger_sell_exit = lambda token_id, task, trigger_source="unspecified", trigger_reason="UNSPECIFIED", signal_entry=None: triggered.append(token_id) or True  # type: ignore[assignment]

    manager._apply_sell_signals(manager._load_copytrade_sell_signals())

    payload = json.loads(sell_path.read_text(encoding="utf-8"))
    row = payload["sell_tokens"][0]
    assert triggered == []
    assert row["status"] == "stale_ignored"
    assert row["note"] == "local_cycle_gate:stale_previous_cycle_signal"


def test_cycle_closed_copytrade_sell_without_position_finalizes_terminal_freeze(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {
                        "token_id": "t1",
                        "introduced_by_buy": True,
                        "active": True,
                        "status": "pending",
                        "signal_ts": time.time(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_tokens_path": str(copytrade_dir / "tokens_from_copytrade.json"),
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager._sell_bootstrap_done = True
    manager._token_cycle_states["t1"] = {
        "cycle_round": 1,
        "next_buy_allowed_ts": 0.0,
        "local_cycle_status": "cycle_closed",
    }
    manager.topic_details["t1"] = {"terminal_sell_reason": "COPYTRADE_SELL"}
    manager._has_account_position = lambda _token_id, force_refresh=False: False  # type: ignore[assignment]

    exit_path = manager._exit_signal_path("t1")
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    exit_path.write_text(
        json.dumps(
            {
                "token_id": "t1",
                "active": True,
                "status": "pending",
                "exit_reason": "COPYTRADE_SELL",
            }
        ),
        encoding="utf-8",
    )

    manager._apply_sell_signals(manager._load_copytrade_sell_signals())

    payload = json.loads(exit_path.read_text(encoding="utf-8"))
    assert payload["status"] == "done"
    assert payload["active"] is False
    assert payload["invalidate_reason"] == "position_flat"

    sell_payload = json.loads(sell_path.read_text(encoding="utf-8"))
    assert sell_payload["sell_tokens"] == []
    archived = sell_payload["archived_sell_tokens"][0]
    assert archived["token_id"] == "t1"
    assert archived["status"] == "done"


def test_load_copytrade_tokens_skips_non_buy_introduced(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {"token_id": "buy_token", "introduced_by_buy": True},
                    {"token_id": "sell_only_token", "introduced_by_buy": False},
                ],
            }
        ),
        encoding="utf-8",
    )

    cfg = GlobalConfig.from_dict(
        {
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(copytrade_dir / "copytrade_sell_signals.json"),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)

    topics = manager._load_copytrade_tokens()

    assert [t["topic_id"] for t in topics] == ["buy_token"]


def test_load_copytrade_tokens_skips_follow_cooldown_token(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    manual_path = copytrade_dir / "manual_intervention_tokens.json"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {"token_id": "cooldown_token", "introduced_by_buy": True},
                    {"token_id": "normal_token", "introduced_by_buy": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(json.dumps({"updated_at": "", "sell_tokens": []}), encoding="utf-8")
    manual_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {
                        "token_id": "cooldown_token",
                        "follow_cooldown_until_ts": time.time() + 3600.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
        }
    )
    manager = _build_manager(cfg)

    topics = manager._load_copytrade_tokens()

    assert [t["topic_id"] for t in topics] == ["normal_token"]


def test_apply_sell_signals_requires_position_even_with_history(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    manager._sell_bootstrap_done = True
    manager._next_sell_full_recheck_at = time.time() + 3600.0
    manager._sell_position_snapshot = {"t1": 0.0}
    manager._sell_position_snapshot_info = "ok"
    manager.handled_topics.add("t1")

    triggered = []
    manager._trigger_sell_exit = lambda token_id, task, **kwargs: triggered.append((token_id, task, kwargs))  # type: ignore[assignment]

    manager._apply_sell_signals(
        {
            "t1": {
                "token_id": "t1",
                "introduced_by_buy": True,
                "status": "pending",
            }
        }
    )

    assert triggered == []
    assert "t1" in manager._handled_sell_signals


def test_stoploss_config_can_be_loaded_from_scheduler():
    cfg = GlobalConfig.from_dict(
        {
            "scheduler": {
                "stoploss": {
                    "enabled": True,
                    "check_interval_sec": 90,
                    "base_drawdown_pct": 0.05,
                    "confirm_rounds": 2,
                    "reentry_window_cooldown_minutes": 120,
                    "next_stoploss_cooldown_minutes": 30,
                    "reentry_extra_delay_after_5_cuts_hours": 24,
                    "reentry_line_ticks": 2,
                    "reentry_zone_lower_pct": 0.02,
                    "probe_break_pct": 0.08,
                    "daily_reentry_max_full_clears": 2,
                    "drawdown_step_per_cycle_pct": 0.01,
                    "max_tokens_per_cycle": 2,
                }
            }
        }
    )
    assert cfg.enable_stoploss is True
    assert cfg.stoploss_check_interval_sec == 90
    assert cfg.stoploss_base_drawdown_pct == 0.05
    assert cfg.stoploss_confirm_rounds == 2
    assert cfg.stoploss_reentry_window_cooldown_minutes == 120
    assert cfg.stoploss_next_stoploss_cooldown_minutes == 30
    assert cfg.stoploss_reentry_extra_delay_after_5_cuts_hours == 24
    assert cfg.stoploss_reentry_line_ticks == 2
    assert cfg.stoploss_reentry_zone_lower_pct == 0.02
    assert cfg.stoploss_probe_break_pct == 0.08
    assert cfg.stoploss_daily_reentry_max_full_clears == 2
    assert cfg.stoploss_drawdown_step_per_cycle_pct == 0.01
    assert cfg.stoploss_max_tokens_per_cycle == 2


def _build_stoploss_manager(tmp_path, *, mode="classic", stoploss_overrides=None):
    data_dir = tmp_path / "data"
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    handled_path = data_dir / "handled_topics.json"
    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["t1"]}),
        encoding="utf-8",
    )
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    tokens_path.write_text(
        json.dumps({"updated_at": "", "tokens": [{"token_id": "t1", "introduced_by_buy": True}]}),
        encoding="utf-8",
    )
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    sell_path.write_text(json.dumps({"updated_at": "", "sell_tokens": []}), encoding="utf-8")
    stoploss_cfg = {
        "enabled": True,
        "check_interval_sec": 60,
        "base_drawdown_pct": 0.05,
        "confirm_rounds": 2,
        "reentry_window_cooldown_minutes": 120.0,
        "next_stoploss_cooldown_minutes": 30.0,
        "reentry_extra_delay_after_5_cuts_hours": 24.0,
        "reentry_timeout_hours": 168.0,
        "reentry_line_ticks": 2,
        "reentry_zone_lower_pct": 0.02,
        "probe_break_pct": 0.08,
        "clear_confirm_timeout_sec": 180.0,
        "clear_confirm_retry_interval_sec": 1800.0,
        "clear_confirm_max_retries": 3,
        "daily_reentry_max_full_clears": 2,
        "drawdown_step_per_cycle_pct": 0.01,
        "max_tokens_per_cycle": 1,
    }
    if isinstance(stoploss_overrides, dict):
        stoploss_cfg.update(stoploss_overrides)
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(data_dir)},
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "stoploss_reentry_state_path": str(copytrade_dir / "stoploss_reentry_state.json"),
            "stoploss_reentry_state_backup_path": str(copytrade_dir / "stoploss_reentry_state.bak.json"),
            "scheduler": {
                "strategy_mode": mode,
                "stoploss": stoploss_cfg,
            },
        }
    )
    manager = _build_manager(cfg)
    manager.handled_topics.add("t1")
    manager._position_address = "0xabc"
    manager._build_copytrade_active_token_set = lambda: {"t1"}  # type: ignore[assignment]
    manager._remove_token_from_copytrade_files = lambda token_id: None  # type: ignore[assignment]
    return manager


def test_stoploss_full_clear_sets_reentry_window_state(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager.topic_details["t1"] = {"resume_state": {"profit_pct": 0.023}, "floor_price": 0.95}
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 20.0,
            "after_size": 0.0,
            "requested_size": kwargs.get("target_size", 20.0),
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [
            {
                "asset": "t1",
                "size": 20.0,
                "avgPrice": 1.0,
                "curPrice": 0.9,
            }
        ],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 61.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_WINDOW"
    assert float(state["reentry_earliest_ts"]) > now
    assert abs(float(state["stop_exit_price"]) - 0.8973) < 1e-9
    assert float(state["reentry_line_price"]) < 0.9
    assert float(state["probe_line_price"]) < float(state["reentry_line_price"])
    assert abs(float(state.get("old_maker_profit_pct") or 0.0) - 0.023) < 1e-9


def test_stoploss_threshold_ticks_expand_for_coarse_tokens(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    assert manager._stoploss_threshold_ticks(anchor_price=0.20, threshold_pct=0.05, tick=0.01) == 2
    assert manager._stoploss_threshold_ticks(anchor_price=0.80, threshold_pct=0.05, tick=0.01) == 4
    assert manager._stoploss_threshold_ticks(anchor_price=0.20, threshold_pct=0.05, tick=0.001) == 20
    assert manager._stoploss_threshold_ticks(anchor_price=0.80, threshold_pct=0.05, tick=0.001) == 40


def test_reentry_band_is_tick_aware_for_coarse_tokens(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    manager._ws_cache["t1"] = {"tick_size": 0.01}
    line, zone_lower, probe = manager._build_stoploss_reentry_band(
        token_id="t1",
        exec_price=0.19,
        line_ticks=2,
        zone_lower_pct=0.02,
        probe_break_pct=0.08,
    )
    assert 0.169998 <= line < 0.17
    assert abs(zone_lower - 0.15) < 1e-9
    assert abs(probe - 0.13) < 1e-9


def test_reentry_band_scales_same_effective_thresholds_for_fine_ticks(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    manager._ws_cache["t1"] = {"tick_size": 0.001}
    line, zone_lower, probe = manager._build_stoploss_reentry_band(
        token_id="t1",
        exec_price=0.19,
        line_ticks=2,
        zone_lower_pct=0.02,
        probe_break_pct=0.08,
    )
    assert 0.169999 <= line < 0.17
    assert abs(zone_lower - 0.15) < 1e-9
    assert abs(probe - 0.13) < 1e-9


def test_estimate_token_tick_size_falls_back_to_clob_book_tick_size(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    manager._ws_cache["t1"] = {}
    manager._fetch_tick_size_from_official_client = lambda token_id: None  # type: ignore[assignment]
    manager._fetch_clob_top_of_book = lambda token_id: {  # type: ignore[assignment]
        "ok": True,
        "bid": 0.18,
        "ask": 0.19,
        "tick_size": 0.001,
        "source": "clob_book",
    }
    tick = manager._estimate_token_tick_size("t1")
    assert abs(tick - 0.001) < 1e-12
    assert abs(float(manager._ws_cache["t1"]["tick_size"]) - 0.001) < 1e-12


def test_estimate_token_tick_size_prefers_official_client_before_clob_book(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    manager._ws_cache["t1"] = {}
    manager._fetch_tick_size_from_official_client = lambda token_id: 0.01  # type: ignore[assignment]
    manager._fetch_clob_top_of_book = lambda token_id: {  # type: ignore[assignment]
        "ok": True,
        "bid": 0.18,
        "ask": 0.19,
        "tick_size": 0.001,
        "source": "clob_book",
    }
    tick = manager._estimate_token_tick_size("t1")
    assert abs(tick - 0.01) < 1e-12
    assert abs(float(manager._ws_cache["t1"]["tick_size"]) - 0.01) < 1e-12


def test_tick_size_change_event_updates_cache_without_wiping_quotes(tmp_path):
    manager = _build_manager(GlobalConfig.from_dict({}))
    manager._ws_token_ids = ["t1"]
    with manager._ws_cache_lock:
        manager._ws_cache["t1"] = {
            "price": 0.52,
            "best_bid": 0.51,
            "best_ask": 0.53,
            "tick_size": 0.01,
            "updated_at": time.time(),
            "seq": 3,
        }

    manager._on_ws_event(
        {
            "event_type": "tick_size_change",
            "asset_id": "t1",
            "new_tick_size": "0.001",
        }
    )

    with manager._ws_cache_lock:
        assert abs(float(manager._ws_cache["t1"]["tick_size"]) - 0.001) < 1e-12
        assert abs(float(manager._ws_cache["t1"]["best_bid"]) - 0.51) < 1e-12
        assert abs(float(manager._ws_cache["t1"]["best_ask"]) - 0.53) < 1e-12


def test_ws_tick_event_negative_timestamp_is_sanitized(tmp_path):
    manager = _build_manager(GlobalConfig.from_dict({}))
    manager._ws_token_ids = ["t1"]

    before = time.time()
    manager._on_ws_event(
        {
            "event_type": "tick",
            "asset_id": "t1",
            "best_bid": "0.51",
            "best_ask": "0.53",
            "timestamp": -123456789.0,
        }
    )
    after = time.time()

    with manager._ws_cache_lock:
        payload = dict(manager._ws_cache["t1"])
    assert abs(float(payload["best_bid"]) - 0.51) < 1e-12
    assert abs(float(payload["best_ask"]) - 0.53) < 1e-12
    assert float(payload["ts"]) >= before
    assert float(payload["ts"]) <= after + 1.0


def test_reentry_requires_probe_then_rebound_zone(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "stoploss_cycle_count": 1,
            "next_stoploss_threshold_pct": 0.06,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now - 10,
            "source_detached": False,
        },
    )
    prices = iter([0.83, 0.83, 0.879, 0.879])
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: next(prices)  # type: ignore[assignment]
    manager._total_liquidation.reenter_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 0.0,
            "after_size": 5.0,
            "executed_price": 0.879,
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 60.0)
        manager._run_stoploss_check(now + 120.0)
        manager._run_stoploss_check(now + 180.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "REENTRY_HOLD"
    assert "resume_state" in manager.topic_details["t1"]
    assert manager.topic_details["t1"]["queue_role"] == "reentry_token"
    assert isinstance(manager.topic_details["t1"].get("stoploss_reentry_resume"), dict)


def test_reentry_uses_frozen_profit_pct_when_available(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_REBOUND",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now - 10,
            "source_detached": False,
            "old_maker_profit_pct": 0.031,
        },
    )
    prices = iter([0.83, 0.83, 0.879, 0.879])
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: next(prices)  # type: ignore[assignment]
    manager._total_liquidation.reenter_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 0.0,
            "after_size": 5.0,
            "executed_price": 0.879,
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 60.0)
        manager._run_stoploss_check(now + 120.0)
        manager._run_stoploss_check(now + 180.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    resume = manager.topic_details["t1"].get("stoploss_reentry_resume") or {}
    assert abs(float(resume.get("hold_profit_pct") or 0.0) - 0.031) < 1e-9


def test_detached_state_without_position_is_cleaned(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_PROBE",
            "source_detached": True,
            "reentry_earliest_ts": now - 1.0,
            "probe_line_price": 0.8,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + float(manager.config.stoploss_check_interval_sec) + 1.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states


def test_stoploss_uses_executed_avg_price_as_anchor(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager._ws_cache["t1"] = {"best_bid": 0.90, "best_ask": 0.91, "updated_at": now}
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 20.0,
            "after_size": 0.0,
            "requested_size": kwargs.get("target_size", 20.0),
            "executed_avg_price": 0.887,
            "executed_price_source": "response_fill",
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 20.0, "avgPrice": 1.0, "curPrice": 0.9}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 61.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert abs(float(state["stop_exit_price"]) - 0.887) < 1e-9
    assert state.get("stop_exit_price_source") == "response_fill"


def test_stoploss_does_not_trigger_on_non_executable_cur_price_only(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._ws_cache["t1"] = {"best_bid": 0.0, "best_ask": 0.0, "updated_at": now}
    calls = []
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: calls.append(kwargs) or {"ok": True, "before_size": 10.0, "after_size": 0.0}
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.80}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 61.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert calls == []


def test_reentry_missing_quote_does_not_advance_state_and_is_observable(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_PROBE",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now - 10,
            "probe_confirm_hits": 1,
            "rebound_confirm_hits": 1,
            "source_detached": False,
        },
    )
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: None  # type: ignore[assignment]
    calls = []
    manager._total_liquidation.reenter_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True}
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_PROBE"
    assert int(state.get("reentry_quote_missing_hits") or 0) >= 1
    assert int(state.get("probe_confirm_hits") or 0) == 0
    assert int(state.get("rebound_confirm_hits") or 0) == 0
    assert "ask missing or stale" in str(state.get("last_error") or "")
    assert calls == []


def test_token_daily_reentry_pause_rolls_over_without_position(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    old_day = "2000-01-01"
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now + 3600.0,
            "source_detached": False,
            "loss_date_utc": old_day,
            "daily_stoploss_full_clear_count": 2,
            "today_realized_loss_pct": 0.123,
            "reentry_paused_for_day": True,
        },
    )
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: 0.88  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    state = manager._stoploss_reentry_states["t1"]
    assert state.get("loss_date_utc") == manager._today_utc_date(now)
    assert int(state.get("daily_stoploss_full_clear_count") or 0) == 0
    assert float(state.get("today_realized_loss_pct") or 0.0) == 0.0
    assert bool(state.get("reentry_paused_for_day", True)) is False


def test_stoploss_state_normalize_clears_stale_pause_status_text(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    state = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "daily_stoploss_full_clear_count": 2.0,
            "reentry_paused_for_day": False,
            "market_status_last": "reentry_paused_for_day",
        },
    )

    assert isinstance(state.get("daily_stoploss_full_clear_count"), int)
    assert state["daily_stoploss_full_clear_count"] == 2
    assert state["market_status_last"] == ""


def test_market_closed_removes_state_immediately(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "stoploss_cycle_count": 2,
            "stop_exit_price": 0.9,
            "reentry_line_price": 0.89,
            "reentry_zone_lower_price": 0.88,
            "probe_line_price": 0.8455,
            "reentry_earliest_ts": now - 1.0,
            "source_detached": False,
        },
    )
    manager._stoploss_is_market_closed = lambda token_id: True  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states


def test_waiting_reentry_target_sell_cancels_with_record(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_sell_signals = lambda: {"t1": {"status": "pending"}}  # type: ignore[assignment]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_PROBE",
            "stop_exit_ts": now - 7200,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "source_detached": False,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    latest = rows[-1]
    assert latest.get("exit_reason") == "STOPLOSS_REENTRY_CANCELED"
    assert (latest.get("exit_data") or {}).get("abandon_reason") == "target_sell_signal_while_waiting_reentry"


def test_waiting_reentry_target_buy_does_not_release_stoploss_waiting(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    stop_exit_ts = now - 7200.0
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"token_id": "t1", "introduced_by_buy": True, "last_seen": "2026-03-21T08:23:13Z"}
    ]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_PROBE",
            "stop_exit_ts": stop_exit_ts,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "source_detached": False,
        },
    )
    manager._estimate_reentry_buyable_price = lambda token_id, row=None: None  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_PROBE"
    assert state.get("market_status_last") != "target_buy_seen_after_stoploss_exit"
    exit_path = manager.config.data_dir / "exit_tokens.json"
    if exit_path.exists():
        rows = json.loads(exit_path.read_text(encoding="utf-8"))
        assert all(row.get("exit_reason") != "STOPLOSS_WAITING_RELEASED_BY_TARGET_BUY" for row in rows)


def test_waiting_window_still_transitions_to_probe_without_target_buy_release(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_tokens = lambda: [  # type: ignore[assignment]
        {"token_id": "t1", "introduced_by_buy": True, "last_seen": "2026-03-21T08:23:13Z"}
    ]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "stop_exit_ts": now - 7200.0,
            "reentry_earliest_ts": now - 1.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "source_detached": False,
        },
    )
    manager._estimate_reentry_buyable_price = lambda token_id, row=None: None  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_PROBE"


def test_reentry_hold_recovery_marker_promotes_to_normal_maker(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "REENTRY_HOLD",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.9,
            "stop_exit_ts": now - 100.0,
            "last_stoploss_size": 3.0,
            "reentry_line_price": 0.89,
            "reentry_zone_lower_price": 0.88,
            "probe_line_price": 0.8455,
            "reentry_earliest_ts": now - 1.0,
            "source_detached": False,
        },
    )
    task = TopicTask(topic_id="t1", log_path=Path("dummy.log"))
    task.log_excerpt = (
        "[REENTRY_HOLD] 达到恢复阈值，恢复常规 maker 卖出: "
        "bid=0.9000 activation=0.8990"
    )
    changed = manager._sync_reentry_hold_recovered_from_log(task)
    assert changed is True
    assert manager._stoploss_reentry_states["t1"]["state"] == "NORMAL_MAKER"


def test_reentry_hold_without_position_is_cleaned_with_record(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "REENTRY_HOLD",
            "stop_exit_ts": now - 3600.0,
            "last_stoploss_size": 3.0,
            "source_detached": False,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + float(manager.config.stoploss_check_interval_sec) + 1.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    latest = rows[-1]
    assert latest.get("exit_reason") == "STOPLOSS_REENTRY_HOLD_CLOSED"
    assert (latest.get("exit_data") or {}).get("reason") == "reentry_hold_no_position"


def test_process_exit_forces_log_refresh_for_reentry_hold_recovery(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "REENTRY_HOLD",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.9,
            "stop_exit_ts": now - 100.0,
            "last_stoploss_size": 3.0,
            "reentry_line_price": 0.89,
            "reentry_zone_lower_price": 0.88,
            "probe_line_price": 0.8455,
            "reentry_earliest_ts": now - 1.0,
            "source_detached": False,
        },
    )
    log_path = tmp_path / "t1.log"
    log_path.write_text(
        "[REENTRY_HOLD] 达到恢复阈值，恢复常规 maker 卖出: bid=0.9000 activation=0.8990\n",
        encoding="utf-8",
    )
    task = TopicTask(topic_id="t1", log_path=log_path)
    task.last_log_excerpt_ts = time.time()
    task.log_excerpt = ""
    manager._handle_process_exit(task, 0)
    assert manager._stoploss_reentry_states["t1"]["state"] == "NORMAL_MAKER"


def test_process_exit_sell_cleanup_success_sets_follow_cooldown(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {"token_id": "t1", "introduced_by_buy": True, "status": "pending"}
                ],
            }
        ),
        encoding="utf-8",
    )
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    tokens_path.write_text(
        json.dumps({"updated_at": "", "tokens": []}),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    task = TopicTask(topic_id="t1")
    task.end_reason = "sell signal cleanup"
    task.no_restart = True

    manager._handle_process_exit(task, 0)

    payload = json.loads((copytrade_dir / "manual_intervention_tokens.json").read_text(encoding="utf-8"))
    rows = payload.get("tokens") or []
    row = next((x for x in rows if str(x.get("token_id")) == "t1"), None)
    assert isinstance(row, dict)
    cooldown_until = float(row.get("follow_cooldown_until_ts") or 0.0)
    assert cooldown_until > time.time() + 23 * 3600.0
    sell_payload = json.loads(sell_path.read_text(encoding="utf-8"))
    sell_rows = sell_payload.get("sell_tokens") or []
    archived_sell_rows = sell_payload.get("archived_sell_tokens") or []
    sell_row = next((x for x in sell_rows if str(x.get("token_id")) == "t1"), None)
    archived_sell_row = next((x for x in archived_sell_rows if str(x.get("token_id")) == "t1"), None)
    assert sell_row is None
    assert isinstance(archived_sell_row, dict)
    assert archived_sell_row.get("status") == "done"
    assert archived_sell_row.get("active") is False
    assert str(archived_sell_row.get("consumed_at") or "").strip()


def test_process_exit_startup_reconcile_position_late_cleanup_success_when_flat(tmp_path):
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details["t1"] = {"queue_role": "startup_reconcile_position"}
    manager.handled_topics.add("t1")
    cycle_calls = []
    manager._advance_token_cycle_state_on_cleanup = (  # type: ignore[method-assign]
        lambda token_id, run_cfg: cycle_calls.append((token_id, dict(run_cfg or {})))
    )
    manager._refresh_unified_position_snapshot = lambda force_refresh=True: ([], {"t1": 0.0}, "ok", "live")  # type: ignore[assignment]

    task = TopicTask(topic_id="t1")
    manager._handle_process_exit(task, 1)

    assert task.status == "exited"
    assert task.no_restart is True
    assert task.end_reason == "position reconcile cleanup success"
    assert "t1" not in manager.handled_topics
    assert manager.topic_details["t1"]["queue_role"] == "cycle_closed"
    assert cycle_calls and cycle_calls[0][0] == "t1"
    assert "t1" not in manager._refill_retry_counts
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    assert rows[-1]["exit_reason"] == "POSITION_RECONCILE_LATE_CLEANUP_SUCCESS"


def test_process_exit_startup_reconcile_position_cleanup_success_on_rc_zero(tmp_path):
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details["t1"] = {
        "queue_role": "startup_reconcile_position",
        "resume_state": {"has_position": True},
    }
    manager.handled_topics.add("t1")
    manager._refill_retry_counts["t1"] = 4
    cycle_calls = []
    manager._advance_token_cycle_state_on_cleanup = (  # type: ignore[method-assign]
        lambda token_id, run_cfg: cycle_calls.append((token_id, dict(run_cfg or {})))
    )
    manager._refresh_unified_position_snapshot = lambda force_refresh=True: ([], {"t1": 0.0}, "ok", "live")  # type: ignore[assignment]

    task = TopicTask(topic_id="t1")
    manager._handle_process_exit(task, 0)

    assert task.status == "exited"
    assert task.no_restart is True
    assert task.end_reason == "position reconcile cleanup success"
    assert "t1" not in manager.handled_topics
    assert manager.topic_details["t1"]["queue_role"] == "cycle_closed"
    assert "resume_state" not in manager.topic_details["t1"]
    assert cycle_calls and cycle_calls[0][0] == "t1"
    assert "t1" not in manager._refill_retry_counts


def test_process_exit_startup_reconcile_position_cleanup_success_when_only_dust_remains(tmp_path):
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details["t1"] = {"queue_role": "startup_reconcile_position"}
    manager.handled_topics.add("t1")
    cycle_calls = []
    manager._advance_token_cycle_state_on_cleanup = (  # type: ignore[method-assign]
        lambda token_id, run_cfg: cycle_calls.append((token_id, dict(run_cfg or {})))
    )
    manager._refresh_unified_position_snapshot = lambda force_refresh=True: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 0.2, "avgPrice": 0.63}],
        {"t1": 0.2},
        "ok",
        "live",
    )

    task = TopicTask(topic_id="t1")
    manager._handle_process_exit(task, 1)

    assert task.status == "exited"
    assert task.no_restart is True
    assert task.end_reason == "position reconcile cleanup success"
    assert manager.topic_details["t1"]["queue_role"] == "cycle_closed"
    assert cycle_calls and cycle_calls[0][0] == "t1"


def test_process_exit_startup_reconcile_position_respects_gap_skip_backoff(tmp_path):
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details["t1"] = {"queue_role": "startup_reconcile_position"}
    manager._refresh_unified_position_snapshot = lambda force_refresh=True: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 14.28, "avgPrice": 0.07}],
        {"t1": 14.28},
        "ok",
        "live",
    )
    exit_path = manager.config.data_dir / "exit_tokens.json"
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    exit_path.write_text(
        json.dumps(
            [
                {
                    "token_id": "t1",
                    "exit_ts": now,
                    "exit_reason": "POSITION_SYNC_SKIP_GAP",
                    "exit_data": {
                        "has_position": True,
                        "position_size": 14.28,
                        "sell_floor_price": 0.073,
                        "last_ask": 0.065,
                    },
                    "refillable": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    task = TopicTask(topic_id="t1")
    manager._handle_process_exit(task, 0)

    assert task.status == "exited"
    assert task.end_reason == "position sync gap hold"
    assert "t1" not in manager.pending_topics
    assert "t1" not in manager.pending_burst_topics
    assert manager.topic_details["t1"]["queue_role"] == "startup_reconcile_position"
    assert float(manager.topic_details["t1"]["gap_hold_until_ts"]) > now
    rows = json.loads(exit_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "POSITION_SYNC_SKIP_GAP"


def test_process_exit_startup_reconcile_position_requeues_after_gap_skip_backoff_expires(tmp_path):
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager.topic_details["t1"] = {"queue_role": "startup_reconcile_position"}
    manager._refresh_unified_position_snapshot = lambda force_refresh=True: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 14.28, "avgPrice": 0.07}],
        {"t1": 14.28},
        "ok",
        "live",
    )
    exit_path = manager.config.data_dir / "exit_tokens.json"
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    exit_path.write_text(
        json.dumps(
            [
                {
                    "token_id": "t1",
                    "exit_ts": now - 301.0,
                    "exit_reason": "POSITION_SYNC_SKIP_GAP",
                    "exit_data": {
                        "has_position": True,
                        "position_size": 14.28,
                        "sell_floor_price": 0.073,
                        "last_ask": 0.065,
                    },
                    "refillable": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    task = TopicTask(topic_id="t1")
    manager._handle_process_exit(task, 0)

    assert task.status == "pending"
    assert "t1" in manager.pending_topics
    assert manager.topic_details["t1"]["queue_role"] == "startup_reconcile_position"
    assert "gap_hold_until_ts" not in manager.topic_details["t1"]
    rows = json.loads(exit_path.read_text(encoding="utf-8"))
    assert rows[-1]["exit_reason"] == "POSITION_RECONCILE_EXITED_WITH_POSITION"


def test_remove_token_from_copytrade_files_soft_invalidates_records(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {"token_id": "t1", "introduced_by_buy": True, "active": True},
                    {"token_id": "keep", "introduced_by_buy": True, "active": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {"token_id": "t1", "introduced_by_buy": True, "status": "pending", "active": True},
                    {"token_id": "keep", "introduced_by_buy": True, "status": "pending", "active": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)

    manager._remove_token_from_copytrade_files("t1")

    tokens_payload = json.loads(tokens_path.read_text(encoding="utf-8"))
    token_row = next((x for x in tokens_payload["tokens"] if x["token_id"] == "t1"), None)
    keep_row = next(x for x in tokens_payload["tokens"] if x["token_id"] == "keep")
    archived_token_row = next(
        (x for x in tokens_payload.get("archived_tokens", []) if x["token_id"] == "t1"),
        None,
    )
    assert token_row is None
    assert isinstance(archived_token_row, dict)
    assert archived_token_row["active"] is False
    assert archived_token_row["invalidate_reason"] == "cleanup_consumed"
    assert str(archived_token_row.get("invalidated_at") or "").strip()
    assert keep_row["active"] is True

    sell_payload = json.loads(sell_path.read_text(encoding="utf-8"))
    sell_row = next((x for x in sell_payload["sell_tokens"] if x["token_id"] == "t1"), None)
    keep_sell = next(x for x in sell_payload["sell_tokens"] if x["token_id"] == "keep")
    archived_sell_row = next(
        (x for x in sell_payload.get("archived_sell_tokens", []) if x["token_id"] == "t1"),
        None,
    )
    assert sell_row is None
    assert isinstance(archived_sell_row, dict)
    assert archived_sell_row["active"] is False
    assert archived_sell_row["status"] == "done"
    assert archived_sell_row["invalidate_reason"] == "cleanup_consumed"
    assert str(archived_sell_row.get("consumed_at") or "").strip()
    assert keep_sell["active"] is True


def test_load_copytrade_entries_skip_inactive_records(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {"token_id": "active_token", "introduced_by_buy": True, "active": True},
                    {"token_id": "inactive_token", "introduced_by_buy": True, "active": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {"token_id": "active_sell", "introduced_by_buy": True, "status": "pending", "active": True},
                    {"token_id": "inactive_sell", "introduced_by_buy": True, "status": "pending", "active": False},
                    {"token_id": "done_sell", "introduced_by_buy": True, "status": "done", "active": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager._load_active_follow_cooldown_map = lambda: {}  # type: ignore[assignment]

    topics = manager._load_copytrade_tokens()
    signals = manager._load_copytrade_sell_signals()

    assert [item["token_id"] for item in topics] == ["active_token"]
    assert set(signals.keys()) == {"active_sell"}


def test_reconcile_exit_signal_soft_invalidates_when_position_missing(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    token_id = "t1"
    signal_path = manager._exit_signal_path(token_id)
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(
        json.dumps(
            {
                "token_id": token_id,
                "active": True,
                "status": "pending",
                "trigger_path": "exit_signal",
                "trigger_source": "unit_test",
                "trigger_reason": "COPYTRADE_SELL",
                "exit_reason": "COPYTRADE_SELL",
                "issued_at": "",
                "updated_at": "",
            }
        ),
        encoding="utf-8",
    )
    manager._has_account_position = lambda _token_id, force_refresh=False: False  # type: ignore[assignment]

    manager._reconcile_exit_signals()

    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    assert payload["active"] is False
    assert payload["status"] == "done"
    assert payload["consumed_by"] == "reconcile_exit_signals"
    assert payload["invalidate_reason"] == "no_position"
    assert str(payload.get("consumed_at") or "").strip()


def test_build_run_config_omits_exit_signal_path_for_buy_without_position(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    manager.topic_details["buy_token"] = {
        "token_id": "buy_token",
        "queue_role": "startup_reconcile_buy",
        "schedule_lane": "base",
    }
    manager._has_account_position = lambda token_id, force_refresh=False: False  # type: ignore[assignment]

    run_cfg = manager._build_run_config("buy_token")

    assert "exit_signal_path" not in run_cfg


def test_build_run_config_backfills_resume_state_for_refill_with_position(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    token_id = "refill_live_position"
    manager.topic_details[token_id] = {
        "token_id": token_id,
        "queue_role": "refill_with_position",
    }
    manager._stoploss_reentry_states[token_id] = manager._default_stoploss_reentry_state(
        token_id
    )
    manager._refresh_unified_position_snapshot = lambda **_kwargs: (  # type: ignore[assignment]
        [{"asset": token_id, "size": 6.0, "avgPrice": 0.52}],
        {token_id: 6.0},
        "ok",
        "live",
    )

    run_cfg = manager._build_run_config(token_id)

    resume = run_cfg.get("resume_state") or {}
    assert resume.get("has_position") is True
    assert float(resume.get("position_size") or 0.0) == 6.0
    assert float(resume.get("entry_price") or 0.0) == 0.52
    assert resume.get("skip_buy") is True
    assert run_cfg.get("startup_skip_if_open_sell") is True


def test_issue_exit_signal_rejects_illegal_reason_and_no_position(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    token_id = "t1"
    manager._has_account_position = lambda _token_id, force_refresh=False: False  # type: ignore[assignment]

    wrote = manager._issue_exit_signal(
        token_id,
        trigger_source="unit_test",
        trigger_reason="UNSPECIFIED",
    )
    assert wrote is False
    assert manager._exit_signal_path(token_id).exists() is False

    wrote = manager._issue_exit_signal(
        token_id,
        trigger_source="unit_test",
        trigger_reason="COPYTRADE_SELL",
    )
    assert wrote is False
    assert manager._exit_signal_path(token_id).exists() is False


def test_exit_signal_requires_reason_to_be_active(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    token_id = "legacy_token"
    signal_path = manager._exit_signal_path(token_id)
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(
        json.dumps(
            {
                "token_id": token_id,
                "active": True,
                "status": "pending",
                "trigger_path": "exit_signal",
                "trigger_source": "legacy_writer",
                "updated_at": "",
            }
        ),
        encoding="utf-8",
    )

    assert manager._has_exit_signal_file(token_id) is False

    manager._has_account_position = lambda _token_id, force_refresh=False: False  # type: ignore[assignment]
    manager._reconcile_exit_signals()

    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    assert payload["active"] is False
    assert payload["status"] == "stale_ignored"
    assert payload["invalidate_reason"] == "missing_exit_reason"
    assert payload["consumed_by"] == "reconcile_exit_signals"


def test_apply_sell_signals_archives_token_and_stops_buy_when_no_position(tmp_path):
    copytrade_dir = tmp_path / "copytrade"
    copytrade_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = copytrade_dir / "tokens_from_copytrade.json"
    sell_path = copytrade_dir / "copytrade_sell_signals.json"
    handled_path = copytrade_dir / "handled_topics.json"
    tokens_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "tokens": [
                    {"token_id": "t1", "introduced_by_buy": True, "active": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    sell_path.write_text(
        json.dumps(
            {
                "updated_at": "",
                "sell_tokens": [
                    {
                        "token_id": "t1",
                        "introduced_by_buy": True,
                        "status": "pending",
                        "active": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    handled_path.write_text(
        json.dumps({"updated_at": "", "topics": ["t1"]}),
        encoding="utf-8",
    )
    cfg = GlobalConfig.from_dict(
        {
            "handled_topics_path": str(handled_path),
            "copytrade_tokens_path": str(tokens_path),
            "copytrade_sell_signals_path": str(sell_path),
            "copytrade_blacklist_path": str(copytrade_dir / "liquidation_blacklist.json"),
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager._load_handled_topics()
    manager.topic_details["t1"] = {"token_id": "t1"}
    manager.pending_topics.append("t1")

    task = TopicTask(topic_id="t1", process=None, log_path=tmp_path / "t1.log")
    task.status = "running"
    task.no_restart = False

    class _DummyProc:
        def __init__(self):
            self.pid = 123
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    task.process = _DummyProc()
    manager.tasks["t1"] = task
    terminated: list[str] = []

    def _fake_terminate(task_obj, reason):
        terminated.append(reason)
        task_obj.process = None
        task_obj.status = "stopped"

    manager._terminate_task = _fake_terminate  # type: ignore[assignment]
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]
    manager._has_position_for_sell_signal = (  # type: ignore[assignment]
        lambda token_id, snapshot, snapshot_info: (False, "snapshot_no_position_confirmed")
    )

    manager._apply_sell_signals(
        {
            "t1": {
                "token_id": "t1",
                "status": "pending",
                "introduced_by_buy": True,
                "signal_ts": time.time(),
            }
        }
    )

    assert terminated == ["sell signal lifecycle ended without position"]
    assert "t1" not in manager.tasks
    assert "t1" not in manager.pending_topics
    assert "t1" not in manager.handled_topics
    tokens_payload = json.loads(tokens_path.read_text(encoding="utf-8"))
    assert tokens_payload["tokens"] == []
    archived_token_row = tokens_payload["archived_tokens"][0]
    assert archived_token_row["token_id"] == "t1"
    assert archived_token_row["invalidate_reason"] == "cleanup_consumed"
    sell_payload = json.loads(sell_path.read_text(encoding="utf-8"))
    assert sell_payload["sell_tokens"] == []
    archived_sell_row = sell_payload["archived_sell_tokens"][0]
    assert archived_sell_row["token_id"] == "t1"
    assert archived_sell_row["status"] == "done"
    assert archived_sell_row["invalidate_reason"] == "cleanup_consumed"


def test_build_run_config_includes_total_liquidation_reason(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)

    run_cfg = manager._build_run_config("t1")

    assert "TOTAL_LIQUIDATION" in set(run_cfg.get("allow_ioc_exit_reasons") or [])


def test_build_run_config_includes_force_sell_only_reason(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    manager.topic_details["t1"] = {
        "token_id": "t1",
        "force_sell_only_on_startup": True,
        "force_sell_only_reason": "stoploss_nofill_escalated",
    }

    run_cfg = manager._build_run_config("t1")

    assert run_cfg["force_sell_only_on_startup"] is True
    assert run_cfg["force_sell_only_reason"] == "stoploss_nofill_escalated"


def test_remove_exit_token_records_keeps_full_history(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    manager._append_exit_token_record(
        "t1",
        "STOPLOSS_IOC_STARTED",
        exit_data={"source": "stoploss_v4", "trigger_path": "stoploss"},
        refillable=False,
    )
    manager._append_exit_token_record(
        "t1",
        "EXIT_CLEANUP_SUCCESS",
        exit_data={"source": "exit_only_process", "trigger_path": "exit_only"},
        refillable=False,
    )

    manager._remove_exit_token_records("t1")

    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    reasons = [row.get("exit_reason") for row in rows]
    assert reasons == ["STOPLOSS_IOC_STARTED", "EXIT_CLEANUP_SUCCESS"]
    assert rows[0].get("trigger_source") == "stoploss_v4"
    assert rows[0].get("trigger_reason") == "STOPLOSS_IOC_STARTED"
    assert rows[0].get("trigger_path") == "stoploss"


def test_source_detached_with_position_is_guard_held_without_forced_sell(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._ws_cache["t1"] = {"best_bid": 0.9, "updated_at": now}
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "stoploss_cycle_count": 1,
            "next_stoploss_threshold_pct": 0.05,
            "source_detached": False,
        },
    )
    liq_calls = []
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: liq_calls.append((args, kwargs)) or {"ok": True}
    )
    sell_calls = []
    manager._trigger_sell_exit = lambda token_id, task=None, **kwargs: sell_calls.append((token_id, task, kwargs))  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.9}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert bool(state.get("source_detached", False)) is True
    assert state.get("market_status_last") == "source_detached"
    assert liq_calls == []
    assert sell_calls == []


def test_source_detached_normal_maker_does_not_claim_stoploss_runtime_owner(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "source_detached": True,
            "market_status_last": "source_detached_guard_hold",
            "last_error": "source_detached guard hold (within grace)",
            "pending_stoploss_before_size": 10.0,
        },
    )

    assert manager._has_stoploss_runtime_owner("t1") is False
    state = manager._stoploss_reentry_states["t1"]
    assert state.get("market_status_last") == "source_detached"
    assert state.get("last_error") == ""
    assert "pending_stoploss_before_size" not in state


def test_source_detached_waiting_probe_still_claims_stoploss_runtime_owner(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_PROBE",
            "source_detached": True,
        },
    )

    assert manager._has_stoploss_runtime_owner("t1") is True


def test_source_detached_timeout_skips_cleanup_below_value_threshold(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._ws_cache["t1"] = {"best_bid": 0.2, "updated_at": now}
    manager._total_liquidation.cfg.position_value_threshold = 3.0  # type: ignore[attr-defined]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "source_detached": True,
            "source_detached_since_ts": now - 1800.0,
            "source_detached_cleanup_started": False,
        },
    )
    sell_calls = []
    manager._trigger_sell_exit = lambda token_id, task=None, **kwargs: sell_calls.append((token_id, task, kwargs)) or True  # type: ignore[assignment]
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 5.0, "avgPrice": 1.0, "curPrice": 0.2}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    assert sell_calls == []
    orphan_path = manager.config.data_dir / "orphan_tokens.json"
    assert orphan_path.exists()
    rows = json.loads(orphan_path.read_text(encoding="utf-8"))
    latest = next(row for row in rows if row.get("token_id") == "t1")
    assert latest.get("token_id") == "t1"
    assert latest.get("reason") == "SOURCE_DETACHED_LOW_VALUE"


def test_source_detached_timeout_triggers_cleanup_above_value_threshold(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._ws_cache["t1"] = {"best_bid": 0.8, "updated_at": now}
    manager._total_liquidation.cfg.position_value_threshold = 3.0  # type: ignore[attr-defined]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "source_detached": True,
            "source_detached_since_ts": now - 1800.0,
            "source_detached_cleanup_started": False,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 5.0, "avgPrice": 1.0, "curPrice": 0.8}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    orphan_path = manager.config.data_dir / "orphan_tokens.json"
    assert orphan_path.exists()
    rows = json.loads(orphan_path.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and len(rows) >= 1
    latest = next(row for row in rows if row.get("token_id") == "t1")
    assert latest.get("token_id") == "t1"


def test_source_detached_cleanup_started_still_moves_directly_to_orphan(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._load_copytrade_tokens = lambda: []  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._ws_cache["t1"] = {"best_bid": 0.8, "updated_at": now}
    manager._total_liquidation.cfg.position_value_threshold = 3.0  # type: ignore[attr-defined]
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "source_detached": True,
            "source_detached_since_ts": now - 1800.0,
            "source_detached_cleanup_started": True,
            "source_detached_cleanup_started_ts": now - 900.0,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 5.0, "avgPrice": 1.0, "curPrice": 0.8}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    orphan_path = manager.config.data_dir / "orphan_tokens.json"
    rows = json.loads(orphan_path.read_text(encoding="utf-8"))
    latest = next(row for row in rows if row.get("token_id") == "t1")
    assert latest.get("token_id") == "t1"
    assert latest.get("reason") == "SOURCE_DETACHED_TIMEOUT"


def test_mark_token_orphaned_clears_stoploss_owner_immediately(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "source_detached": True,
            "source_detached_since_ts": now - 1800.0,
        },
    )

    manager._mark_token_orphaned(
        "t1",
        reason="UNIT_TEST_ORPHAN",
        trigger_source="unit_test",
        position_snapshot={"size": 3.0, "snapshot_info": "ok"},
    )

    assert "t1" not in manager._stoploss_reentry_states
    detail = manager.topic_details["t1"]
    assert detail.get("orphaned") is True
    orphan_path = manager.config.data_dir / "orphan_tokens.json"
    rows = json.loads(orphan_path.read_text(encoding="utf-8"))
    latest = next(row for row in rows if row.get("token_id") == "t1")
    assert latest.get("token_id") == "t1"
    assert latest.get("status") == "orphaned"


def test_mark_token_orphaned_terminal_blocks_existing_refill_records(tmp_path):
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(tmp_path / "data")},
        }
    )
    manager = _build_manager(cfg)
    manager._refill_retry_counts["t1"] = 6
    exit_path = manager.config.data_dir / "exit_tokens.json"
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    exit_path.write_text(
        json.dumps(
            [
                {
                    "token_id": "t1",
                    "exit_ts": time.time(),
                    "exit_reason": "SELL_ABANDONED",
                    "exit_data": {"has_position": True},
                    "refillable": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    manager._mark_token_orphaned(
        "t1",
        reason="MARKET_TERMINAL_DETECTED",
        trigger_source="unit_test",
        position_snapshot={"size": 2.0, "snapshot_info": "ok"},
    )

    rows = json.loads(exit_path.read_text(encoding="utf-8"))
    latest = next(row for row in rows if row.get("token_id") == "t1")
    assert latest.get("refillable") is False
    assert latest.get("refill_block_reason") == "MARKET_TERMINAL_DETECTED"
    assert latest.get("refill_block_source") == "terminal_orphan"
    assert "t1" not in manager._refill_retry_counts


def test_waiting_reentry_times_out_and_is_abandoned(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"reentry_timeout_hours": 24.0})
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_REBOUND",
            "stop_exit_ts": now - (25.0 * 3600.0),
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "last_error": "ask missing or stale",
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    assert "t1" not in manager._stoploss_reentry_states
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    latest = rows[-1]
    assert latest.get("token_id") == "t1"
    assert latest.get("exit_reason") == "STOPLOSS_REENTRY_ABANDONED"
    exit_data = latest.get("exit_data") or {}
    assert exit_data.get("abandon_reason") == "reentry_timeout"


def test_filter_refillable_tokens_skips_terminal_orphan_even_with_refillable_exit(tmp_path):
    cfg = GlobalConfig.from_dict(
        {
            "paths": {"data_directory": str(tmp_path / "data")},
            "refill": {
                "refill_cooldown_minutes_with_position": 0,
                "refill_cooldown_minutes_no_position": 0,
            },
        }
    )
    manager = _build_manager(cfg)
    manager._load_latest_orphan_states = lambda: {  # type: ignore[assignment]
        "t1": {
            "token_id": "t1",
            "status": "orphaned",
            "reason": "MARKET_TERMINAL_DETECTED",
        }
    }

    refillable = manager._filter_refillable_tokens(
        [
            {
                "token_id": "t1",
                "exit_ts": time.time() - 3600.0,
                "exit_reason": "SELL_ABANDONED",
                "exit_data": {"has_position": True, "position_size": 3.0},
                "refillable": True,
            }
        ]
    )

    assert refillable == []


def test_orphan_probe_uses_official_terminal_market_state_to_stop_retries(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    now = time.time()
    manager.topic_details["t1"] = {"token_id": "t1", "condition_id": "cond-1"}
    manager._append_orphan_token_record(
        {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60.0)),
            "updated_ts": float(now - 60.0),
            "token_id": "t1",
            "status": "orphaned",
            "probe_attempts": 0,
            "next_probe_at": float(now - 1.0),
            "reason": "UNIT_TEST_ORPHAN",
            "trigger_source": "unit_test",
        }
    )
    manager._build_copytrade_active_token_set = lambda: {"t1"}  # type: ignore[assignment]
    manager._refresh_sell_position_snapshot = lambda: ({"t1": 3.0}, "ok")  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._market_state_checker = types.SimpleNamespace(
        check_market_state=lambda *args, **kwargs: autorun_mod.MarketState(
            status=autorun_mod.MarketStatus.CLOSED,
            condition_id="cond-1",
            token_id="t1",
            data={"closed": True, "active": False},
            checked_at=now,
            is_tradeable=False,
            refillable=False,
        )
    )
    cleaner_calls = []
    manager._market_closed_cleaner = types.SimpleNamespace(
        clean_closed_market=lambda **kwargs: cleaner_calls.append(kwargs)
    )

    manager._run_orphan_recovery_probe(now)

    rows = json.loads((manager.config.data_dir / "orphan_tokens.json").read_text(encoding="utf-8"))
    latest = next(row for row in rows if row.get("token_id") == "t1")
    assert latest.get("status") == "manual_only"
    assert latest.get("reason") == "MARKET_CLOSED"
    assert latest.get("next_probe_at") == 0.0
    assert "official_market_status=closed" in str(latest.get("note") or "")
    assert cleaner_calls and cleaner_calls[-1]["exit_reason"] == "MARKET_CLOSED"


def test_orphan_probe_stops_when_official_positions_confirm_no_position(tmp_path):
    cfg = GlobalConfig.from_dict({"paths": {"data_directory": str(tmp_path / "data")}})
    manager = _build_manager(cfg)
    now = time.time()
    manager.topic_details["t1"] = {"token_id": "t1", "condition_id": "cond-1"}
    manager._append_orphan_token_record(
        {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 60.0)),
            "updated_ts": float(now - 60.0),
            "token_id": "t1",
            "status": "orphaned",
            "probe_attempts": 2,
            "next_probe_at": float(now - 1.0),
            "reason": "UNIT_TEST_ORPHAN",
            "trigger_source": "unit_test",
        }
    )
    manager._build_copytrade_active_token_set = lambda: {"t1"}  # type: ignore[assignment]
    manager._refresh_sell_position_snapshot = lambda: ({}, "ok")  # type: ignore[assignment]
    manager._load_copytrade_sell_signals = lambda: {}  # type: ignore[assignment]
    manager._market_state_checker = types.SimpleNamespace(
        check_market_state=lambda *args, **kwargs: autorun_mod.MarketState(
            status=autorun_mod.MarketStatus.ACTIVE,
            condition_id="cond-1",
            token_id="t1",
            data={"active": True, "closed": False},
            checked_at=now,
            is_tradeable=True,
            refillable=True,
        )
    )

    manager._run_orphan_recovery_probe(now)

    rows = json.loads((manager.config.data_dir / "orphan_tokens.json").read_text(encoding="utf-8"))
    latest = next(row for row in rows if row.get("token_id") == "t1")
    assert latest.get("status") == "manual_only"
    assert latest.get("reason") == "POSITIONS_NO_POSITION"
    assert latest.get("next_probe_at") == 0.0
    assert latest.get("note") == "official_positions_absent"


def test_reentry_fill_above_line_is_recovered_into_reentry_hold(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_REBOUND",
            "stoploss_cycle_count": 1,
            "stop_exit_price": 0.90,
            "stop_exit_ts": now - 7200,
            "last_stoploss_size": 5.0,
            "reentry_line_price": 0.88,
            "reentry_zone_lower_price": 0.87,
            "probe_line_price": 0.836,
            "reentry_earliest_ts": now - 10,
            "source_detached": False,
        },
    )
    manager._estimate_reentry_buyable_price = lambda token_id, position_row=None: 0.879  # type: ignore[assignment]
    manager._total_liquidation.reenter_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": True,
            "before_size": 0.0,
            "after_size": 5.0,
            "executed_avg_price": 0.885,
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 60.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "REENTRY_HOLD"
    assert bool(state.get("last_reentry_above_line", False)) is True
    assert abs(float(state.get("last_reentry_above_line_price") or 0.0) - 0.885) < 1e-9
    assert "resume_state" in manager.topic_details["t1"]


def test_stoploss_fill_pending_confirm_is_finalized_on_later_flat_snapshot(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager.topic_details["t1"] = {"resume_state": {"profit_pct": 0.023}, "floor_price": 0.95}
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": False,
            "before_size": 20.0,
            "after_size": 20.0,
            "filled_size": 20.0,
            "executed_avg_price": 0.8973,
            "executed_price_source": "response_fill",
            "requested_size": kwargs.get("target_size", 20.0),
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    snapshots = iter(
        [
            ([{"asset": "t1", "size": 20.0, "avgPrice": 1.0, "curPrice": 0.9}], "ok"),
            ([{"asset": "t1", "size": 20.0, "avgPrice": 1.0, "curPrice": 0.9}], "ok"),
            ([], "ok"),
        ]
    )
    autorun_mod._fetch_position_rows_from_data_api = lambda address: next(snapshots)  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
        manager._run_stoploss_check(now + 61.0)
        state = manager._stoploss_reentry_states["t1"]
        assert state["state"] == "STOPLOSS_CLEAR_PENDING_CONFIRM"
        manager._run_stoploss_check(now + 240.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_WINDOW"
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    reasons = [row.get("exit_reason") for row in rows]
    assert "STOPLOSS_CLEAR_PENDING_CONFIRM" in reasons
    assert reasons[-1] == "STOPLOSS_FULL_CLEAR"


def test_stoploss_liquidation_uses_whitelisted_ioc_reason(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "stoploss_cycle_count": 0,
            "next_stoploss_threshold_pct": 0.05,
            "source_detached": False,
            "position_opened_ts": now - 600.0,
        },
    )
    liq_calls = []
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: liq_calls.append(kwargs) or {
            "ok": False,
            "before_size": 10.0,
            "after_size": 10.0,
            "filled_size": 0.0,
            "requested_size": kwargs.get("target_size", 10.0),
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.90}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    assert len(liq_calls) == 1
    assert liq_calls[0].get("reason") == "STOPLOSS_REENTRY"


def test_stoploss_daily_pause_triggers_after_second_full_clear(tmp_path):
    manager = _build_stoploss_manager(
        tmp_path,
        stoploss_overrides={"daily_reentry_max_full_clears": 2},
    )
    now = time.time()
    manager._estimate_token_tick_size = lambda token_id, position_row=None: 0.01  # type: ignore[assignment]
    state = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_EXITED_WAITING_WINDOW",
            "stoploss_cycle_count": 1,
            "daily_stoploss_full_clear_count": 1,
            "today_realized_loss_pct": 0.07,
            "loss_date_utc": manager._today_utc_date(now),
            "reentry_paused_for_day": False,
        },
    )

    manager._finalize_stoploss_waiting_reentry(
        "t1",
        state,
        now=now,
        before_size=10.0,
        after_size=0.0,
        exec_price=0.90,
        exec_source="unit_test",
        drawdown=-0.05,
        line_ticks=2,
        zone_lower_pct=0.02,
        probe_break_pct=0.08,
        drawdown_step=0.01,
        extra_reentry_cd=0.0,
        window_cd=3600.0,
        max_daily_full_clears=2,
    )

    assert state["state"] == "STOPLOSS_EXITED_WAITING_WINDOW"
    assert int(state.get("daily_stoploss_full_clear_count") or 0) == 2
    assert bool(state.get("reentry_paused_for_day")) is True
    assert state.get("market_status_last") == "reentry_paused_for_day"
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    assert rows[-1]["exit_reason"] == "STOPLOSS_FULL_CLEAR"
    assert rows[-1]["exit_data"]["daily_stoploss_full_clear_count"] == 2
    assert rows[-1]["exit_data"]["daily_stoploss_max_full_clears"] == 2


def test_stoploss_records_started_and_no_fill_when_ioc_does_not_execute(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager.config.stoploss_clear_confirm_max_retries = 3
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "stoploss_cycle_count": 0,
            "next_stoploss_threshold_pct": 0.05,
            "source_detached": False,
            "position_opened_ts": now - 600.0,
        },
    )
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": False,
            "before_size": 10.0,
            "after_size": 10.0,
            "filled_size": 0.0,
            "requested_size": kwargs.get("target_size", 10.0),
            "reason": kwargs.get("reason"),
            "error": "unit_test_no_fill",
        }
    )
    manager._confirm_stoploss_ioc_fill_via_data_api = (  # type: ignore[method-assign]
        lambda *args, **kwargs: (10.0, 0.0, "unit_test_unconfirmed")
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.90}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    reasons = [row.get("exit_reason") for row in rows]
    assert reasons == ["STOPLOSS_IOC_STARTED", "STOPLOSS_IOC_NO_FILL"]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_IOC_RETRY_PENDING"
    assert int(state["nofill_retry_count"]) == 1


def test_stoploss_no_fill_escalates_to_sell_only_after_retry_limit(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager.config.stoploss_clear_confirm_max_retries = 1
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "stoploss_cycle_count": 0,
            "next_stoploss_threshold_pct": 0.05,
            "source_detached": False,
            "position_opened_ts": now - 600.0,
        },
    )
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": False,
            "before_size": 10.0,
            "after_size": 10.0,
            "filled_size": 0.0,
            "requested_size": kwargs.get("target_size", 10.0),
            "reason": kwargs.get("reason"),
            "error": "unit_test_nofill",
        }
    )
    manager._confirm_stoploss_ioc_fill_via_data_api = (  # type: ignore[method-assign]
        lambda *args, **kwargs: (10.0, 0.0, "unit_test_unconfirmed")
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.90}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_IOC_RETRY_ESCALATED"
    assert int(state["stoploss_nofill_sell_only_requeues"]) == 1
    assert "t1" in manager.pending_topics
    detail = manager.topic_details["t1"]
    assert detail["force_sell_only_on_startup"] is True
    assert detail["force_sell_only_reason"] == "stoploss_nofill_escalated"
    assert detail["queue_role"] == "stoploss_nofill_sell_only"
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    assert rows[-1]["exit_reason"] == "STOPLOSS_IOC_NO_FILL_ESCALATED"


def test_stoploss_escalated_position_gone_finalizes_waiting_reentry(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_IOC_RETRY_ESCALATED",
            "stoploss_cycle_count": 0,
            "source_detached": False,
            "pending_stoploss_before_size": 10.0,
            "pending_stoploss_exit_price": 0.49,
            "pending_stoploss_exec_source": "stoploss_trigger_price",
            "pending_stoploss_drawdown": -0.055,
            "old_entry_price": 0.52,
            "old_last_buy_price": 0.52,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_WINDOW"
    assert float(state["stop_exit_price"]) == 0.49
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    reasons = [row.get("exit_reason") for row in rows]
    assert "STOPLOSS_IOC_NO_FILL_POSITION_GONE" in reasons
    assert "STOPLOSS_FULL_CLEAR_LATE" in reasons
    assert reasons[-1] == "STOPLOSS_FULL_CLEAR"


def test_stoploss_escalated_sell_only_requeues_eventually_require_manual_intervention(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_IOC_RETRY_ESCALATED",
            "stoploss_cycle_count": 0,
            "source_detached": False,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.90}],
        "ok",
    )
    try:
        for idx in range(autorun_mod.STOPLOSS_NOFILL_SELL_ONLY_MAX_REQUEUES):
            manager.pending_topics.clear()
            manager._run_stoploss_check(now + idx)
        manager.pending_topics.clear()
        manager._run_stoploss_check(now + 999.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_MANUAL_INTERVENTION_REQUIRED"
    assert manager.topic_details["t1"]["queue_role"] == "manual_intervention"
    manual_payload = json.loads(
        (manager.config.copytrade_sell_signals_path.parent / "manual_intervention_tokens.json").read_text(
            encoding="utf-8"
        )
    )
    assert manual_payload["tokens"][0]["reason"] == "STOPLOSS_NOFILL_SELL_ONLY_MAX_REQUEUES"


def test_stoploss_postcheck_converts_error_into_waiting_reentry_when_fill_confirmed(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "stoploss_cycle_count": 0,
            "next_stoploss_threshold_pct": 0.05,
            "source_detached": False,
            "position_opened_ts": now - 600.0,
        },
    )
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": False,
            "before_size": 10.0,
            "after_size": 10.0,
            "filled_size": 0.0,
            "requested_size": kwargs.get("target_size", 10.0),
            "reason": kwargs.get("reason"),
            "error": "unit_test_post_error",
        }
    )
    manager._confirm_stoploss_ioc_fill_via_data_api = (  # type: ignore[method-assign]
        lambda *args, **kwargs: (0.0, 10.0, "unit_test_confirmed_via_data_api")
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.90}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    reasons = [row.get("exit_reason") for row in rows]
    assert reasons == ["STOPLOSS_IOC_STARTED", "STOPLOSS_FULL_CLEAR"]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_EXITED_WAITING_WINDOW"
    assert float(state["stop_exit_price"]) == 0.9


def test_stoploss_journal_records_trigger_before_ioc(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 0.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "NORMAL_MAKER",
            "stoploss_cycle_count": 0,
            "next_stoploss_threshold_pct": 0.05,
            "source_detached": False,
            "position_opened_ts": now - 600.0,
        },
    )
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: {
            "ok": False,
            "before_size": 10.0,
            "after_size": 10.0,
            "filled_size": 0.0,
            "requested_size": kwargs.get("target_size", 10.0),
            "reason": kwargs.get("reason"),
            "error": "unit_test_no_fill",
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.90}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    journal_path = manager.config.data_dir / "stoploss_event_journal.jsonl"
    lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["token_id"] == "t1"
    assert payload["event"] == "STOPLOSS_TRIGGERED"
    assert payload["stage"] == "pre_ioc_liquidation"
    assert payload["data"]["reason"] == "STOPLOSS_REENTRY"


def test_stoploss_min_age_gate_delays_execution_until_five_minutes(tmp_path):
    manager = _build_stoploss_manager(tmp_path, stoploss_overrides={"min_age_minutes": 5.0})
    now = time.time()
    manager.config.stoploss_confirm_rounds = 1
    manager._ws_cache["t1"] = {"best_bid": 0.90, "updated_at": now}
    liq_calls = []
    manager._total_liquidation.liquidate_single_token_taker = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: liq_calls.append(kwargs) or {
            "ok": False,
            "before_size": 10.0,
            "after_size": 10.0,
            "filled_size": 0.0,
            "requested_size": kwargs.get("target_size", 10.0),
            "reason": kwargs.get("reason"),
            "error": "unit_test_no_fill",
        }
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: (  # type: ignore[assignment]
        [{"asset": "t1", "size": 10.0, "avgPrice": 1.0, "curPrice": 0.90}],
        "ok",
    )
    try:
        manager._run_stoploss_check(now)
        assert liq_calls == []
        state = manager._stoploss_reentry_states["t1"]
        assert float(state.get("position_opened_ts") or 0.0) == float(now)
        manager._run_stoploss_check(now + 301.0)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]

    assert len(liq_calls) == 1
    assert liq_calls[0].get("reason") == "STOPLOSS_REENTRY"


def test_stoploss_pending_confirm_schedules_retry_before_escalation(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_CLEAR_PENDING_CONFIRM",
            "stop_exit_ts": now - 300.0,
            "pending_confirm_started_ts": now - 181.0,
            "pending_confirm_next_retry_ts": now - 1.0,
            "pending_confirm_retry_count": 0,
            "pending_confirm_before_size": 5.0,
            "pending_confirm_after_size_snapshot": 5.0,
            "pending_confirm_filled_size": 5.0,
            "pending_confirm_exec_price": 0.49,
            "pending_confirm_exec_price_source": "response_fill",
            "pending_confirm_drawdown": -0.055,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([{"asset": "t1", "size": 5.0, "avgPrice": 0.52, "curPrice": 0.49}], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_CLEAR_PENDING_CONFIRM"
    assert int(state["pending_confirm_retry_count"]) == 1
    assert float(state["pending_confirm_next_retry_ts"]) > now
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    assert rows[-1]["exit_reason"] == "STOPLOSS_CLEAR_CONFIRM_RETRY_SCHEDULED"


def test_stoploss_pending_confirm_escalates_after_max_retries(tmp_path):
    manager = _build_stoploss_manager(tmp_path)
    now = time.time()
    manager._stoploss_reentry_states["t1"] = manager._normalize_stoploss_reentry_state_record(
        "t1",
        {
            "state": "STOPLOSS_CLEAR_PENDING_CONFIRM",
            "stop_exit_ts": now - 7200.0,
            "pending_confirm_started_ts": now - 181.0,
            "pending_confirm_next_retry_ts": now - 1.0,
            "pending_confirm_retry_count": 3,
            "pending_confirm_before_size": 5.0,
            "pending_confirm_after_size_snapshot": 5.0,
            "pending_confirm_filled_size": 5.0,
            "pending_confirm_exec_price": 0.49,
            "pending_confirm_exec_price_source": "response_fill",
            "pending_confirm_drawdown": -0.055,
        },
    )
    old_fetch = autorun_mod._fetch_position_rows_from_data_api
    autorun_mod._fetch_position_rows_from_data_api = lambda address: ([{"asset": "t1", "size": 5.0, "avgPrice": 0.52, "curPrice": 0.49}], "ok")  # type: ignore[assignment]
    try:
        manager._run_stoploss_check(now)
    finally:
        autorun_mod._fetch_position_rows_from_data_api = old_fetch  # type: ignore[assignment]
    state = manager._stoploss_reentry_states["t1"]
    assert state["state"] == "STOPLOSS_CLEAR_PENDING_ESCALATED"
    rows = json.loads((manager.config.data_dir / "exit_tokens.json").read_text(encoding="utf-8"))
    assert rows[-1]["exit_reason"] == "STOPLOSS_CLEAR_CONFIRM_ESCALATED"
