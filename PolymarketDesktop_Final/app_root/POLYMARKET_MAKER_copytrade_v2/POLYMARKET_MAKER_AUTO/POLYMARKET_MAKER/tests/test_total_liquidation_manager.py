from __future__ import annotations

import json
import os
import tempfile
import types
import time
from pathlib import Path
from types import SimpleNamespace

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from total_liquidation_manager import TotalLiquidationManager


class _Task:
    def __init__(self, running: bool, log_excerpt: str = ""):
        self._running = running
        self.log_excerpt = log_excerpt
        self.last_log_excerpt_ts = 0.0

    def is_running(self) -> bool:
        return self._running


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Autorun:
    def __init__(self, cfg, running_tasks: int, log_excerpt: str = ""):
        self.config = cfg
        self.tasks = {str(i): _Task(True, log_excerpt=log_excerpt) for i in range(running_tasks)}
        self._ws_cache_lock = _Lock()
        self._ws_cache = {}
        self.pending_topics = []
        self.pending_exit_topics = []


def _build_cfg(tmp: Path, enable: bool = True):
    return SimpleNamespace(
        data_dir=tmp / "data",
        log_dir=tmp / "logs",
        max_concurrent_tasks=10,
        total_liquidation={
            "enable_total_liquidation": enable,
            "min_interval_hours": 72,
            "trigger": {
                "idle_slot_ratio_threshold": 0.5,
                "idle_slot_duration_minutes": 1,
                "startup_grace_hours": 6,
                "no_trade_duration_minutes": 1,
                "min_free_balance": 20,
                "low_balance_force_hours": 6,
                "enable_low_balance_force_trigger": True,
                "require_conditions": 2,
            },
            "liquidation": {
                "taker_slippage_bps": 30,
            },
            "reset": {
                "hard_reset_enabled": True,
                "remove_logs": True,
                "remove_json_state": True,
            },
        },
    )


def test_disabled_never_triggers():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=False)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        _install_fake_clob_modules()
        autorun = _Autorun(cfg, running_tasks=0)
        metrics = mgr.update_metrics(autorun)
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is False
        assert reasons == []


def test_trigger_with_two_conditions_and_interval_guard():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        _install_fake_clob_modules()
        mgr.cfg.startup_grace_hours = 0
        autorun = _Autorun(cfg, running_tasks=0)

        mgr._idle_since = time.time() - 120
        mgr._last_trade_activity_ts = time.time() - 120
        mgr._last_fill_activity_ts = time.time() - 120
        metrics = mgr.update_metrics(autorun)
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is True
        assert len(reasons) >= 2

        mgr._save_state({"last_trigger_ts": time.time()})
        ok2, _ = mgr.should_trigger(metrics)
        assert ok2 is False




def test_force_trigger_when_low_balance_persists_long_enough():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        _install_fake_clob_modules()
        autorun = _Autorun(cfg, running_tasks=10)

        mgr.cfg.startup_grace_hours = 0
        mgr.cfg.no_trade_duration_minutes = 99999
        mgr.cfg.idle_slot_duration_minutes = 99999
        mgr.cfg.require_conditions = 2
        mgr.cfg.low_balance_force_hours = 6
        mgr._cached_free_balance = 10.0
        mgr._next_balance_probe_at = time.time() + 60

        metrics = mgr.update_metrics(autorun)
        assert metrics["low_balance_since"] is not None

        mgr._low_balance_since = time.time() - (6 * 3600 + 10)
        metrics = mgr.update_metrics(autorun)
        ok, reasons = mgr.should_trigger(metrics)

        assert ok is True
        assert any("low_balance_for=" in r for r in reasons)


def test_force_trigger_resets_after_balance_recovers():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        _install_fake_clob_modules()
        autorun = _Autorun(cfg, running_tasks=10)

        mgr.cfg.startup_grace_hours = 0
        mgr.cfg.no_trade_duration_minutes = 99999
        mgr.cfg.idle_slot_duration_minutes = 99999
        mgr.cfg.require_conditions = 2
        mgr.cfg.low_balance_force_hours = 6

        mgr._cached_free_balance = 10.0
        mgr._next_balance_probe_at = time.time() + 60
        metrics = mgr.update_metrics(autorun)
        assert metrics["low_balance_since"] is not None

        mgr._cached_free_balance = 100.0
        mgr._next_balance_probe_at = time.time() + 60
        metrics = mgr.update_metrics(autorun)
        assert metrics["low_balance_since"] is None

        ok, reasons = mgr.should_trigger(metrics)
        assert ok is False
        assert not any("low_balance_for=" in r for r in reasons)

