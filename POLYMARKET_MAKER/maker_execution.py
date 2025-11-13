# -*- coding: utf-8 -*-
"""Maker-only execution helpers for Polymarket trading.

This module provides two high-level routines used by the volatility arbitrage
script:

``maker_buy_follow_bid``
    Place a GTC buy order at the current best bid and keep adjusting the order
    upward whenever the market bid rises. The routine polls every ``poll_sec``
    seconds, accumulates fills, and exits once the requested quantity is filled
    (or the remainder falls below the minimum notional requirement).

``maker_sell_follow_ask_with_floor_wait``
    Place a GTC sell order at ``max(best_ask, floor_X)`` and follow the ask
    downward without crossing below the provided floor price. If the ask drops
    below the floor the routine cancels the working order and waits until the
    market recovers above the floor before re-posting.

Both helpers favour websocket snapshots supplied by the caller via
``best_bid_fn`` / ``best_ask_fn``. When these callables are absent or return
``None`` the helpers fall back to best-effort REST lookups using the provided
client.

The functions return lightweight dictionaries that summarise order history and
fill statistics so that the strategy layer can update its internal state.
"""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Dict, List, Optional, Tuple

from trading.execution import ClobPolymarketAPI


BUY_PRICE_DP = 2
BUY_SIZE_DP = 4
SELL_PRICE_DP = 4
SELL_SIZE_DP = 2
_MIN_FILL_EPS = 1e-9
DEFAULT_MIN_ORDER_SIZE = 5.0


def _round_up_to_dp(value: float, dp: int) -> float:
    factor = 10 ** dp
    return math.ceil(value * factor - 1e-12) / factor


def _round_down_to_dp(value: float, dp: int) -> float:
    factor = 10 ** dp
    return math.floor(value * factor + 1e-12) / factor


def _ceil_to_dp(value: float, dp: int) -> float:
    factor = 10 ** dp
    return math.ceil(value * factor - 1e-12) / factor


def _floor_to_dp(value: float, dp: int) -> float:
    factor = 10 ** dp
    return math.floor(value * factor + 1e-12) / factor


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _extract_best_price(payload: Any, side: str) -> Optional[float]:
    numeric = _coerce_float(payload)
    if numeric is not None:
        return numeric

    if isinstance(payload, Mapping):
        primary_keys = {
            "bid": (
                "best_bid",
                "bestBid",
                "bid",
                "highestBid",
                "bestBidPrice",
                "bidPrice",
                "buy",
            ),
            "ask": (
                "best_ask",
                "bestAsk",
                "ask",
                "offer",
                "best_offer",
                "bestOffer",
                "lowestAsk",
                "sell",
            ),
        }[side]
        for key in primary_keys:
            if key in payload:
                extracted = _extract_best_price(payload[key], side)
                if extracted is not None:
                    return extracted

        ladder_keys = {
            "bid": ("bids", "bid_levels", "buy_orders", "buyOrders"),
            "ask": ("asks", "ask_levels", "sell_orders", "sellOrders", "offers"),
        }[side]
        for key in ladder_keys:
            if key in payload:
                ladder = payload[key]
                if isinstance(ladder, Iterable) and not isinstance(ladder, (str, bytes, bytearray)):
                    for entry in ladder:
                        if isinstance(entry, Mapping) and "price" in entry:
                            candidate = _coerce_float(entry.get("price"))
                            if candidate is not None:
                                return candidate
                        extracted = _extract_best_price(entry, side)
                        if extracted is not None:
                            return extracted

        for value in payload.values():
            extracted = _extract_best_price(value, side)
            if extracted is not None:
                return extracted
        return None

    if isinstance(payload, Iterable) and not isinstance(payload, (str, bytes, bytearray)):
        for item in payload:
            extracted = _extract_best_price(item, side)
            if extracted is not None:
                return extracted
        return None

    return None


