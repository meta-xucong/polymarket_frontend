"""轻量封装模块：将交互式 `Volatility_arbitrage_run.main` 改造成可编程调用。

用法示例：
    from arbitrage_wrapper import run_arbitrage

    run_arbitrage(
        market_url="https://polymarket.com/market/<slug>",
        direction="YES",           # 或 "NO"
        size=10,                    # 可选：买入份数；留空使用脚本默认按 $1 反推
        subquestion_choice=0,       # 可选：若传入事件页 URL，可用序号选择子问题
        buy_price_threshold=0.35,   # 可选：买入触发价
        drop_window_minutes=10,
        drop_pct=0.05,
        profit_pct=0.05,
        countdown_minutes_before=30,  # 可选：如传数字表示结束前多少分钟进入仅卖出
        countdown_absolute_ts=...,    # 可选：直接传入绝对时间戳（秒/毫秒或 ISO 字符串）
    )

思路：
- 预先构造脚本期望的输入序列，并用 mock.patch 注入到内置 input。
- 若未提供的参数则退回脚本默认值，保持与原交互式流程一致。
- 脚本启动后的 stop/exit 监听线程会在输入耗尽时遇到 EOF 并自动退出。
- 倒计时启动时间可用 countdown（绝对时间或分钟数）/ countdown_minutes_before / countdown_absolute_ts（三选一）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional
from unittest.mock import patch


try:
    from Volatility_arbitrage_run import (
        _apply_timezone_override_meta,
        _pick_market_subquestion,
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

    resolved_market_url = market_url or market_source
    if not resolved_market_url:
        raise ValueError("必须提供 market_url 或 market_source")

    auto_sub_idx: Optional[int] = None
    auto_sub_direct_url: Optional[str] = None
    if isinstance(subquestion_choice, str) and subquestion_choice.strip().startswith(
        ("http://", "https://")
    ):
        auto_sub_direct_url = subquestion_choice.strip()
    else:
        try:
            auto_sub_idx = int(subquestion_choice) if subquestion_choice is not None else None
        except (TypeError, ValueError):
            auto_sub_idx = None

    resolve_inputs: List[str] = []
    if subquestion_choice is not None:
        resolve_inputs.append(str(subquestion_choice))

    resolver = _InputFeeder(resolve_inputs)
    _orig_pick_market_subquestion = _pick_market_subquestion

    def _pick_market_subquestion_proxy(markets: list[dict]) -> dict:
        if auto_sub_direct_url:
            return {"__direct_url__": auto_sub_direct_url}
        if auto_sub_idx is not None:
            if 0 <= auto_sub_idx < len(markets):
                return markets[auto_sub_idx]
            raise ValueError(
                f"子问题序号 {auto_sub_idx} 超出范围（共有 {len(markets)} 个子问题，从 0 开始编号）"
            )
        if len(markets) == 1:
            return markets[0]
        return _orig_pick_market_subquestion(markets)

    try:
        with patch("builtins.input", resolver):
            with patch(
                "Volatility_arbitrage_run._pick_market_subquestion",
                _pick_market_subquestion_proxy,
            ):
                yes_id, no_id, _title, market_meta = _resolve_with_fallback(
                    resolved_market_url
                )
    except EOFError as exc:
        raise ValueError(
            "事件页需要选择子问题，请提供 subquestion_choice 或直接传入具体市场 URL"
        ) from exc
    market_meta = market_meta or {}
    market_meta = _apply_timezone_override_meta(market_meta, timezone_override)

    needs_timezone_prompt = not bool(market_meta.get("timezone_hint"))
    prompt_deadline, manual_deadline_disabled = _prepare_deadline_prompt(
        market_meta,
        deadline_option=None if deadline_option is None else str(deadline_option),
    )

    provided_countdown_fields = [
        value
        for value in (
            ("countdown", countdown),
            ("countdown_minutes_before", countdown_minutes_before),
            ("countdown_absolute_ts", countdown_absolute_ts),
        )
        if value[1] is not None
    ]
    if len(provided_countdown_fields) > 1:
        names = ", ".join(name for name, _ in provided_countdown_fields)
        raise ValueError(
            f"countdown 参数只能三选一（countdown / countdown_minutes_before / countdown_absolute_ts），当前同时提供: {names}"
        )

    countdown_value = countdown
    if countdown_value is None and countdown_minutes_before is not None:
        countdown_value = countdown_minutes_before
    if countdown_value is None and countdown_absolute_ts is not None:
        iso_ts = countdown_absolute_ts
        if isinstance(countdown_absolute_ts, (int, float)):
            ts_val = float(countdown_absolute_ts)
            if ts_val > 1e12:
                ts_val = ts_val / 1000.0
            iso_ts = datetime.fromtimestamp(ts_val, tz=timezone.utc).isoformat()
        countdown_value = str(iso_ts)

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
        inputs.append("" if countdown_value is None else str(countdown_value))

    feeder = _InputFeeder(inputs)

    def _resolve_stub(source: str):
        if str(source).strip() == str(resolved_market_url).strip():
            return yes_id, no_id, _title, market_meta
        return _resolve_with_fallback(source)

    with patch("builtins.input", feeder):
        with patch("Volatility_arbitrage_run._resolve_with_fallback", _resolve_stub):
            _run_main()


__all__ = ["run_arbitrage"]