def test_hard_reset_preserves_copytrade_config_and_clears_state_files():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        project_root = base / "POLYMARKET_MAKER_AUTO"
        copytrade_dir = base / "copytrade"
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        copytrade_dir.mkdir(parents=True, exist_ok=True)

        # data/log 文件
        (cfg.log_dir / "a.log").write_text("x", encoding="utf-8")
        (cfg.data_dir / "autorun_status.json").write_text("{}", encoding="utf-8")

        # copytrade：配置文件应保留，状态文件应清空重建
        (copytrade_dir / "copytrade_config.json").write_text('{"targets":[1]}', encoding="utf-8")
        (copytrade_dir / "tokens_from_copytrade.json").write_text('{"tokens":[{"token_id":"1"}]}', encoding="utf-8")
        (copytrade_dir / "copytrade_sell_signals.json").write_text('{"sell_tokens":[{"token_id":"1"}]}', encoding="utf-8")
        (copytrade_dir / "copytrade_state.json").write_text('{"targets":{"a":1}}', encoding="utf-8")
        (copytrade_dir / "liquidation_blacklist.json").write_text('{"tokens":[{"token_id":"1"}]}', encoding="utf-8")

        mgr = TotalLiquidationManager(cfg, project_root)
        autorun = _Autorun(cfg, running_tasks=0)
        mgr._hard_reset_files(autorun)

        assert (copytrade_dir / "copytrade_config.json").exists()
        assert not (cfg.log_dir / "a.log").exists()
        assert not (cfg.data_dir / "autorun_status.json").exists()

        tokens_payload = json.loads((copytrade_dir / "tokens_from_copytrade.json").read_text(encoding="utf-8"))
        assert tokens_payload.get("tokens") == []
        signals_payload = json.loads((copytrade_dir / "copytrade_sell_signals.json").read_text(encoding="utf-8"))
        assert signals_payload.get("sell_tokens") == []
        state_payload = json.loads((copytrade_dir / "copytrade_state.json").read_text(encoding="utf-8"))
        assert state_payload.get("targets") == {}
        blacklist_payload = json.loads((copytrade_dir / "liquidation_blacklist.json").read_text(encoding="utf-8"))
        assert blacklist_payload.get("tokens") == [{"token_id": "1"}]


def test_taker_price_applies_slippage_bps():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        px = mgr._compute_taker_price(bid=0.5, ask=0.51)
        assert abs(px - 0.5 * (1 - 0.003)) < 1e-9




def _install_fake_balance_types_module():
    import types

    fake_mod = types.ModuleType("py_clob_client.clob_types")

    class _AssetType:
        COLLATERAL = "COLLATERAL"

    class _BalanceAllowanceParams:
        def __init__(self, asset_type=None, token_id=None, signature_type=-1):
            self.asset_type = asset_type
            self.token_id = token_id
            self.signature_type = signature_type

    fake_mod.AssetType = _AssetType
    fake_mod.BalanceAllowanceParams = _BalanceAllowanceParams
    old = sys.modules.get("py_clob_client.clob_types")
    sys.modules["py_clob_client.clob_types"] = fake_mod
    return old


def _restore_fake_balance_types_module(old):
    if old is None:
        sys.modules.pop("py_clob_client.clob_types", None)
    else:
        sys.modules["py_clob_client.clob_types"] = old




def test_balance_query_uses_official_balance_allowance_params_shape():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        captured = {}

        class _FakeClient:
            def get_balance_allowance(self, params):
                captured["asset_type"] = getattr(params, "asset_type", None)
                captured["token_id"] = getattr(params, "token_id", "__missing__")
                captured["signature_type"] = getattr(params, "signature_type", None)
                return {"balance": "3.21"}

        mgr._cached_client = _FakeClient()
        autorun = _Autorun(cfg, running_tasks=0)

        old_mod = _install_fake_balance_types_module()
        try:
            value = mgr._query_free_balance_usdc(autorun)
        finally:
            _restore_fake_balance_types_module(old_mod)

        assert value == 3.21
        assert captured["asset_type"] == "COLLATERAL"
        assert captured["token_id"] is None
        assert captured["signature_type"] == -1

def test_balance_probe_is_rate_limited():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["trigger"]["balance_poll_interval_sec"] = 9999
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _FakeClient:
            def __init__(self):
                self.calls = 0

            def get_balance_allowance(self, params):
                self.calls += 1
                return {"balance": "100000000"}

        fake = _FakeClient()
        mgr._cached_client = fake
        autorun = _Autorun(cfg, running_tasks=0)

        old_mod = _install_fake_balance_types_module()
        try:
            v1 = mgr._query_free_balance_usdc(autorun)
            v2 = mgr._query_free_balance_usdc(autorun)
        finally:
            _restore_fake_balance_types_module(old_mod)

        assert v1 == 100.0
        assert v2 == 100.0
        assert fake.calls == 1


def test_balance_query_can_bypass_enabled_flag_for_buy_gate():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=False)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _FakeClient:
            def get_balance_allowance(self, params):
                return {"balance": "25000000"}

        mgr._cached_client = _FakeClient()
        autorun = _Autorun(cfg, running_tasks=0)

        old_mod = _install_fake_balance_types_module()
        try:
            disabled_value = mgr._query_free_balance_usdc(autorun)
            bypass_value = mgr._query_free_balance_usdc(autorun, ignore_enabled=True, force=True)
        finally:
            _restore_fake_balance_types_module(old_mod)

        assert disabled_value is None
        assert bypass_value == 25.0


def test_balance_query_force_reprobe_ignores_rate_limit_cache():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["trigger"]["balance_poll_interval_sec"] = 9999
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _FakeClient:
            def __init__(self):
                self.calls = 0

            def get_balance_allowance(self, params):
                self.calls += 1
                return {"balance": "12000000" if self.calls == 1 else "8000000"}

        fake = _FakeClient()
        mgr._cached_client = fake
        autorun = _Autorun(cfg, running_tasks=0)

        old_mod = _install_fake_balance_types_module()
        try:
            v1 = mgr._query_free_balance_usdc(autorun)
            v2 = mgr._query_free_balance_usdc(autorun, force=True)
        finally:
            _restore_fake_balance_types_module(old_mod)

        assert v1 == 12.0
        assert v2 == 8.0
        assert fake.calls == 2


def test_startup_grace_blocks_idle_trigger():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["trigger"]["startup_grace_hours"] = 6
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        autorun = _Autorun(cfg, running_tasks=0)

        mgr._idle_since = time.time() - 3600
        mgr._last_trade_activity_ts = time.time()
        metrics = mgr.update_metrics(autorun)
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is False
        assert all("idle_slots" not in r for r in reasons)