def _fetch_best_price(client: Any, token_id: str, side: str) -> Optional[float]:
    method_candidates = (
        ("get_market_orderbook", {"market": token_id}),
        ("get_market_orderbook", {"token_id": token_id}),
        ("get_market_orderbook", {"market_id": token_id}),
        ("get_order_book", {"market": token_id}),
        ("get_order_book", {"token_id": token_id}),
        ("get_orderbook", {"market": token_id}),
        ("get_orderbook", {"token_id": token_id}),
        ("get_market", {"market": token_id}),
        ("get_market", {"token_id": token_id}),
        ("get_market_data", {"market": token_id}),
        ("get_market_data", {"token_id": token_id}),
        ("get_ticker", {"market": token_id}),
        ("get_ticker", {"token_id": token_id}),
    )

    for name, kwargs in method_candidates:
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            resp = fn(**kwargs)
        except TypeError:
            continue
        except Exception:
            continue

        payload = resp
        if isinstance(resp, tuple) and len(resp) == 2:
            payload = resp[1]
        if isinstance(payload, Mapping) and {"data", "status"} <= set(payload.keys()):
            payload = payload.get("data")

        best = _extract_best_price(payload, side)
        if best is not None:
            return float(best)
    return None


def _best_bid(client: Any, token_id: str, best_bid_fn: Optional[Callable[[], Optional[float]]]) -> Optional[float]:
    if best_bid_fn is not None:
        try:
            val = best_bid_fn()
        except Exception:
            val = None
        if val is not None and val > 0:
            return float(val)
    return _fetch_best_price(client, token_id, "bid")


def _best_ask(client: Any, token_id: str, best_ask_fn: Optional[Callable[[], Optional[float]]]) -> Optional[float]:
    if best_ask_fn is not None:
        try:
            val = best_ask_fn()
        except Exception:
            val = None
        if val is not None and val > 0:
            return float(val)
    return _fetch_best_price(client, token_id, "ask")


def _cancel_order(client: Any, order_id: Optional[str]) -> bool:
    if not order_id:
        return False
    method_names = (
        "cancel_order",
        "cancelOrder",
        "cancel",
        "cancel_orders",
        "cancelOrders",
        "delete_order",
        "deleteOrder",
        "cancel_limit_order",
        "cancelLimitOrder",
        "cancel_open_order",
        "cancelOpenOrder",
    )

    targets: deque[Any] = deque([client])
    visited: set[int] = set()
    while targets:
        obj = targets.popleft()
        if obj is None:
            continue
        obj_id = id(obj)
        if obj_id in visited:
            continue
        visited.add(obj_id)
        for name in method_names:
            method = getattr(obj, name, None)
            if not callable(method):
                continue
            try:
                method(order_id)
                return True
            except TypeError:
                try:
                    method(id=order_id)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
        for attr in ("client", "api", "private"):
            nested = getattr(obj, attr, None)
            if nested is not None:
                targets.append(nested)
    return False


def _order_tick(dp: int) -> float:
    return 10 ** (-dp)


def _update_fill_totals(
    order_id: str,
    status_payload: Dict[str, Any],
    accounted: Dict[str, float],
    notional_sum: float,
    last_known_price: float,
    *,
    status_text: Optional[str] = None,
    expected_full_size: Optional[float] = None,
) -> Tuple[float, float, float]:
    filled_amount = float(status_payload.get("filledAmount", 0.0) or 0.0)
    avg_price = status_payload.get("avgPrice")
    if avg_price is None:
        avg_price = last_known_price
    else:
        avg_price = float(avg_price)

    if filled_amount <= _MIN_FILL_EPS and status_text:
        status_upper = status_text.upper()
        if status_upper in {"FILLED", "MATCHED", "COMPLETED", "EXECUTED"}:
            if expected_full_size is not None and expected_full_size > 0:
                filled_amount = max(filled_amount, float(expected_full_size))

    previous = accounted.get(order_id, 0.0)
    delta = max(filled_amount - previous, 0.0)
    accounted[order_id] = filled_amount
    notional_sum += delta * avg_price
    return filled_amount, avg_price, notional_sum


