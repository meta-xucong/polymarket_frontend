from typing import Optional, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import arbitrage_wrapper


class ArbitrageRequest(BaseModel):
    market_source: Optional[str] = None
    market_url: Optional[str] = None
    subquestion_choice: Optional[Union[int, str]] = None
    direction: str = "YES"
    size: Optional[float] = None
    manual_size_is_target: bool = True
    sell_mode: str = "aggressive"
    buy_price_threshold: Optional[float] = None
    drop_window_minutes: Optional[float] = 10.0
    drop_pct: Optional[float] = 0.05
    profit_pct: Optional[float] = 0.05
    enable_incremental_drop_pct: bool = True
    countdown: Optional[Union[str, float, int]] = None
    countdown_minutes_before: Optional[Union[str, float, int]] = None
    countdown_absolute_ts: Optional[Union[str, float, int]] = None
    timezone_override: Optional[str] = None
    deadline_option: Optional[Union[str, int]] = None


app = FastAPI()


@app.post("/arbitrage/run")
async def run_arbitrage(req: ArbitrageRequest):
    try:
        arbitrage_wrapper.run_arbitrage(
            market_url=req.market_url,
            market_source=req.market_source,
            subquestion_choice=req.subquestion_choice,
            direction=req.direction,
            size=req.size,
            manual_size_is_target=req.manual_size_is_target,
            sell_mode=req.sell_mode,
            buy_price_threshold=req.buy_price_threshold,
            drop_window_minutes=req.drop_window_minutes,
            drop_pct=req.drop_pct,
            profit_pct=req.profit_pct,
            enable_incremental_drop_pct=req.enable_incremental_drop_pct,
            countdown=req.countdown,
            countdown_minutes_before=req.countdown_minutes_before,
            countdown_absolute_ts=req.countdown_absolute_ts,
            timezone_override=req.timezone_override,
            deadline_option=req.deadline_option,
        )
        return {"status": "ok"}
    except Exception as exc:  # pragma: no cover - FastAPI 返回 HTTP 400
        raise HTTPException(status_code=400, detail=str(exc))