def test_trade_activity_refreshes_even_if_last_line_text_repeats():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        autorun = _Autorun(cfg, running_tasks=1, log_excerpt="[MAKER][BUY] 挂单 -> price=0.33 qty=5")
        task = autorun.tasks["0"]
        task.last_log_excerpt_ts = 100.0

        t1_trade, t1_fill = mgr._collect_trade_activity_ts(autorun)
        assert t1_trade > 0
        assert t1_fill == 0

        # 文本不变，但时间戳更新，说明是新一轮日志刷新，应被视为活动
        task.last_log_excerpt_ts = 101.0
        t2_trade, t2_fill = mgr._collect_trade_activity_ts(autorun)
        assert t2_trade > 0
        assert t2_fill == 0


def test_trade_activity_is_based_on_order_behavior_not_ws_updates():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        mgr.cfg.startup_grace_hours = 0
        mgr.cfg.no_trade_duration_minutes = 1

        # 先让无交易活动超时
        autorun = _Autorun(cfg, running_tasks=1, log_excerpt="[DIAG] no-op")
        mgr._last_trade_activity_ts = time.time() - 120
        mgr._last_fill_activity_ts = time.time() - 120
        metrics = mgr.update_metrics(autorun)
        _, reasons = mgr.should_trigger(metrics)
        assert any("no_trade_for=" in r for r in reasons)

        # 仅挂单行为不应清零 no_trade（规则以真实成交为准）
        autorun.tasks["0"].log_excerpt = "[MAKER][BUY] 挂单 -> price=0.33 qty=5"
        autorun.tasks["0"].last_log_excerpt_ts = 102.0
        metrics2 = mgr.update_metrics(autorun)
        _, reasons2 = mgr.should_trigger(metrics2)
        assert any("no_trade_for=" in r for r in reasons2)

        # 出现真实成交行为后，no_trade 应清零
        autorun.tasks["0"].log_excerpt = "[MAKER][BUY] 挂单状态 -> price=0.33 filled=1.0000 remaining=4.0000 status=LIVE"
        autorun.tasks["0"].last_log_excerpt_ts = 103.0
        metrics3 = mgr.update_metrics(autorun)
        _, reasons3 = mgr.should_trigger(metrics3)
        assert all("no_trade_for=" not in r for r in reasons3)



def test_fill_activity_detects_non_last_log_line():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        mgr.cfg.startup_grace_hours = 0
        mgr.cfg.no_trade_duration_minutes = 1

        autorun = _Autorun(cfg, running_tasks=1, log_excerpt="[DIAG] no-op")
        mgr._last_fill_activity_ts = time.time() - 120

        # 成交行不在最后一行，之前实现会漏记
        autorun.tasks["0"].log_excerpt = (
            "[MAKER][BUY] 挂单状态 -> price=0.33 filled=1.0000 remaining=4.0000 status=LIVE\n"
            "[DIAG] heartbeat"
        )
        autorun.tasks["0"].last_log_excerpt_ts = 104.0

        metrics = mgr.update_metrics(autorun)
        _, reasons = mgr.should_trigger(metrics)
        assert all("no_trade_for=" not in r for r in reasons)


def test_liquidation_scope_only_copytrade_tokens():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        project_root = base / "POLYMARKET_MAKER_AUTO"
        copytrade_dir = base / "copytrade"
        copytrade_dir.mkdir(parents=True, exist_ok=True)
        (copytrade_dir / "tokens_from_copytrade.json").write_text(
            '{"tokens":[{"token_id":"A"}]}' , encoding="utf-8"
        )
        (copytrade_dir / "copytrade_sell_signals.json").write_text(
            '{"sell_tokens":[]}', encoding="utf-8"
        )

        mgr = TotalLiquidationManager(cfg, project_root)

        fake_mod = types.ModuleType("maker_execution")
        fake_mod.maker_sell_follow_ask_with_floor_wait = lambda **kwargs: {"status": "FILLED"}
        sys.modules["maker_execution"] = fake_mod

        mgr._fetch_positions = lambda: [
            {"token_id": "A", "size": 10, "price": 0.5},
            {"token_id": "B", "size": 10, "price": 0.5},
        ]

        called = []
        mgr._place_sell_ioc = lambda client, token_id, price, size: called.append(token_id) or {}

        class _Client:
            pass

        mgr._cached_client = _Client()

        autorun = _Autorun(cfg, running_tasks=0)
        result = mgr._liquidate_positions(autorun)
        assert result["liquidated"] == 1
        assert called == ["A"]



