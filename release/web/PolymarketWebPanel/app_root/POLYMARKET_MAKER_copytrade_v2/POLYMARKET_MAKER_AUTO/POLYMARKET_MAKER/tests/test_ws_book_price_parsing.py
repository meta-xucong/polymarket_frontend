import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace()

from poly_maker_autorun import _extract_best_bid_ask_from_book_event, _extract_top_price_from_levels


def test_extract_top_price_from_levels_uses_best_not_first():
    bid_levels = [
        {"price": "0.21", "size": "1"},
        {"price": "0.35", "size": "2"},
        {"price": "0.29", "size": "3"},
    ]
    ask_levels = [
        {"price": "0.99", "size": "1"},
        {"price": "0.57", "size": "2"},
        {"price": "0.61", "size": "3"},
    ]

    assert _extract_top_price_from_levels(bid_levels, "bid") == 0.35
    assert _extract_top_price_from_levels(ask_levels, "ask") == 0.57


def test_extract_top_price_from_levels_ignores_non_positive_size():
    ask_levels = [
        {"price": "0.999", "size": "0"},
        {"price": "0.61", "size": "5"},
    ]
    bid_levels = [
        ["0.001", "0"],
        ["0.42", "3"],
    ]

    assert _extract_top_price_from_levels(ask_levels, "ask") == 0.61
    assert _extract_top_price_from_levels(bid_levels, "bid") == 0.42


def test_book_event_prefers_explicit_best_fields_over_levels():
    ev = {
        "event_type": "book",
        "asset_id": "token-A",
        "best_bid": "0.44",
        "best_ask": "0.46",
        "buys": [{"price": "0.2", "size": "1"}],
        "sells": [{"price": "0.99", "size": "1"}],
    }

    bid, ask = _extract_best_bid_ask_from_book_event(ev)
    assert bid == 0.44
    assert ask == 0.46
