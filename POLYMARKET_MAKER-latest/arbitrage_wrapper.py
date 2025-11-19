"""轻量封装模块：将交互式 `Volatility_arbitrage_run.main` 改造成可编程调用。

核心约束：
- 底层策略逻辑完全参数驱动，不再依赖 input()；
- CLI 入口负责交互式提问；
- 自动化 / FastAPI 入口通过 JSON 传参调用此处的 run_arbitrage。
"""
from __future__ import annotations

from typing import Optional

try:
    from Volatility_arbitrage_run import (
        ArbitrageParams,
        _fetch_market_by_slug,
        _list_markets_under_event,
        _market_meta_from_obj,
        _resolve_with_fallback,
        main as _run_main,
        resolve_token_ids,
    )
except Exception as exc:  # pragma: no cover - 初始化即失败时直接抛错
    raise RuntimeError(f"无法导入套利主脚本：{exc}")


def run_arbitrage(
    market_url: Optional[str] = None,
    direction: str = "YES",
    size: Optional[float] = None,
    *,
    subquestion_choice: Optional[str | int] = None,
    manual_size_is_target: bool = True,
    sell_mode: str = "aggressive",
    buy_price_threshold: Optional[float] = None,
    drop_window_minutes: Optional[float] = 10.0,
    drop_pct: Optional[float] = 0.05,
    profit_pct: Optional[float] = 0.05,
    enable_incremental_drop_pct: bool = True,
    countdown: Optional[str | float | int] = None,
    countdown_minutes_before: Optional[str | float | int] = None,
    countdown_absolute_ts: Optional[str | float | int] = None,
    timezone_override: Optional[str] = None,
    deadline_option: Optional[str | int] = None,
    market_source: Optional[str] = None,
) -> None:
    """以编程方式运行套利脚本，参数映射自原交互式问题。"""

    resolved_market_source = market_url or market_source
    if not resolved_market_source:
        raise ValueError("必须提供 market_url 或 market_source")

    params = ArbitrageParams(
        market_source=resolved_market_source,
        market_url=market_url,
        subquestion_choice=subquestion_choice,
        direction=direction,
        size=size,
        manual_size_is_target=manual_size_is_target,
        sell_mode=sell_mode,
        buy_price_threshold=buy_price_threshold,
        drop_window_minutes=drop_window_minutes if drop_window_minutes is not None else 10.0,
        drop_pct=drop_pct if drop_pct is not None else 0.05,
        profit_pct=profit_pct if profit_pct is not None else 0.05,
        enable_incremental_drop_pct=enable_incremental_drop_pct,
        countdown=countdown,
        countdown_minutes_before=countdown_minutes_before,
        countdown_absolute_ts=countdown_absolute_ts,
        timezone_override=timezone_override,
        deadline_option=deadline_option,
    )

    _run_main(params, interactive=False)


__all__ = ["run_arbitrage", "ArbitrageParams"]
