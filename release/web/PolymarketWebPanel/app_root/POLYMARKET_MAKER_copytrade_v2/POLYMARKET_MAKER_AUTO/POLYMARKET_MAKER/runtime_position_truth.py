from __future__ import annotations

from typing import Any, Dict, Optional

POSITION_TRUTH_ZERO = "ZERO"
POSITION_TRUTH_DUST = "DUST_NON_ACTIONABLE"
POSITION_TRUTH_ACTIONABLE = "ACTIONABLE"

DEFAULT_POSITION_ZERO_EPSILON = 1e-4
DEFAULT_ACTIONABLE_MIN_ORDER_SIZE = 5.0

_MIN_ORDER_SIZE_KEYS = (
    "min_order_size",
    "minimum_order_size",
    "market_min_order_size",
    "minMarketOrderSize",
    "minOrderSize",
)


def normalize_position_size(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if normalized <= 0:
        return None
    return normalized


def normalize_market_min_order_size(
    value: Any,
    *,
    default: Optional[float] = DEFAULT_ACTIONABLE_MIN_ORDER_SIZE,
) -> Optional[float]:
    normalized = normalize_position_size(value)
    if normalized is not None:
        return normalized
    if default is None:
        return None
    fallback = normalize_position_size(default)
    return fallback if fallback is not None else None


def extract_market_min_order_size(
    payload: Any,
    *,
    default: Optional[float] = None,
) -> Optional[float]:
    if isinstance(payload, dict):
        for key in _MIN_ORDER_SIZE_KEYS:
            normalized = normalize_market_min_order_size(payload.get(key), default=None)
            if normalized is not None:
                return normalized
        for nested_key in ("market", "market_info", "metadata", "params"):
            nested = payload.get(nested_key)
            if isinstance(nested, dict):
                normalized = extract_market_min_order_size(nested, default=None)
                if normalized is not None:
                    return normalized
    return normalize_market_min_order_size(default, default=None)


def classify_position_truth(
    position_size: Any,
    *,
    market_min_order_size: Any = None,
    fallback_actionable_min_order_size: float = DEFAULT_ACTIONABLE_MIN_ORDER_SIZE,
    zero_epsilon: float = DEFAULT_POSITION_ZERO_EPSILON,
) -> str:
    normalized_size = normalize_position_size(position_size)
    if normalized_size is None or normalized_size <= max(float(zero_epsilon), 0.0):
        return POSITION_TRUTH_ZERO

    actionable_floor = normalize_market_min_order_size(
        market_min_order_size,
        default=fallback_actionable_min_order_size,
    )
    if actionable_floor is None:
        actionable_floor = max(float(fallback_actionable_min_order_size), zero_epsilon)

    if normalized_size + max(float(zero_epsilon), 0.0) < actionable_floor:
        return POSITION_TRUTH_DUST
    return POSITION_TRUTH_ACTIONABLE


def is_position_truth_terminal(position_truth: str) -> bool:
    return position_truth in {POSITION_TRUTH_ZERO, POSITION_TRUTH_DUST}


def is_position_truth_actionable(position_truth: str) -> bool:
    return position_truth == POSITION_TRUTH_ACTIONABLE


def classify_row_position_truth(
    row: Optional[Dict[str, Any]],
    *,
    size: Any = None,
    default_market_min_order_size: float = DEFAULT_ACTIONABLE_MIN_ORDER_SIZE,
    zero_epsilon: float = DEFAULT_POSITION_ZERO_EPSILON,
) -> str:
    row_payload = row if isinstance(row, dict) else {}
    position_size = size
    if position_size is None and row_payload:
        position_size = row_payload.get("size")
    market_min_order_size = extract_market_min_order_size(
        row_payload,
        default=default_market_min_order_size,
    )
    return classify_position_truth(
        position_size,
        market_min_order_size=market_min_order_size,
        fallback_actionable_min_order_size=default_market_min_order_size,
        zero_epsilon=zero_epsilon,
    )
