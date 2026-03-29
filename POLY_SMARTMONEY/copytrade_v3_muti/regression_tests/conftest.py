import importlib.util
import sys
import threading
from pathlib import Path

import pytest


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
    spec = importlib.util.spec_from_file_location("pm_auto_regression", AUTORUN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def autorun_mod():
    return _load_autorun_module()


@pytest.fixture(scope="session")
def strategy_source():
    return STRATEGY_PATH.read_text(encoding="utf-8")


@pytest.fixture
def manager_factory(autorun_mod, tmp_path):
    def _factory():
        manager = autorun_mod.AutoRunManager.__new__(autorun_mod.AutoRunManager)
        manager.config = autorun_mod.GlobalConfig()
        manager.topic_details = {}
        manager.tasks = {}
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
        manager._position_address = ""
        manager._position_address_origin = ""
        manager._position_address_warned = False
        manager._ws_cache = {}

        manager._load_latest_orphan_states = lambda: {}
        manager._orphan_state_blocks_refill = lambda state: False
        manager._has_account_position = lambda token_id: False
        manager._get_condition_id_for_token = lambda token_id: None
        manager._remove_pending_topic = lambda token_id: manager.pending_topics.discard(token_id)
        manager._enqueue_pending_topic = lambda token_id: manager.pending_topics.add(token_id) or True
        manager._get_task_run_config = lambda task: {}
        manager._advance_token_cycle_state_on_cleanup = lambda *args, **kwargs: None
        manager._remove_from_handled_topics = lambda *args, **kwargs: None
        manager._mark_token_cycle_closed_runtime = lambda *args, **kwargs: None
        return manager

    return _factory
