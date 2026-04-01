import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Volatility_arbitrage_strategy import ActionType, StrategyConfig, VolArbStrategy


def test_buy_requires_three_10s_holds_below_drop_threshold() -> None:
    cfg = StrategyConfig(
        token_id="T",
        drop_pct=0.01,
        buy_confirm_hold_seconds=10.0,
        buy_confirm_required_hits=3,
    )
    strategy = VolArbStrategy(cfg)

    strategy.on_tick(best_bid=0.78, best_ask=0.79, ts=0.0)
    strategy.on_tick(best_bid=0.78, best_ask=0.79, ts=1.0)

    assert strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=2.0) is None
    assert strategy.status()["buy_confirm"]["hits"] == 0

    assert strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=11.9) is None
    assert strategy.status()["buy_confirm"]["hits"] == 0

    assert strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=12.0) is None
    assert strategy.status()["buy_confirm"]["hits"] == 1

    assert strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=22.0) is None
    assert strategy.status()["buy_confirm"]["hits"] == 2

    action = strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=32.0)
    assert action is not None
    assert action.action == ActionType.BUY
    assert action.extra["buy_confirm_hits"] == 3
    assert action.extra["buy_confirm_required_hits"] == 3
    assert action.extra["buy_confirm_hold_seconds"] == pytest.approx(10.0)


def test_buy_confirm_resets_when_price_recovers_above_threshold() -> None:
    cfg = StrategyConfig(
        token_id="T",
        drop_pct=0.01,
        buy_confirm_hold_seconds=10.0,
        buy_confirm_required_hits=3,
    )
    strategy = VolArbStrategy(cfg)

    strategy.on_tick(best_bid=0.78, best_ask=0.79, ts=0.0)
    strategy.on_tick(best_bid=0.78, best_ask=0.79, ts=1.0)

    assert strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=2.0) is None
    assert strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=12.0) is None
    assert strategy.status()["buy_confirm"]["hits"] == 1

    assert strategy.on_tick(best_bid=0.77, best_ask=0.79, ts=13.0) is None
    confirm = strategy.status()["buy_confirm"]
    assert confirm["active_since_ts"] is None
    assert confirm["hits"] == 0

    assert strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=14.0) is None
    assert strategy.status()["buy_confirm"]["hits"] == 0
    assert strategy.on_tick(best_bid=0.76, best_ask=0.77, ts=24.0) is None
    assert strategy.status()["buy_confirm"]["hits"] == 1


def test_on_sell_filled_treats_dust_as_flat():
    cfg = StrategyConfig(token_id="T", min_market_order_size=5.0)
    strategy = VolArbStrategy(cfg)

    strategy.on_buy_filled(avg_price=0.5, size=10.0)
    strategy.on_sell_filled(avg_price=0.55, size=9.0, remaining=4.0)

    status = strategy.status()
    assert status["state"] == "FLAT"
    assert status["awaiting"] is None
    assert status["position_size"] is None


def test_on_sell_filled_marks_remaining_sell_pending():
    cfg = StrategyConfig(token_id="T", min_market_order_size=5.0)
    strategy = VolArbStrategy(cfg)

    strategy.on_buy_filled(avg_price=0.5, size=10.0)
    strategy.on_sell_filled(avg_price=0.6, size=4.0, remaining=6.0)

    status = strategy.status()
    assert status["state"] == "LONG"
    assert status["awaiting"] == ActionType.SELL
    assert status["position_size"] == pytest.approx(6.0)


def test_mark_awaiting_allows_external_sell_flag():
    cfg = StrategyConfig(token_id="T")
    strategy = VolArbStrategy(cfg)

    strategy.on_buy_filled(avg_price=0.5, size=10.0)
    strategy.mark_awaiting(ActionType.SELL)

    status = strategy.status()
    assert status["state"] == "LONG"
    assert status["awaiting"] == ActionType.SELL
