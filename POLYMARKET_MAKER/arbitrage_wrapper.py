"""Lightweight callable wrapper for the volatility arbitrage runner."""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from Volatility_arbitrage_run import RunConfig, run_with_config


def run_arbitrage(
    market_source: str,
    direction: Literal["YES", "NO"],
    size: Optional[float] = None,
    *,
    sell_mode: Literal["aggressive", "conservative"] = "aggressive",
    buy_price_threshold: Optional[float] = None,
    drop_window_minutes: float = 10.0,
    drop_pct: float = 0.05,
    profit_pct: float = 0.05,
    enable_incremental_drop_pct: bool = True,
    countdown_minutes_before: Optional[float] = None,
    countdown_absolute_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Execute the volatility arbitrage loop with explicit parameters."""

    cfg = RunConfig(
        source=market_source,
        direction=direction.upper(),
        sell_mode=sell_mode,
        size=size,
        buy_price_threshold=buy_price_threshold,
        drop_window_minutes=drop_window_minutes,
        drop_pct=drop_pct,
        profit_pct=profit_pct,
        enable_incremental_drop_pct=enable_incremental_drop_pct,
        countdown_minutes_before=countdown_minutes_before,
        countdown_absolute_ts=countdown_absolute_ts,
    )
    return run_with_config(cfg)
