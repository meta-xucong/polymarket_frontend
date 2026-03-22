"""Compatibility tests for strategy callbacks used in the run loop."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Volatility_arbitrage_run import (
    _is_buy_stage_active_for_inactive_release,
    _strategy_accepts_total_position,
)


class _OldStrategy:
    def __init__(self) -> None:
        self.calls = []

    def on_buy_filled(self, avg_price, size=None):  # pragma: no cover - signature only
        self.calls.append((avg_price, size))


class _NewStrategy:
    def __init__(self) -> None:
        self.calls = []

    def on_buy_filled(  # pragma: no cover - signature only
        self, avg_price, size=None, *, total_position=None
    ):
        self.calls.append((avg_price, size, total_position))


def test_strategy_without_total_position_is_detected() -> None:
    strategy = _OldStrategy()
    assert not _strategy_accepts_total_position(strategy)

    kwargs = {"avg_price": 0.66, "size": 10.0}
    if _strategy_accepts_total_position(strategy):
        kwargs["total_position"] = 10.0

    strategy.on_buy_filled(**kwargs)
    assert strategy.calls == [(0.66, 10.0)]


def test_strategy_with_total_position_is_detected() -> None:
    strategy = _NewStrategy()
    assert _strategy_accepts_total_position(strategy)

    kwargs = {"avg_price": 0.66, "size": 10.0}
    if _strategy_accepts_total_position(strategy):
        kwargs["total_position"] = 10.0

    strategy.on_buy_filled(**kwargs)
    assert strategy.calls == [(0.66, 10.0, 10.0)]


def test_buy_inactive_release_only_applies_to_flat_buy_stage() -> None:
    assert _is_buy_stage_active_for_inactive_release(
        current_state="FLAT",
        awaiting=None,
        strategy_position_size=0.0,
        local_position_size=None,
        effective_min_order_size=5.0,
        sell_only_active=False,
        now_ts=3600.0,
        buy_cooldown_until=0.0,
    )
    assert not _is_buy_stage_active_for_inactive_release(
        current_state="LONG",
        awaiting=None,
        strategy_position_size=5.0,
        local_position_size=5.0,
        effective_min_order_size=5.0,
        sell_only_active=False,
        now_ts=3600.0,
        buy_cooldown_until=0.0,
    )
    assert not _is_buy_stage_active_for_inactive_release(
        current_state="FLAT",
        awaiting=None,
        strategy_position_size=0.0,
        local_position_size=None,
        effective_min_order_size=5.0,
        sell_only_active=True,
        now_ts=3600.0,
        buy_cooldown_until=0.0,
    )
    assert not _is_buy_stage_active_for_inactive_release(
        current_state="FLAT",
        awaiting=None,
        strategy_position_size=0.0,
        local_position_size=None,
        effective_min_order_size=5.0,
        sell_only_active=False,
        now_ts=300.0,
        buy_cooldown_until=600.0,
    )
