"""轻量封装模块：将交互式 `Volatility_arbitrage_run.main` 改造成可编程调用。

用法示例：
    from arbitrage_wrapper import run_arbitrage

    run_arbitrage(
        market_url="https://polymarket.com/market/<slug>",
        direction="YES",           # 或 "NO"
        size=10,                    # 可选：买入份数；留空使用脚本默认按 $1 反推
        buy_price_threshold=0.35,   # 可选：买入触发价
        drop_window_minutes=10,
        drop_pct=0.05,
        profit_pct=0.05,
    )

思路：
- 预先构造脚本期望的输入序列，并用 mock.patch 注入到内置 input。
- 若未提供的参数则退回脚本默认值，保持与原交互式流程一致。
- 脚本启动后的 stop/exit 监听线程会在输入耗尽时遇到 EOF 并自动退出。
"""
from __future__ import annotations

from typing import Iterable, List, Optional
from unittest.mock import patch


try:
    from Volatility_arbitrage_run import (
        _apply_timezone_override_meta,
        _resolve_with_fallback,
        _should_offer_common_deadline_options,
        main as _run_main,
    )
except Exception as exc:  # pragma: no cover - 初始化即失败时直接抛错
    raise RuntimeError(f"无法导入套利主脚本：{exc}")


def _calc_deadline_from_meta(meta: Optional[dict]) -> Optional[float]:
    """仿照脚本内部逻辑，提取 end_ts/resolved_ts 的最早时间。"""

    if not isinstance(meta, dict):
        return None
    candidates: List[float] = []
    for key in ("end_ts", "resolved_ts"):
        ts_val = meta.get(key)
        if isinstance(ts_val, (int, float)):
            candidates.append(float(ts_val))
    return min(candidates) if candidates else None


class _InputFeeder:
    """按序返回预设输入，耗尽后抛 EOF 以终结监听线程。"""

    def __init__(self, values: Iterable[str]):
        self._values = list(values)
        self._idx = 0

    def __call__(self, prompt: str = "") -> str:
        if self._idx < len(self._values):
            val = self._values[self._idx]
            self._idx += 1
            return str(val)
        raise EOFError("arbitrage_wrapper: no more scripted input")


def _prepare_deadline_prompt(meta: dict, deadline_option: Optional[str]) -> tuple[bool, bool]:
    """返回 (是否需要截止时间输入, 是否因选项禁用截止时间)。"""

    prompt_deadline = False
    manual_deadline_disabled = False

    deadline_ts = _calc_deadline_from_meta(meta)
    if deadline_ts and meta.get("end_ts_precise"):
        prompt_deadline = True
    elif _should_offer_common_deadline_options(meta):
        prompt_deadline = True

    option_text = "" if deadline_option is None else str(deadline_option).strip()
    if prompt_deadline and option_text == "4":
        manual_deadline_disabled = True
    elif deadline_ts is None:
        manual_deadline_disabled = True

    return prompt_deadline, manual_deadline_disabled


def run_arbitrage(
    market_url: Optional[str] = None,
    direction: str = "YES",
    size: Optional[float] = None,
    *,
    manual_size_is_target: bool = True,
    sell_mode: str = "aggressive",
    buy_price_threshold: Optional[float] = None,
    drop_window_minutes: Optional[float] = 10.0,
    drop_pct: Optional[float] = 0.05,
    profit_pct: Optional[float] = 0.05,
    enable_incremental_drop_pct: bool = True,
    countdown: Optional[str | float | int] = None,
    timezone_override: Optional[str] = None,
    deadline_option: Optional[str | int] = None,
    market_source: Optional[str] = None,
) -> None:
    """以编程方式运行套利脚本，参数映射自原交互式问题。"""

    resolved_market_url = market_url or market_source
    if not resolved_market_url:
        raise ValueError("必须提供 market_url 或 market_source")

    yes_id, no_id, _title, market_meta = _resolve_with_fallback(resolved_market_url)
    market_meta = market_meta or {}
    market_meta = _apply_timezone_override_meta(market_meta, timezone_override)

    needs_timezone_prompt = not bool(market_meta.get("timezone_hint"))
    prompt_deadline, manual_deadline_disabled = _prepare_deadline_prompt(
        market_meta,
        deadline_option=None if deadline_option is None else str(deadline_option),
    )

    inputs: List[str] = []
    inputs.append(resolved_market_url)

    if needs_timezone_prompt:
        inputs.append(timezone_override or "")

    if prompt_deadline:
        inputs.append("" if deadline_option is None else str(deadline_option))

    inputs.append("y" if str(direction).upper() == "YES" else "n")

    if size is None:
        inputs.append("")
    else:
        inputs.append(str(size))
        inputs.append("y" if manual_size_is_target else "n")

    inputs.append("2" if sell_mode.lower() == "conservative" else "1")
    inputs.append("") if buy_price_threshold is None else inputs.append(str(buy_price_threshold))
    inputs.append("") if drop_window_minutes is None else inputs.append(str(drop_window_minutes))
    inputs.append("") if drop_pct is None else inputs.append(str(float(drop_pct) * 100.0))
    inputs.append("") if profit_pct is None else inputs.append(str(float(profit_pct) * 100.0))
    inputs.append("y" if enable_incremental_drop_pct else "n")

    has_deadline = _calc_deadline_from_meta(market_meta)
    if not manual_deadline_disabled and has_deadline:
        inputs.append("" if countdown is None else str(countdown))

    feeder = _InputFeeder(inputs)

    def _resolve_stub(source: str):
        if str(source).strip() == str(resolved_market_url).strip():
            return yes_id, no_id, _title, market_meta
        return _resolve_with_fallback(source)

    with patch("builtins.input", feeder):
        with patch("Volatility_arbitrage_run._resolve_with_fallback", _resolve_stub):
            _run_main()


__all__ = ["run_arbitrage"]