def maker_buy_follow_bid(
    client: Any,
    token_id: str,
    target_size: float,
    *,
    poll_sec: float = 10.0,
    min_quote_amt: float = 1.0,
    min_order_size: float = DEFAULT_MIN_ORDER_SIZE,
    best_bid_fn: Optional[Callable[[], Optional[float]]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    progress_probe: Optional[Callable[[], None]] = None,
    progress_probe_interval: float = 60.0,
) -> Dict[str, Any]:
    """Continuously maintain a maker buy order following the market bid."""

    goal_size = max(_ceil_to_dp(float(target_size), BUY_SIZE_DP), 0.0)
    api_min_qty = 0.0
    if min_order_size and min_order_size > 0:
        api_min_qty = _ceil_to_dp(float(min_order_size), BUY_SIZE_DP)
        goal_size = max(goal_size, api_min_qty)
    if goal_size <= 0:
        return {
            "status": "SKIPPED",
            "avg_price": None,
            "filled": 0.0,
            "remaining": 0.0,
            "orders": [],
        }

    adapter = ClobPolymarketAPI(client)
    orders: List[Dict[str, Any]] = []
    records: Dict[str, Dict[str, Any]] = {}
    accounted: Dict[str, float] = {}

    remaining = goal_size
    filled_total = 0.0
    notional_sum = 0.0

    active_order: Optional[str] = None
    active_price: Optional[float] = None

    final_status = "PENDING"
    tick = _order_tick(BUY_PRICE_DP)

    next_probe_at = 0.0

    while True:
        if stop_check and stop_check():
            if active_order:
                _cancel_order(client, active_order)
                rec = records.get(active_order)
                if rec is not None:
                    rec["status"] = "CANCELLED"
            final_status = "STOPPED"
            break

        if active_order is None:
            if api_min_qty and remaining + _MIN_FILL_EPS < api_min_qty:
                final_status = "FILLED_TRUNCATED" if filled_total > _MIN_FILL_EPS else "SKIPPED_TOO_SMALL"
                break
            bid = _best_bid(client, token_id, best_bid_fn)
            if bid is None or bid <= 0:
                sleep_fn(poll_sec)
                continue
            px = _round_up_to_dp(bid, BUY_PRICE_DP)
            if px <= 0:
                sleep_fn(poll_sec)
                continue
            min_qty = 0.0
            if min_quote_amt and min_quote_amt > 0:
                min_qty = _ceil_to_dp(min_quote_amt / max(px, 1e-9), BUY_SIZE_DP)
            eff_qty = max(remaining, min_qty)
            if api_min_qty:
                eff_qty = max(eff_qty, api_min_qty)
            eff_qty = _ceil_to_dp(eff_qty, BUY_SIZE_DP)
            if eff_qty <= 0:
                final_status = "SKIPPED"
                break
            payload = {
                "tokenId": token_id,
                "side": "BUY",
                "price": px,
                "size": eff_qty,
                "timeInForce": "GTC",
                "type": "GTC",
                "allowPartial": True,
            }
            response = adapter.create_order(payload)
            order_id = str(response.get("orderId"))
            record = {
                "id": order_id,
                "side": "buy",
                "price": px,
                "size": eff_qty,
                "status": "OPEN",
                "filled": 0.0,
            }
            orders.append(record)
            records[order_id] = record
            accounted[order_id] = 0.0
            active_order = order_id
            active_price = px
            if progress_probe:
                interval = max(progress_probe_interval, poll_sec, 1e-6)
                try:
                    progress_probe()
                except Exception as probe_exc:
                    print(f"[MAKER][BUY] 进度探针执行异常：{probe_exc}")
                next_probe_at = time.time() + interval
            print(
                f"[MAKER][BUY] 挂单 -> price={px:.{BUY_PRICE_DP}f} qty={eff_qty:.{BUY_SIZE_DP}f} remaining={remaining:.{BUY_SIZE_DP}f}"
            )
            continue

        sleep_fn(poll_sec)
        if (
            progress_probe
            and active_order
            and progress_probe_interval > 0
            and time.time() >= max(next_probe_at, 0.0)
        ):
            try:
                progress_probe()
            except Exception as probe_exc:
                print(f"[MAKER][BUY] 进度探针执行异常：{probe_exc}")
            interval = max(progress_probe_interval, poll_sec, 1e-6)
            next_probe_at = time.time() + interval
        try:
            status_payload = adapter.get_order_status(active_order)
        except Exception as exc:
            print(f"[MAKER][BUY] 查询订单状态异常：{exc}")
            status_payload = {"status": "UNKNOWN", "filledAmount": accounted.get(active_order, 0.0)}

        record = records.get(active_order)
        status_text = str(status_payload.get("status", "UNKNOWN"))
        record_size = None
        if record is not None:
            try:
                record_size = float(record.get("size", 0.0) or 0.0)
            except Exception:
                record_size = None
        last_price_hint = active_price
        if last_price_hint is None:
            last_price_hint = _coerce_float(status_payload.get("avgPrice"))
        if last_price_hint is None:
            last_price_hint = 0.0
        filled_amount, avg_price, notional_sum = _update_fill_totals(
            active_order,
            status_payload,
            accounted,
            notional_sum,
            float(last_price_hint),
            status_text=status_text,
            expected_full_size=record_size,
        )
        filled_total = sum(accounted.values())
        remaining = max(goal_size - filled_total, 0.0)
        status_text_upper = status_text.upper()
        if record is not None:
            record["filled"] = filled_amount
            record["status"] = status_text_upper
            if avg_price is not None:
                record["avg_price"] = avg_price
            price_display = record.get("price", active_price)
            total_size = float(record.get("size", 0.0) or 0.0)
            remaining_slice = max(total_size - filled_amount, 0.0)
            if price_display is not None:
                print(
                    f"[MAKER][BUY] 挂单状态 -> price={float(price_display):.{BUY_PRICE_DP}f} "
                    f"filled={filled_amount:.{BUY_SIZE_DP}f} remaining={remaining_slice:.{BUY_SIZE_DP}f} "
                    f"status={status_text_upper}"
                )

        current_bid = _best_bid(client, token_id, best_bid_fn)
        min_buyable = 0.0
        if min_quote_amt and min_quote_amt > 0 and current_bid and current_bid > 0:
            min_buyable = _ceil_to_dp(min_quote_amt / max(current_bid, 1e-9), BUY_SIZE_DP)
        if api_min_qty:
            min_buyable = max(min_buyable, api_min_qty)

        if remaining <= _MIN_FILL_EPS or (min_buyable and remaining < min_buyable):
            if active_order:
                _cancel_order(client, active_order)
                rec = records.get(active_order)
                if rec is not None:
                    rec["status"] = "CANCELLED"
                active_order = None
            if remaining <= _MIN_FILL_EPS:
                final_status = "FILLED"
            else:
                final_status = "FILLED_TRUNCATED" if filled_total > _MIN_FILL_EPS else "SKIPPED_TOO_SMALL"
            break

        if current_bid is not None and active_price is not None and current_bid >= active_price + tick - 1e-12:
            print(
                f"[MAKER][BUY] 买一上行 -> 撤单重挂 | old={active_price:.{BUY_PRICE_DP}f} new={current_bid:.{BUY_PRICE_DP}f}"
            )
            _cancel_order(client, active_order)
            rec = records.get(active_order)
            if rec is not None:
                rec["status"] = "CANCELLED"
            active_order = None
            active_price = None
            continue

        final_states = {"FILLED", "MATCHED", "COMPLETED", "EXECUTED"}
        cancel_states = {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
        if status_text_upper in final_states:
            active_order = None
            active_price = None
            continue
        if status_text_upper in cancel_states:
            active_order = None
            active_price = None
            continue

    avg_price = notional_sum / filled_total if filled_total > 0 else None
    remaining = max(goal_size - filled_total, 0.0)
    return {
        "status": final_status,
        "avg_price": avg_price,
        "filled": filled_total,
        "remaining": remaining,
        "orders": orders,
    }


def maker_sell_follow_ask_with_floor_wait(
    client: Any,
    token_id: str,
    position_size: float,
    floor_X: float,
    *,
    poll_sec: float = 10.0,
    min_order_size: float = DEFAULT_MIN_ORDER_SIZE,
    best_ask_fn: Optional[Callable[[], Optional[float]]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    sell_mode: str = "conservative",
    aggressive_step: float = 0.01,
    aggressive_timeout: float = 120.0,
) -> Dict[str, Any]:
    """Maintain a maker sell order while respecting a profit floor."""

    goal_size = max(_floor_to_dp(float(position_size), SELL_SIZE_DP), 0.0)
    api_min_qty = 0.0
    if min_order_size and min_order_size > 0:
        api_min_qty = _ceil_to_dp(float(min_order_size), SELL_SIZE_DP)
    if goal_size < 0.01:
        return {
            "status": "SKIPPED",
            "avg_price": None,
            "filled": 0.0,
            "remaining": 0.0,
            "orders": [],
        }

    adapter = ClobPolymarketAPI(client)
    orders: List[Dict[str, Any]] = []
    records: Dict[str, Dict[str, Any]] = {}
    accounted: Dict[str, float] = {}

    remaining = goal_size
    filled_total = 0.0
    notional_sum = 0.0

    active_order: Optional[str] = None
    active_price: Optional[float] = None
    waiting_for_floor = False

    final_status = "PENDING"
    tick = _order_tick(SELL_PRICE_DP)

    aggressive_mode = str(sell_mode).lower() == "aggressive"
    aggressive_timer_start: Optional[float] = None
    aggressive_floor_locked = False
    aggressive_next_price_override: Optional[float] = None
    aggressive_locked_price: Optional[float] = None
    try:
        aggressive_timeout = float(aggressive_timeout)
    except (TypeError, ValueError):
        aggressive_timeout = 120.0
    try:
        aggressive_step = float(aggressive_step)
    except (TypeError, ValueError):
        aggressive_step = 0.01
    if aggressive_step <= 0:
        aggressive_mode = False
    floor_float = float(floor_X)

    while True:
        if stop_check and stop_check():
            if active_order:
                _cancel_order(client, active_order)
                rec = records.get(active_order)
                if rec is not None:
                    rec["status"] = "CANCELLED"
                aggressive_timer_start = None
            final_status = "STOPPED"
            break

        if api_min_qty and remaining + _MIN_FILL_EPS < api_min_qty:
            final_status = "FILLED_TRUNCATED" if filled_total > _MIN_FILL_EPS else "SKIPPED_TOO_SMALL"
            break

        ask = _best_ask(client, token_id, best_ask_fn)
        if ask is None or ask <= 0:
            waiting_for_floor = True
            if active_order:
                _cancel_order(client, active_order)
                rec = records.get(active_order)
                if rec is not None:
                    rec["status"] = "CANCELLED"
                active_order = None
                active_price = None
                aggressive_timer_start = None
                aggressive_next_price_override = None
            sleep_fn(poll_sec)
            continue

        if ask < floor_X - 1e-12:
            if not waiting_for_floor:
                print(
                    f"[MAKER][SELL] 卖一跌破地板，撤单等待 | ask={ask:.{SELL_PRICE_DP}f} floor={floor_X:.{SELL_PRICE_DP}f}"
                )
            waiting_for_floor = True
            if active_order:
                _cancel_order(client, active_order)
                rec = records.get(active_order)
                if rec is not None:
                    rec["status"] = "CANCELLED"
                active_order = None
                active_price = None
                aggressive_timer_start = None
                aggressive_next_price_override = None
            sleep_fn(poll_sec)
            continue

        if waiting_for_floor and ask >= floor_X:
            waiting_for_floor = False

        if active_order is None:
            px_candidate = max(_round_down_to_dp(ask, SELL_PRICE_DP), floor_float)
            if aggressive_mode:
                if aggressive_next_price_override is not None:
                    px_candidate = max(
                        _round_down_to_dp(aggressive_next_price_override, SELL_PRICE_DP),
                        floor_float,
                    )
                    aggressive_next_price_override = None
                elif aggressive_locked_price is not None:
                    px_candidate = max(
                        _round_down_to_dp(aggressive_locked_price, SELL_PRICE_DP),
                        floor_float,
                    )
            else:
                aggressive_next_price_override = None
            px = px_candidate
            qty = _floor_to_dp(remaining, SELL_SIZE_DP)
            if qty < 0.01:
                final_status = "FILLED"
                break
            if api_min_qty and qty + _MIN_FILL_EPS < api_min_qty:
                final_status = "FILLED_TRUNCATED" if filled_total > _MIN_FILL_EPS else "SKIPPED_TOO_SMALL"
                break
            payload = {
                "tokenId": token_id,
                "side": "SELL",
                "price": px,
                "size": qty,
                "timeInForce": "GTC",
                "type": "GTC",
                "allowPartial": True,
            }
            try:
                response = adapter.create_order(payload)
            except Exception as exc:
                msg = str(exc).lower()
                insufficient = any(
                    keyword in msg for keyword in ("insufficient", "balance", "position")
                )
                if insufficient:
                    current_remaining = max(goal_size - filled_total, 0.0)
                    shrink_qty = _floor_to_dp(max(current_remaining - tick, 0.0), SELL_SIZE_DP)
                    if shrink_qty >= 0.01 and (
                        not api_min_qty or shrink_qty + _MIN_FILL_EPS >= api_min_qty
                    ):
                        print(
                            "[MAKER][SELL] 可用仓位不足，调整卖出数量后重试 -> "
                            f"old={qty:.{SELL_SIZE_DP}f} new={shrink_qty:.{SELL_SIZE_DP}f}"
                        )
                        goal_size = filled_total + shrink_qty
                        remaining = max(goal_size - filled_total, 0.0)
                        continue
                    final_status = (
                        "FILLED_TRUNCATED" if filled_total > _MIN_FILL_EPS else "SKIPPED_TOO_SMALL"
                    )
                    remaining = max(goal_size - filled_total, 0.0)
                    print(
                        "[MAKER][SELL] 可用仓位低于最小挂单量，放弃后续卖出尝试。"
                    )
                    break
                raise
            order_id = str(response.get("orderId"))
            record = {
                "id": order_id,
                "side": "sell",
                "price": px,
                "size": qty,
                "status": "OPEN",
                "filled": 0.0,
            }
            orders.append(record)
            records[order_id] = record
            accounted[order_id] = 0.0
            active_order = order_id
            active_price = px
            if aggressive_mode:
                if px <= floor_float + 1e-12:
                    aggressive_locked_price = px
                if aggressive_locked_price is not None:
                    aggressive_floor_locked = True
                    aggressive_timer_start = None
                else:
                    aggressive_floor_locked = False
                    aggressive_timer_start = time.time()
            print(
                f"[MAKER][SELL] 挂单 -> price={px:.{SELL_PRICE_DP}f} qty={qty:.{SELL_SIZE_DP}f} remaining={remaining:.{SELL_SIZE_DP}f}"
            )
            continue

        sleep_fn(poll_sec)
        try:
            status_payload = adapter.get_order_status(active_order)
        except Exception as exc:
            print(f"[MAKER][SELL] 查询订单状态异常：{exc}")
            status_payload = {"status": "UNKNOWN", "filledAmount": accounted.get(active_order, 0.0)}

        record = records.get(active_order)
        status_text = str(status_payload.get("status", "UNKNOWN"))
        record_size = None
        if record is not None:
            try:
                record_size = float(record.get("size", 0.0) or 0.0)
            except Exception:
                record_size = None
        last_price_hint = active_price
        if last_price_hint is None:
            last_price_hint = _coerce_float(status_payload.get("avgPrice"))
        if last_price_hint is None:
            last_price_hint = floor_X
        filled_amount, avg_price, notional_sum = _update_fill_totals(
            active_order,
            status_payload,
            accounted,
            notional_sum,
            float(last_price_hint),
            status_text=status_text,
            expected_full_size=record_size,
        )
        filled_total = sum(accounted.values())
        remaining = max(goal_size - filled_total, 0.0)
        status_text_upper = status_text.upper()
        if record is not None:
            record["filled"] = filled_amount
            record["status"] = status_text_upper
            if avg_price is not None:
                record["avg_price"] = avg_price
            price_display = record.get("price", active_price)
            total_size = float(record.get("size", 0.0) or 0.0)
            remaining_slice = max(total_size - filled_amount, 0.0)
            if price_display is not None:
                print(
                    f"[MAKER][SELL] 挂单状态 -> price={float(price_display):.{SELL_PRICE_DP}f} "
                    f"sold={filled_amount:.{SELL_SIZE_DP}f} remaining={remaining_slice:.{SELL_SIZE_DP}f} "
                    f"status={status_text_upper}"
                )

        if api_min_qty and remaining < api_min_qty:
            if active_order:
                _cancel_order(client, active_order)
                rec = records.get(active_order)
                if rec is not None:
                    rec["status"] = "CANCELLED"
                active_order = None
                active_price = None
                aggressive_timer_start = None
                aggressive_next_price_override = None
            final_status = "FILLED_TRUNCATED" if filled_total > _MIN_FILL_EPS else "SKIPPED_TOO_SMALL"
            break

        if remaining <= 0.0 or _floor_to_dp(remaining, SELL_SIZE_DP) < 0.01:
            if active_order:
                _cancel_order(client, active_order)
                rec = records.get(active_order)
                if rec is not None:
                    rec["status"] = "CANCELLED"
                active_order = None
                aggressive_timer_start = None
                aggressive_next_price_override = None
            final_status = "FILLED"
            break

        ask = _best_ask(client, token_id, best_ask_fn)
        if ask is None:
            continue

        if ask < floor_X - 1e-12:
            print(
                f"[MAKER][SELL] 卖一再次跌破地板，撤单等待 | ask={ask:.{SELL_PRICE_DP}f} floor={floor_X:.{SELL_PRICE_DP}f}"
            )
            _cancel_order(client, active_order)
            rec = records.get(active_order)
            if rec is not None:
                rec["status"] = "CANCELLED"
            active_order = None
            active_price = None
            waiting_for_floor = True
            aggressive_timer_start = None
            aggressive_next_price_override = None
            continue

        if aggressive_mode and active_order and not waiting_for_floor:
            if active_price is not None and active_price <= floor_float + 1e-12:
                aggressive_locked_price = active_price
                aggressive_floor_locked = True
                aggressive_timer_start = None
            if not aggressive_floor_locked and active_price is not None:
                if aggressive_timer_start is None:
                    aggressive_timer_start = time.time()
                elapsed = time.time() - aggressive_timer_start
                if elapsed >= aggressive_timeout:
                    target_price = active_price - aggressive_step
                    if target_price >= floor_float - 1e-12:
                        next_px = max(
                            _round_down_to_dp(target_price, SELL_PRICE_DP),
                            floor_float,
                        )
                        if next_px >= active_price - 1e-12:
                            aggressive_timer_start = time.time()
                            if next_px <= floor_float + 1e-12:
                                aggressive_locked_price = next_px
                                aggressive_floor_locked = True
                                aggressive_timer_start = None
                        else:
                            print(
                                "[MAKER][SELL][激进] 挂单超时未成交，下调挂价 -> "
                                f"old={active_price:.{SELL_PRICE_DP}f} new={next_px:.{SELL_PRICE_DP}f}"
                            )
                            _cancel_order(client, active_order)
                            rec = records.get(active_order)
                            if rec is not None:
                                rec["status"] = "CANCELLED"
                            active_order = None
                            active_price = None
                            aggressive_next_price_override = next_px
                            aggressive_timer_start = None
                            if next_px <= floor_float + 1e-12:
                                aggressive_locked_price = next_px
                                aggressive_floor_locked = True
                            continue
                    else:
                        aggressive_locked_price = active_price
                        aggressive_floor_locked = True
                        aggressive_timer_start = None

        if active_price is not None and ask <= active_price - tick - 1e-12:
            new_px = max(_round_down_to_dp(ask, SELL_PRICE_DP), float(floor_X))
            print(
                f"[MAKER][SELL] 卖一下行 -> 撤单重挂 | old={active_price:.{SELL_PRICE_DP}f} new={new_px:.{SELL_PRICE_DP}f}"
            )
            _cancel_order(client, active_order)
            rec = records.get(active_order)
            if rec is not None:
                rec["status"] = "CANCELLED"
            active_order = None
            active_price = None
            aggressive_timer_start = None
            aggressive_next_price_override = None
            continue

        final_states = {"FILLED", "MATCHED", "COMPLETED", "EXECUTED"}
        cancel_states = {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
        if status_text_upper in final_states:
            active_order = None
            active_price = None
            aggressive_timer_start = None
            aggressive_next_price_override = None
            continue
        if status_text_upper in cancel_states:
            active_order = None
            active_price = None
            aggressive_timer_start = None
            aggressive_next_price_override = None
            continue

    avg_price = notional_sum / filled_total if filled_total > 0 else None
    remaining = max(goal_size - filled_total, 0.0)
    return {
        "status": final_status,
        "avg_price": avg_price,
        "filled": filled_total,
        "remaining": remaining,
        "orders": orders,
    }
