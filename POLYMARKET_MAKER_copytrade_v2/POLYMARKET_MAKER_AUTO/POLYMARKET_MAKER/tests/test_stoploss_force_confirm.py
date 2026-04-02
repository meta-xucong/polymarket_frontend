import sys
import types
import threading
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

from poly_maker_autorun import AutoRunManager, GlobalConfig


def _build_min_manager(tmp_path):
    manager = AutoRunManager.__new__(AutoRunManager)
    manager.config = GlobalConfig()
    manager.topic_details = {}
    manager.tasks = {}
    manager.handled_topics = set()
    manager.pending_topics = set()
    manager.pending_burst_topics = set()
    manager.pending_exit_topics = set()
    manager._refilled_tokens = set()
    manager._refill_retry_counts = {}
    manager._stoploss_reentry_states = {}
    manager._active_unmanaged_rearm_blocked_until = {}
    manager._buy_paused_due_to_balance = False
    manager._market_state_checker = None
    manager._market_closed_cleaner = None
    manager._file_io_lock = threading.RLock()
    manager._exit_tokens_path = tmp_path / "exit_tokens.json"
    manager._refill_debug = False
    manager._unified_position_rows = []
    manager._unified_position_snapshot = {}
    manager._unified_position_info = ""
    manager._unified_position_ts = 0.0
    manager._position_address = "0xabc"
    manager._position_address_origin = "test"
    manager._position_address_warned = False
    manager._ws_cache = {}
    manager._stoploss_quote_cycle_cache = {}

    manager._save_stoploss_reentry_states = lambda: None
    manager._load_copytrade_tokens = lambda: []
    manager._load_copytrade_sell_signals = lambda: {}
    manager._update_stoploss_source_detached_flags = lambda *args, **kwargs: False
    manager._has_actionable_position = lambda token_id, size, row=None: float(size) > 0
    manager._stoploss_is_market_closed = lambda token_id: False
    manager._append_stoploss_event_journal = lambda *args, **kwargs: None
    manager._append_exit_token_record = lambda *args, **kwargs: None
    manager._prepare_token_for_stoploss_execution = lambda *args, **kwargs: None
    manager._resolve_profit_pct_for_token = lambda token_id: 0.01
    return manager


def test_force_wide_spread_stoploss_requires_persistent_confirmation(tmp_path):
    manager = _build_min_manager(tmp_path)
    token_id = "tok"
    row = {
        "asset": token_id,
        "size": 10,
        "avgPrice": 0.18,
        "curPrice": 0.18,
    }
    state = manager._default_stoploss_reentry_state(token_id)
    state["position_opened_ts"] = 100.0
    manager._stoploss_reentry_states[token_id] = state
    manager.handled_topics = {token_id}
    manager._refresh_unified_position_snapshot = (
        lambda force_refresh, now: ([row], {}, "ok", "test")
    )
    manager._estimate_stoploss_sellable_price = lambda token_id, row: 0.16
    manager._estimate_token_tick_size = lambda token_id, row=None: 0.01
    manager._get_stoploss_cycle_quote = lambda token_id: {"bid": 0.16, "ask": 0.19}
    manager.config.stoploss_wide_spread_confirm_rounds = 3
    manager.config.stoploss_wide_spread_confirm_window_sec = 30.0

    liq_calls = []

    class _FakeLiquidation:
        _running = False

        @staticmethod
        def liquidate_single_token_taker(*args, **kwargs):
            liq_calls.append((args, kwargs))
            return {
                "after_size": 0.0,
                "before_size": 10.0,
                "filled_size": 10.0,
                "executed_avg_price": 0.16,
            }

    manager._total_liquidation = _FakeLiquidation()

    manager._run_stoploss_check(now=1000.0)

    assert liq_calls == []
    assert state["wide_spread_stoploss_confirm_hits"] == 1
    assert state["stoploss_confirm_hits"] == 0
