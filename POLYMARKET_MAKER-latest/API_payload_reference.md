# Polymarket 套利脚本接口字段对照与标准 JSON 示例

该文档梳理了 `arbitrage_wrapper.run_arbitrage` 暴露给外部（含 FastAPI 封装）时需要的全部字段，字段默认值直接对照源码，便于在 Web 端或 Swagger 里构造请求体。

## 字段速览
- `market_source` / `market_url`：事件页或具体子问题的 URL（二选一，至少填一个）。
- `subquestion_choice`：事件页场景下选择子问题的方式，可填整数序号（从 0 开始）或直接传子问题 URL；已传具体市场 URL 可留空。
- `direction`：`"YES"` / `"NO"`，区分买入方向。
- `size`：买入份数，可留空按脚本默认以 $1 反推；`manual_size_is_target` 控制该份数是“目标持仓”还是“额外买入”。
- `sell_mode`：`"aggressive"`（默认，对应交互输入 1）或 `"conservative"`（对应 2）。
- `buy_price_threshold`：可选买入触发价，不需要可留空。
- `drop_window_minutes`：跌幅观察窗口，单位分钟，默认 10。
- `drop_pct`：跌幅触发百分比，传小数形式（例如 0.05 代表 5%）。
- `profit_pct`：盈利了结百分比，传小数形式（例如 0.05 代表 5%）。
- `enable_incremental_drop_pct`：是否随时间增加跌幅阈值，默认启用。
- `countdown` / `countdown_minutes_before` / `countdown_absolute_ts`：截止时间三选一（最早不填、不触发倒计时）。`countdown_absolute_ts` 支持秒/毫秒时间戳或 ISO 字符串。
- `timezone_override`：若市场元数据缺少时区提示，可手动指定（如 `"UTC+8"`）。
- `deadline_option`：选择通用截止时间快捷项时可传（对应交互菜单），无通用选项可留空。

以上字段与脚本交互输入的映射可参考 `arbitrage_wrapper.run_arbitrage` 的参数与输入拼装逻辑。

## 标准 JSON 示例
### 场景 A：事件 URL + 子问题序号
```json
{
  "market_source": "https://polymarket.com/event/example-event-slug",
  "subquestion_choice": 2,
  "direction": "NO",
  "size": 5,
  "manual_size_is_target": true,
  "sell_mode": "aggressive",
  "buy_price_threshold": 0.35,
  "drop_window_minutes": 10,
  "drop_pct": 0.05,
  "profit_pct": 0.05,
  "enable_incremental_drop_pct": true,
  "countdown_minutes_before": 30,
  "countdown": null,
  "countdown_absolute_ts": null,
  "timezone_override": null,
  "deadline_option": null
}
```

### 场景 B：直接传子问题 URL（无需 `subquestion_choice`）
```json
{
  "market_source": "https://polymarket.com/market/example-market-slug",
  "direction": "YES",
  "size": null,
  "manual_size_is_target": true,
  "sell_mode": "conservative",
  "buy_price_threshold": null,
  "drop_window_minutes": 5,
  "drop_pct": 0.02,
  "profit_pct": 0.03,
  "enable_incremental_drop_pct": false,
  "countdown": "2024-12-31T16:00:00Z",
  "countdown_minutes_before": null,
  "countdown_absolute_ts": null,
  "timezone_override": "UTC",
  "deadline_option": 4
}
```

> 说明：三个 countdown 字段只能保留其中一个非空，示例中已体现互斥关系；若全部为 `null`，脚本会按市场元数据决定是否需要倒计时输入。