def test_append_blacklist_tokens_merges_existing_rows():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        project_root = base / "POLYMARKET_MAKER_AUTO"

        mgr = TotalLiquidationManager(cfg, project_root)
        mgr.blacklist_path.parent.mkdir(parents=True, exist_ok=True)
        mgr.blacklist_path.write_text(
            json.dumps({"updated_at": "", "tokens": [{"token_id": "A", "blocked_at": "old"}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        added = mgr._append_blacklist_tokens(["A", "B", ""])
        assert added == 1

        payload = json.loads(mgr.blacklist_path.read_text(encoding="utf-8"))
        tokens = {row["token_id"] for row in payload.get("tokens", [])}
        assert tokens == {"A", "B"}




def test_balance_parser_ignores_allowance_only_payload():
    payload = {"allowance": "5000", "nested": {"x": "999"}}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed is None




def test_balance_parser_supports_nested_balance_amount_payload():
    payload = {"balance": {"amount": "1.38"}, "allowance": "5000"}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 1.38


def test_balance_parser_supports_balance_list_payload():
    payload = {"balance": ["1.38"], "allowance": "5000"}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 1.38




def test_balance_parser_rejects_allowance_inside_balance_subtree():
    payload = {"balance": {"allowance": "5000"}, "foo": "1"}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed is None


def test_balance_parser_supports_balance_list_of_objects_payload():
    payload = {"balance": [{"allowance": "5000"}, {"amount": "1.38"}]}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 1.38

def test_balance_parser_prefers_balance_field_over_other_numeric_values():
    payload = {
        "allowance": "5000",
        "nested": {"x": "999"},
        "balance": "1.38",
    }
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 1.38


def test_balance_parser_normalizes_integer_balance_string_from_base_units():
    payload = {"balance": "53608824", "allowance": "999999999"}
    parsed = TotalLiquidationManager._extract_balance_float(payload)
    assert parsed == 53.608824


def test_should_trigger_hits_low_balance_after_usdc_unit_normalization():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["trigger"]["min_free_balance"] = 20
        cfg.total_liquidation["trigger"]["require_conditions"] = 1
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        metrics = {
            "idle_since": None,
            "last_trade_activity_ts": time.time(),
            "free_balance": TotalLiquidationManager._extract_balance_float({"balance": "19000000"}),
            "in_startup_grace": True,
        }
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is True
        assert any("free_balance=19.0000<min=20.0000" in r for r in reasons)


def test_balance_query_does_not_call_none_fallback_on_exception():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _FakeClient:
            def __init__(self):
                self.calls = 0

            def get_balance_allowance(self, params):
                self.calls += 1
                raise RuntimeError("boom")

        fake = _FakeClient()
        mgr._cached_client = fake
        mgr._cached_free_balance = 7.0
        autorun = _Autorun(cfg, running_tasks=0)

        old_mod = _install_fake_balance_types_module()
        try:
            v = mgr._query_free_balance_usdc(autorun)
        finally:
            _restore_fake_balance_types_module(old_mod)

        assert v == 7.0
        assert fake.calls == 1
        assert mgr._last_balance_probe_error == "boom"


def test_liquidation_scope_empty_skips_for_safety():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        project_root = base / "POLYMARKET_MAKER_AUTO"
        mgr = TotalLiquidationManager(cfg, project_root)

        fake_mod = types.ModuleType("maker_execution")
        fake_mod.maker_sell_follow_ask_with_floor_wait = lambda **kwargs: {"status": "FILLED"}
        sys.modules["maker_execution"] = fake_mod

        mgr._fetch_positions = lambda: [{"token_id": "A", "size": 10, "price": 0.5}]

        called = []
        mgr._place_sell_ioc = lambda client, token_id, price, size: called.append(token_id) or {}

        class _Client:
            pass

        mgr._cached_client = _Client()

        autorun = _Autorun(cfg, running_tasks=0)
        result = mgr._liquidate_positions(autorun)
        assert result["liquidated"] == 0
        assert called == []
        assert any("scope is empty" in err for err in result["errors"])


def test_liquidation_scope_all_positions_liquidates_without_copytrade_files():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.total_liquidation["liquidation"]["token_scope_mode"] = "all_positions"
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        project_root = base / "POLYMARKET_MAKER_AUTO"
        mgr = TotalLiquidationManager(cfg, project_root)

        fake_mod = types.ModuleType("maker_execution")
        fake_mod.maker_sell_follow_ask_with_floor_wait = lambda **kwargs: {"status": "FILLED"}
        sys.modules["maker_execution"] = fake_mod

        mgr._fetch_positions = lambda: [
            {"token_id": "A", "size": 10, "price": 0.5},
            {"token_id": "B", "size": 10, "price": 0.5},
        ]

        called = []
        mgr._place_sell_ioc = lambda client, token_id, price, size: called.append(token_id) or {}

        class _Client:
            pass

        mgr._cached_client = _Client()

        autorun = _Autorun(cfg, running_tasks=0)
        result = mgr._liquidate_positions(autorun)
        assert result["liquidated"] == 2
        assert called == ["A", "B"]


def test_precheck_all_positions_does_not_require_copytrade_scope():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.total_liquidation["liquidation"]["token_scope_mode"] = "all_positions"
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")

        fake_client = object()
        mgr._cached_client = fake_client
        err, client, scope = mgr._precheck_liquidation_ready()
        assert err is None
        assert client is fake_client
        assert scope is None


def test_execute_restart_only_mode_skips_liquidation_and_hard_reset():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.total_liquidation["execution_mode"] = "restart_only"
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = ["x"]
                self.pending_exit_topics = ["y"]
                self.stop_event = _Evt()

            def _cleanup_all_tasks(self):
                raise RuntimeError("should not cleanup")

        autorun = _Run()

        mgr._precheck_liquidation_ready = lambda: (_ for _ in ()).throw(RuntimeError("should not precheck"))
        mgr._liquidate_positions = lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("should not liquidate"))
        mgr._hard_reset_files = lambda _a: (_ for _ in ()).throw(RuntimeError("should not hard reset"))

        result = mgr.execute(autorun, ["cond_a", "cond_b"])

        assert result["hard_reset"] is False
        assert result["liquidated"] == 0
        assert autorun.stop_event.called is True
        assert mgr._state.get("last_trigger_ts") is not None
        assert mgr._state.get("execution_mode") == "restart_only"


def test_execute_aborted_does_not_hard_reset_or_stop():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = ["x"]
                self.pending_exit_topics = ["y"]
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

        autorun = _Run()

        hard_reset_called = {"v": False}
        mgr._hard_reset_files = lambda _a: hard_reset_called.__setitem__("v", True)
        mgr._liquidate_positions = lambda _a: {
            "liquidated": 0,
            "maker_count": 0,
            "taker_count": 0,
            "errors": ["copytrade token scope is empty; skip liquidation for safety"],
            "aborted": True,
        }

        result = mgr.execute(autorun, ["cond_a", "cond_b"])
        assert result["hard_reset"] is False
        assert hard_reset_called["v"] is False
        assert autorun.stop_event.called is False


def test_execute_aborted_does_not_set_trigger_cooldown():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")

        class _Evt:
            def set(self):
                return None

        class _Run:
            def __init__(self):
                self.pending_topics = []
                self.pending_exit_topics = []
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

        mgr._liquidate_positions = lambda _a: {
            "liquidated": 0,
            "maker_count": 0,
            "taker_count": 0,
            "errors": ["client init failed"],
            "aborted": True,
        }

        mgr.execute(_Run(), ["cond_a", "cond_b"])
        assert mgr._get_last_trigger_ts() == 0.0
        assert mgr._state.get("last_abort_ts") is not None


def test_execute_precheck_abort_keeps_runtime_intact():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")
        mgr._precheck_liquidation_ready = lambda: ("client init failed", None, None)

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        touched = {"stop_ws": 0, "cleanup": 0}

        class _Run:
            def __init__(self):
                self.pending_topics = ["x"]
                self.pending_exit_topics = ["y"]
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                touched["stop_ws"] += 1

            def _cleanup_all_tasks(self):
                touched["cleanup"] += 1

        autorun = _Run()
        result = mgr.execute(autorun, ["cond_a", "cond_b"])

        assert result.get("aborted") is True
        assert touched["stop_ws"] == 0
        assert touched["cleanup"] == 0
        assert autorun.pending_topics == ["x"]
        assert autorun.pending_exit_topics == ["y"]
        assert autorun.stop_event.called is False


def test_execute_exception_requests_restart():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")
        mgr._precheck_liquidation_ready = lambda: (None, object(), {"A"})

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = []
                self.pending_exit_topics = []
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

        mgr._liquidate_positions = lambda _a, **_kw: (_ for _ in ()).throw(RuntimeError("boom"))

        autorun = _Run()
        result = mgr.execute(autorun, ["cond_a", "cond_b"])
        assert any("boom" in e for e in result["errors"])
        assert autorun.stop_event.called is True


def test_execute_uses_prechecked_scope_without_reloading():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")
        mgr._hard_reset_files = lambda _a: None

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = []
                self.pending_exit_topics = []
                self.stop_event = _Evt()
                self.reset_called = False

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

            def _reset_all_runtime_state(self):
                self.reset_called = True

        fake_client = object()
        mgr._precheck_liquidation_ready = lambda: (None, fake_client, {"A"})
        mgr._load_copytrade_token_scope = lambda: (_ for _ in ()).throw(RuntimeError("should not reload scope"))
        mgr._fetch_positions = lambda: [{"token_id": "A", "size": 10, "price": 0.5}]
        mgr._resolve_bid_ask = lambda _a, _t: (0.5, 0.5)
        called = []
        mgr._place_sell_ioc = lambda client, token_id, price, size: called.append((client, token_id)) or {}

        autorun = _Run()
        result = mgr.execute(autorun, ["cond_a", "cond_b"])
        assert result.get("aborted") is None
        assert result["liquidated"] == 1
        assert called and called[0][0] is fake_client
        assert autorun.reset_called is True
        assert autorun.stop_event.called is True


def test_execute_liquidation_does_not_stop_ws_aggregator():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")
        mgr._precheck_liquidation_ready = lambda: (None, object(), {"A"})
        mgr._liquidate_positions = lambda _a, **_kw: {
            "liquidated": 0,
            "maker_count": 0,
            "taker_count": 0,
            "errors": ["dry run"],
            "aborted": True,
        }

        touched = {"stop_ws": 0, "cleanup": 0, "suspend": 0, "resume": 0}

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = ["x"]
                self.pending_exit_topics = ["y"]
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                touched["stop_ws"] += 1

            def _cleanup_all_tasks(self):
                touched["cleanup"] += 1

            def _suspend_ws_updates(self, _reason=""):
                touched["suspend"] += 1

            def _resume_ws_updates(self, _reason=""):
                touched["resume"] += 1

        autorun = _Run()
        result = mgr.execute(autorun, ["cond_a", "cond_b"])

        assert result.get("aborted") is True
        assert touched["stop_ws"] == 0
        assert touched["cleanup"] == 1
        assert touched["suspend"] == 1
        assert touched["resume"] == 1


def test_liquidation_uses_taker_when_quote_stale_even_if_spread_wide():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")

        # 若误走 Maker 分支，测试应失败
        fake_mod = types.ModuleType("maker_execution")
        fake_mod.maker_sell_follow_ask_with_floor_wait = lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("maker path should not be used for stale quote")
        )
        sys.modules["maker_execution"] = fake_mod

        mgr._fetch_positions = lambda: [{"token_id": "A", "size": 10, "price": 0.5}]
        mgr._load_copytrade_token_scope = lambda: {"A"}

        taker_calls = []
        mgr._place_sell_ioc = lambda client, token_id, price, size: taker_calls.append((token_id, price, size)) or {}

        class _Client:
            pass

        mgr._cached_client = _Client()

        autorun = _Autorun(cfg, running_tasks=0)
        # 报价存在但已过期（总清仓里 WS 通常已停止）
        autorun._ws_cache = {
            "A": {
                "best_bid": 0.4,
                "best_ask": 0.8,
                "updated_at": time.time() - 300,
            }
        }

        result = mgr._liquidate_positions(autorun)
        assert result["liquidated"] == 1
        assert result["maker_count"] == 0
        assert result["taker_count"] == 1
        assert len(taker_calls) == 1


def test_should_trigger_fallback_startup_grace_when_metric_missing():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["trigger"]["startup_grace_hours"] = 6
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        mgr._idle_since = time.time() - 3600
        mgr._last_trade_activity_ts = time.time()
        metrics = {
            "idle_since": mgr._idle_since,
            "last_trade_activity_ts": mgr._last_trade_activity_ts,
            "free_balance": 100.0,
        }
        ok, reasons = mgr.should_trigger(metrics)
        assert ok is False
        assert all("idle_slots" not in r for r in reasons)


def _install_fake_clob_modules():
    fake_clob_types = types.ModuleType("py_clob_client.clob_types")

    class _OrderArgs:
        def __init__(self, token_id, side, price, size):
            self.token_id = token_id
            self.side = side
            self.price = price
            self.size = size

    class _OrderType:
        FAK = "FAK"
        IOC = "IOC"
        FOK = "FOK"

    class _OpenOrderParams:
        pass

    fake_clob_types.OrderArgs = _OrderArgs
    fake_clob_types.OrderType = _OrderType
    fake_clob_types.OpenOrderParams = _OpenOrderParams

    fake_constants = types.ModuleType("py_clob_client.order_builder.constants")
    fake_constants.SELL = "SELL"

    fake_client_mod = types.ModuleType("py_clob_client.client")

    class _ClobClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self._api_creds = None

        def create_or_derive_api_creds(self):
            return {"api_key": "k", "api_secret": "s", "api_passphrase": "p"}

        def set_api_creds(self, creds):
            self._api_creds = creds

    fake_client_mod.ClobClient = _ClobClient

    fake_pkg = types.ModuleType("py_clob_client")
    fake_pkg.__path__ = []
    fake_order_builder = types.ModuleType("py_clob_client.order_builder")

    sys.modules["py_clob_client"] = fake_pkg
    sys.modules["py_clob_client.client"] = fake_client_mod
    sys.modules["py_clob_client.clob_types"] = fake_clob_types
    sys.modules["py_clob_client.order_builder"] = fake_order_builder
    sys.modules["py_clob_client.order_builder.constants"] = fake_constants

    # 让 Volatility_arbitrage_main_rest.get_client() 在测试环境下可初始化
    os.environ.setdefault("POLY_KEY", "0x" + "1" * 64)
    os.environ.setdefault("POLY_FUNDER", "0x" + "2" * 40)


def test_place_sell_ioc_fak_no_match_fallback_to_ladder_price():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _Client:
            def __init__(self):
                self.prices = []

            def create_order(self, order):
                self.prices.append(float(order.price))
                return {"price": order.price}

            def post_order(self, _signed, _order_type):
                if len(self.prices) < 3:
                    raise Exception("no orders found to match with FAK order")
                return {"status": "accepted"}

        client = _Client()
        resp = mgr._place_sell_ioc(client, token_id="A", price=0.5, size=10)
        assert resp["status"] == "accepted"
        assert len(client.prices) == 3
        assert client.prices[0] > client.prices[1] > client.prices[2]


def test_place_sell_ioc_non_fak_error_raises_immediately():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _Client:
            def create_order(self, order):
                return order

            def post_order(self, _signed, _order_type):
                raise RuntimeError("signature expired")

        try:
            mgr._place_sell_ioc(_Client(), token_id="A", price=0.5, size=10)
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "signature expired" in str(exc)
        assert raised is True


def test_fill_activity_requires_positive_quantity_for_filled_or_sold_markers():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        autorun = _Autorun(cfg, running_tasks=1, log_excerpt="[MAKER][BUY] 挂单状态 -> price=0.33 filled=0.0000 remaining=10.0000 status=LIVE")
        _, fill_ts_zero = mgr._collect_trade_activity_ts(autorun)
        assert fill_ts_zero == 0

        autorun.tasks["0"].last_log_excerpt_ts = 1.0
        autorun.tasks["0"].log_excerpt = "[MAKER][SELL] 挂单状态 -> price=0.95 sold=0.00 remaining=10.00 status=LIVE"
        _, fill_ts_sold_zero = mgr._collect_trade_activity_ts(autorun)
        assert fill_ts_sold_zero == 0

        autorun.tasks["0"].last_log_excerpt_ts = 2.0
        autorun.tasks["0"].log_excerpt = "[MAKER][BUY] 挂单状态 -> price=0.33 filled=1.2500 remaining=8.7500 status=LIVE"
        _, fill_ts_positive = mgr._collect_trade_activity_ts(autorun)
        assert fill_ts_positive > 0


def test_fill_activity_does_not_use_plain_chinese_keywords_without_quantity():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        autorun = _Autorun(cfg, running_tasks=1, log_excerpt="[MAKER][BUY] 买入成交，等待后续同步")
        _, fill_ts = mgr._collect_trade_activity_ts(autorun)
        assert fill_ts == 0


def test_execute_wait_timeout_uses_liquidation_config_minutes():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = _build_cfg(base, enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        cfg.total_liquidation["liquidation"]["task_stop_timeout_minutes"] = 4

        mgr = TotalLiquidationManager(cfg, base / "POLYMARKET_MAKER_AUTO")
        mgr._precheck_liquidation_ready = lambda: (None, object(), {"A"})

        observed = {"timeout": None}

        def _fake_wait(_autorun, timeout_sec=0.0):
            observed["timeout"] = timeout_sec
            return False

        mgr._wait_for_tasks_stopped = _fake_wait

        class _Evt:
            def __init__(self):
                self.called = False

            def set(self):
                self.called = True

        class _Run:
            def __init__(self):
                self.pending_topics = []
                self.pending_exit_topics = []
                self.stop_event = _Evt()

            def _stop_ws_aggregator(self):
                return None

            def _cleanup_all_tasks(self):
                return None

        result = mgr.execute(_Run(), ["cond_a", "cond_b"])
        assert result.get("aborted") is True
        assert observed["timeout"] == 240.0


def test_fetch_positions_accepts_official_list_payload():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        old_addr = os.environ.get("POLY_DATA_ADDRESS")
        os.environ["POLY_DATA_ADDRESS"] = "0x1111111111111111111111111111111111111111"

        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        calls = {"n": 0}

        def _fake_urlopen(req, timeout=0):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp([{"asset": "A", "size": "1.0"}])
            return _Resp([])

        import total_liquidation_manager as tlm
        old_urlopen = tlm.urllib.request.urlopen
        tlm.urllib.request.urlopen = _fake_urlopen
        try:
            rows = mgr._fetch_positions()
        finally:
            tlm.urllib.request.urlopen = old_urlopen
            if old_addr is None:
                os.environ.pop("POLY_DATA_ADDRESS", None)
            else:
                os.environ["POLY_DATA_ADDRESS"] = old_addr

        assert len(rows) == 1
        assert mgr._last_positions_fetch_error is None


def test_parse_trade_timestamp_supports_ms_and_iso():
    ms = TotalLiquidationManager._parse_trade_timestamp(1735689600000)
    iso = TotalLiquidationManager._parse_trade_timestamp("2025-01-01T00:00:00Z")
    assert abs(ms - 1735689600.0) < 1e-6
    assert abs(iso - 1735689600.0) < 1e-6


def test_update_metrics_uses_data_api_trade_when_fill_not_in_last_line():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        mgr.cfg.startup_grace_hours = 0
        mgr.cfg.no_trade_duration_minutes = 1

        old_addr = os.environ.get("POLY_DATA_ADDRESS")
        os.environ["POLY_DATA_ADDRESS"] = "0x1111111111111111111111111111111111111111"

        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def _fake_urlopen(req, timeout=0):
            url = req.full_url
            if "/trades?" in url:
                return _Resp([{"timestamp": time.time(), "side": "BUY", "asset": "A"}])
            return _Resp([])

        import total_liquidation_manager as tlm
        old_urlopen = tlm.urllib.request.urlopen
        tlm.urllib.request.urlopen = _fake_urlopen
        try:
            autorun = _Autorun(cfg, running_tasks=1, log_excerpt="[DIAG] heartbeat")
            mgr._last_fill_activity_ts = time.time() - 120
            metrics = mgr.update_metrics(autorun)
            _, reasons = mgr.should_trigger(metrics)
        finally:
            tlm.urllib.request.urlopen = old_urlopen
            if old_addr is None:
                os.environ.pop("POLY_DATA_ADDRESS", None)
            else:
                os.environ["POLY_DATA_ADDRESS"] = old_addr

        assert all("no_trade_for=" not in r for r in reasons)


def test_reenter_single_token_taker_requires_ask_quote():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        mgr._cached_client = object()
        mgr._fetch_single_position_size = lambda _token_id: 0.0
        mgr._resolve_bid_ask = lambda _autorun, _token_id: (0.45, 0.0)
        place_calls = []
        mgr._place_buy_ioc = lambda *_args, **_kwargs: place_calls.append(True)

        resp = mgr.reenter_single_token_taker(
            _Autorun(cfg, running_tasks=0),
            "t1",
            target_size=3.0,
            reference_buy_price=0.5,
            reason="unit_test",
            max_attempts=1,
        )

        assert resp.get("ok") is False
        assert "missing taker ask quote" in str(resp.get("error") or "")
        assert place_calls == []


def test_reenter_single_token_taker_rejects_stale_ask_quote():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")
        mgr._cached_client = object()
        mgr._fetch_single_position_size = lambda _token_id: 0.0
        mgr._resolve_bid_ask = lambda _autorun, _token_id: (0.45, 0.46)
        mgr._is_quote_fresh = lambda _autorun, _token_id, max_age_sec=30.0: False
        place_calls = []
        mgr._place_buy_ioc = lambda *_args, **_kwargs: place_calls.append(True)

        resp = mgr.reenter_single_token_taker(
            _Autorun(cfg, running_tasks=0),
            "t1",
            target_size=3.0,
            reference_buy_price=0.5,
            reason="unit_test",
            max_attempts=1,
        )

        assert resp.get("ok") is False
        assert "stale taker ask quote" in str(resp.get("error") or "")
        assert place_calls == []


def test_single_token_ioc_liquidation_blocks_non_whitelist_reason_and_records_audit():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        audit_rows = []
        autorun = _Autorun(cfg, running_tasks=0)
        autorun._append_exit_token_record = (  # type: ignore[attr-defined]
            lambda token_id, exit_reason, **kwargs: audit_rows.append(
                (token_id, exit_reason, kwargs)
            )
        )

        place_calls = []
        mgr._cached_client = object()
        mgr._place_sell_ioc = lambda *_args, **_kwargs: place_calls.append(True)

        resp = mgr.liquidate_single_token_taker(
            autorun,
            "t1",
            target_size=3.0,
            reason="SOURCE_DETACHED_TIMEOUT",
            max_attempts=1,
        )

        assert resp.get("ok") is False
        assert resp.get("blocked") is True
        assert resp.get("ioc_allowed") is False
        assert resp.get("reason") == "SOURCE_DETACHED_TIMEOUT"
        assert place_calls == []
        assert len(audit_rows) == 1
        token_id, exit_reason, kwargs = audit_rows[0]
        assert token_id == "t1"
        assert exit_reason == "IOC_BLOCKED_NON_WHITELIST"
        exit_data = kwargs.get("exit_data") or {}
        assert exit_data.get("source") == "total_liquidation_single_token_taker"
        assert exit_data.get("requested_reason") == "SOURCE_DETACHED_TIMEOUT"


def test_fetch_open_orders_falls_back_to_open_order_params():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        _install_fake_clob_modules()
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        class _Client:
            def get_orders(self, params=None):
                if params is None:
                    raise TypeError("params required")
                return [{"id": "o1", "asset_id": "t1", "side": "SELL"}]

        rows = mgr._fetch_open_orders(_Client())
        assert rows == [{"order_id": "o1", "token_id": "t1", "side": "SELL"}]


def test_single_token_ioc_liquidation_blocks_when_open_orders_still_live():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        place_calls = []
        mgr._cached_client = object()
        mgr._fetch_single_position_size = lambda _token_id: 10.0
        mgr._clear_open_orders_for_token = lambda *_args, **_kwargs: {
            "cleared": False,
            "rounds": 3,
            "canceled_ids": ["o1", "o2"],
            "cancel_errors": [],
            "remaining_orders": [{"order_id": "o3", "token_id": "t1", "side": "SELL"}],
        }
        mgr._place_sell_ioc = lambda *_args, **_kwargs: place_calls.append(True)

        resp = mgr.liquidate_single_token_taker(
            _Autorun(cfg, running_tasks=0),
            "t1",
            reason="STOPLOSS_REENTRY",
            max_attempts=1,
        )

        assert resp.get("ok") is False
        assert "open orders still live after cancel" in str(resp.get("error") or "")
        assert "remaining_order_ids=o3" in str(resp.get("error") or "")
        assert (resp.get("cancel_debug") or {}).get("rounds") == 3
        assert place_calls == []


def test_single_token_ioc_liquidation_treats_post_error_position_clear_as_success():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        size_reads = iter([10.0, 0.0, 0.0, 0.0])
        mgr._cached_client = object()
        mgr._fetch_single_position_size = lambda _token_id: next(size_reads)
        mgr._cancel_open_orders_for_token = lambda *_args, **_kwargs: 0
        mgr._wait_for_open_orders_clear = lambda *_args, **_kwargs: True
        mgr._resolve_bid_ask = lambda _autorun, _token_id: (0.14, 0.15)
        mgr._place_sell_ioc = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("not enough balance / allowance")
        )

        resp = mgr.liquidate_single_token_taker(
            _Autorun(cfg, running_tasks=0),
            "t1",
            reason="STOPLOSS_REENTRY",
            max_attempts=1,
        )

        assert resp.get("ok") is True
        assert resp.get("filled_size") == 10.0
        assert resp.get("after_size") == 0.0
        assert resp.get("error") in (None, "")


def test_clear_open_orders_for_token_retries_and_clears_on_second_round():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        fetch_responses = iter(
            [
                [{"order_id": "o1", "token_id": "t1", "side": "SELL"}],
                [{"order_id": "o1", "token_id": "t1", "side": "SELL"}],
                [],
            ]
        )
        canceled = []
        mgr._fetch_open_orders = lambda _client: next(fetch_responses)
        mgr._cancel_orders_batch = lambda _client, order_ids: {
            "used_batch": True,
            "canceled_ids": list(order_ids),
            "not_canceled": [],
        }
        mgr._wait_for_open_orders_clear = lambda *_args, **_kwargs: len(canceled) >= 2

        def _wait(*_args, **_kwargs):
            return len(canceled) >= 2

        mgr._wait_for_open_orders_clear = _wait

        original_batch = mgr._cancel_orders_batch

        def _record_batch(_client, order_ids):
            canceled.extend(order_ids)
            return original_batch(_client, order_ids)

        mgr._cancel_orders_batch = _record_batch

        info = mgr._clear_open_orders_for_token(object(), "t1", cancel_rounds=3)

        assert info["cleared"] is True
        assert info["rounds"] == 2
        assert canceled == ["o1", "o1"]
        assert info["remaining_orders"] == []


def test_clear_open_orders_for_token_reports_cancel_errors():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        mgr._fetch_open_orders = lambda _client: [
            {"order_id": "o1", "token_id": "t1", "side": "SELL"}
        ]

        def _raise_cancel(_client, _order_id):
            raise RuntimeError("cancel failed")

        mgr._cancel_orders_batch = lambda *_args, **_kwargs: {"used_batch": False, "canceled_ids": [], "not_canceled": []}
        mgr._cancel_order_compat = _raise_cancel
        mgr._wait_for_open_orders_clear = lambda *_args, **_kwargs: False

        info = mgr._clear_open_orders_for_token(object(), "t1", cancel_rounds=1)

        assert info["cleared"] is False
        assert info["rounds"] == 1
        assert info["cancel_errors"] == ["o1:cancel failed"]
        assert info["remaining_orders"] == [{"order_id": "o1", "token_id": "t1", "side": "SELL"}]


def test_clear_open_orders_for_token_records_not_canceled_from_batch_response():
    with tempfile.TemporaryDirectory() as td:
        cfg = _build_cfg(Path(td), enable=True)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        mgr = TotalLiquidationManager(cfg, Path(td) / "POLYMARKET_MAKER_AUTO")

        mgr._fetch_open_orders = lambda _client: [
            {"order_id": "o1", "token_id": "t1", "side": "SELL"}
        ]
        mgr._cancel_orders_batch = lambda *_args, **_kwargs: {
            "used_batch": True,
            "canceled_ids": [],
            "not_canceled": ["o1:not owned"],
        }
        mgr._wait_for_open_orders_clear = lambda *_args, **_kwargs: False

        info = mgr._clear_open_orders_for_token(object(), "t1", cancel_rounds=1)

        assert info["cleared"] is False
        assert info["not_canceled"] == ["o1:not owned"]
