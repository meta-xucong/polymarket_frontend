# 配置参数文档（补全版）

本文档汇总当前仓库里核心运行配置的字段含义、格式与建议取值，覆盖：

- `POLYMARKET_MAKER/config/run_params.json`
- `POLYMARKET_MAKER/config/global_config.json`
- `POLYMARKET_MAKER/config/strategy_defaults.json`
- `POLYMARKET_MAKER/config/trading.yaml`
- `copytrade/copytrade_config.json`

---

## 1) run_params.json —— 单市场策略参数

用于 `Volatility_arbitrage_run.py`。多数“比例/百分比”字段支持两种写法：
- `0~1` 小数（如 `0.05`）
- 百分数值（如 `5`，会被解释为 `5%`）

| 字段 | 含义 | 类型/格式 | 建议 |
| --- | --- | --- | --- |
| `market_url` | 目标市场 URL 或 slug。 | 字符串 | 必填。 |
| `timezone` | 市场时区（用于时间解析/显示）。 | IANA 时区字符串 | 例如 `America/New_York`。 |
| `deadline_override_ts` | 手工覆盖市场截止时间戳。 | 秒/毫秒时间戳 | 自动识别不可靠时再用。 |
| `disable_deadline_checks` | 跳过截止时间相关检查。 | 布尔 | 仅诊断场景使用。 |
| `deadline_policy.override_choice` | 常用截止模板选择。 | `1~4` 或 `null` | 不填则走自动解析。 |
| `deadline_policy.disable_deadline` | 强制不设截止时间。 | 布尔 | 特殊场景。 |
| `deadline_policy.timezone` | 默认截止策略时区。 | IANA 时区字符串 | 与市场一致。 |
| `deadline_policy.default_deadline.time` | 默认回退时间（HH:MM / HH.MM）。 | 字符串 | 如 `12:59`。 |
| `deadline_policy.default_deadline.timezone` | 默认回退时间的时区。 | IANA 时区字符串 | 如 `America/New_York`。 |
| `side` | 下单方向。 | `YES` / `NO` | 建议显式填写。 |
| `order_size` | 下单量或目标仓位（取决于下个字段）。 | 数值 | 为空则按策略默认。 |
| `order_size_is_target` | `true`=将 `order_size` 视作目标总仓位；`false`=单笔下单量。 | 布尔 | 控总仓建议 `true`。 |
| `sell_mode` | 卖出挂单风格。 | `aggressive` / `conservative` | 默认 `aggressive`。 |
| `buy_price_threshold` | 买入价格上限阈值。 | `0~1` 浮点 | 留空则按策略逻辑。 |
| `min_buy_price` | 硬性买入价格下限（低于则不买）。 | `0~1` 浮点 | 风险控制建议 `0.05`，避免买入过低价token。 |
| `max_buy_price` | 硬性买入价格上限（超过则不买）。 | `0~1` 浮点 | 风险控制建议保留，如 `0.98`。 |
| `drop_window_minutes` | 跌幅计算窗口（分钟）。 | 浮点 | 常用 `10~120`。 |
| `drop_pct` | 触发买入的下跌比例阈值。 | 比例浮点 | 如 `0.01`（1%）。 |
| `profit_pct` | 止盈阈值。 | 比例浮点 | 如 `0.005~0.05`。 |
| `enable_incremental_drop_pct` | 卖出后是否提高下次抄底阈值。 | 布尔 | 建议与下一项配套。 |
| `incremental_drop_pct_step` | 阈值递增步长。 | 比例浮点 | 如 `0.0002`。 |
| `no_event_exit_minutes` | 启动后无行情自动退出时间（分钟）。 | 浮点 | `<=0` 表示禁用。 |
| `signal_timeout_minutes` | 信号超时窗口（分钟）；超时后信号视作失效。 | 浮点 | 常用 `10~60`。 |
| `sell_inactive_hours` | 卖出挂单长期不活跃保护阈值（小时）。 | 浮点 | `<=0` 表示禁用。 |
| `countdown.minutes_before_end` | 距离结束 N 分钟切换到“仅卖出”。 | 浮点 | 如 `60~360`。 |
| `countdown.absolute_time` | 绝对倒计时切换时间。 | 时间戳/ISO/日期文本 | 建议用带时区 ISO。 |
| `countdown.timezone` | `absolute_time` 无时区时的推断时区。 | IANA 时区字符串 | 与市场一致。 |

| `shock_guard.enabled` | 是否启用急跌门禁模块（前置于原买入逻辑）。 | 布尔 | 默认 `false`，建议先灰度。 |
| `shock_guard.shock_window_sec` | 急跌检测窗口（秒）。 | 浮点 | 常用 `20~90`。 |
| `shock_guard.shock_drop_pct` | 急跌跌幅阈值（窗口内）。 | 比例浮点 | 常用 `0.15~0.30`。 |
| `shock_guard.shock_velocity_pct_per_sec` | 急跌速度阈值（可选，负向速度）。 | 比例浮点/空 | 留空表示禁用速度条件。 |
| `shock_guard.shock_abs_floor` | 绝对低价风险阈值（可选）。 | `0~1` 浮点/空 | 如 `0.03`。 |
| `shock_guard.observation_hold_sec` | 触发急跌后的观察冻结时长（秒）。 | 浮点 | 常用 `45~180`。 |
| `shock_guard.recovery.rebound_pct_min` | 观察期后恢复确认的最小反弹比例。 | 比例浮点 | 如 `0.03~0.08`。 |
| `shock_guard.recovery.reconfirm_sec` | 最近创新低后的最小确认时长（秒）。 | 浮点 | 如 `20~120`。 |
| `shock_guard.recovery.spread_cap` | 恢复确认时允许的最大点差（可选）。 | 浮点/空 | 留空表示不校验。 |
| `shock_guard.recovery.require_conditions` | 恢复确认需满足条件数（反弹/不创新低/点差）。 | 整数 | 建议 `2`。 |
| `shock_guard.blocked_cooldown_sec` | 恢复失败后封禁买入时长（秒）。 | 浮点 | 常用 `120~600`。 |
| `shock_guard.max_pending_buy_age_sec` | 延迟买入信号最大保留时长（秒）。 | 浮点 | 常用 `60~300`。 |

### 1.1 `shock_guard`（大跌风控）参数逐项解释（按你给的示例）

```json
"shock_guard": {
  "enabled": true,
  "shock_window_sec": 180,
  "shock_drop_pct": 0.1,
  "shock_velocity_pct_per_sec": null,
  "shock_abs_floor": 0.05,
  "observation_hold_sec": 180,
  "recovery": {
    "rebound_pct_min": 0.08,
    "reconfirm_sec": 90,
    "spread_cap": 0.04,
    "require_conditions": 2
  }
}
```

- `enabled: true`：启用大跌门禁。开启后，策略在“疑似快速下杀”时会先冻结买入，而不是立即抄底。
- `shock_window_sec: 180`：用最近 180 秒作为急跌识别窗口。窗口越大，识别更稳但反应更慢。
- `shock_drop_pct: 0.1`：若窗口内价格从局部高点到低点跌幅达到 10%，判定为“shock”。
- `shock_velocity_pct_per_sec: null`：不启用“每秒跌速”这一额外条件，仅按跌幅判定。
- `shock_abs_floor: 0.05`：绝对低价地板线。价格低于/接近该区域时，会更偏向风险规避（避免在极低流动性区间接刀）。
- `observation_hold_sec: 180`：触发 shock 后，至少观察 180 秒不买入，等待波动收敛。

`recovery` 表示“观察期后是否允许恢复买入”的确认规则：

- `rebound_pct_min: 0.08`：从 shock 低点至少反弹 8%，才算有恢复迹象。
- `reconfirm_sec: 90`：即使出现反弹，也需再确认 90 秒（通常要求这段时间不再创新低）。
- `spread_cap: 0.04`：恢复买入前，盘口点差需不高于 0.04，防止在流动性差时误开仓。
- `require_conditions: 2`：恢复条件里至少满足 2 项才放行（常见是“有反弹 + 未再破低”或“有反弹 + 点差达标”）。

> 实战理解：这一组参数是“先刹车、后观察、再分条件恢复”的防抄底踩踏机制，核心目标是避免在瀑布段连续接刀。

---

## 2) global_config.json —— 调度与系统行为

用于 `poly_maker_autorun.py`。当前支持**分组写法**（推荐）和**历史平铺写法**（兼容）。

### 2.1 `scheduler` 调度参数

| 字段 | 含义 | 类型/格式 | 建议 |
| --- | --- | --- | --- |
| `max_concurrent_tasks` | 并发任务上限。 | 整数 | 根据机器资源设置。 |
| `max_exit_cleanup_tasks` | 清仓任务专用并发槽位上限。 | 整数 | 默认 3。 |
| `command_poll_seconds` | 主循环轮询间隔。 | 浮点（秒） | `1~10`。 |
| `copytrade_poll_seconds` | copytrade token 文件刷新间隔。 | 浮点（秒） | `10~60`。 |
| `sell_position_poll_interval_sec` | 卖仓位同步轮询间隔。 | 浮点（秒） | `1800~7200`。 |
| `enable_slot_refill` | 是否启用空槽位回填。 | 布尔 | 建议开启。 |
| `refill_cooldown_minutes` | 单 token 回填冷却时间。 | 浮点（分钟） | `15~60`。 |
| `max_refill_retries` | 回填重试次数上限。 | 整数 | `1~5`。 |
| `refill_check_interval_sec` | 回填检查频率。 | 浮点（秒） | `30~120`。 |
| `enable_pending_soft_eviction` | 是否启用 pending 软淘汰。 | 布尔 | 建议开启。 |
| `pending_soft_eviction_minutes` | pending 超时淘汰阈值。 | 浮点（分钟） | `30~120`。 |
| `pending_soft_eviction_check_interval_sec` | pending 淘汰检查间隔。 | 浮点（秒） | `60~600`。 |
| `buy_pause_min_free_balance` | 买入硬性可用资金下限 M（USDC）。`<=0` 关闭此功能。余额低于 M 时不再启动新买入任务，只保留卖出路径。 | 浮点 | 建议与风控口径一致，如 `20`。 |
| `buy_pause_balance_poll_interval_sec` | 低余额买入门禁的余额轮询间隔。 | 浮点（秒） | `30~300`。 |
| `title_blacklist` | 标题关键词黑名单策略。命中后禁止继续买入；有仓位时可选“清仓”或“仅卖出 maker”。 | 对象 | 见下方 2.1.1。 |

### 2.1.1 `scheduler.title_blacklist` 标题黑名单

| 字段 | 含义 | 类型/格式 | 默认 | 建议 |
| --- | --- | --- | --- | --- |
| `enabled` | 是否启用标题黑名单。 | 布尔 | `false` | 生产环境先灰度开启。 |
| `keywords` | 关键词数组（手工填写）。大小写不敏感。 | 字符串数组 | `[]` | 填较稳定的主题词。 |
| `match_on_slug` | 是否将 slug 也纳入关键词匹配。 | 布尔 | `true` | 建议保持 `true`。 |
| `action_with_position` | 命中黑名单且“有仓位”时的动作：`sell_only_maker` 或 `liquidate`。 | 枚举字符串 | `sell_only_maker` | 追求损失更小时用 `sell_only_maker`。 |

行为说明：

- 命中黑名单且**无仓位**：不买入，直接写入 EXIT（`TITLE_BLACKLIST_NO_POSITION`），并在当前运行周期内不再录用。
- 命中黑名单且**有仓位**：
  - `sell_only_maker`：启动仅卖出 maker（挂单卖出，不再买入）。
  - `liquidate`：走清仓通道快速卖出。

### 2.2 `scheduler` aggressive 模式参数

> `strategy_mode=aggressive` 是“进入激进模式主流程”的总开关；
> `aggressive_enable_self_sell_reentry` 是“是否启用自账户卖出成交回补（reentry）”子开关，二者不是重复项。

| 字段 | 含义 | 类型/格式 | 默认 | 建议 |
| --- | --- | --- | --- | --- |
| `strategy_mode` | 调度策略模式。`classic`=仅基础队列；`aggressive`=启用激进调度能力。 | 枚举字符串 | `classic` | 需要激进调度时设为 `aggressive`。 |
| `aggressive_burst_slots` | 激进模式下额外突发槽位数（在 `max_concurrent_tasks` 之外增加）。 | 整数 | `5` | 建议 `1~8`，过大可能放大资源竞争。 |
| `aggressive_enable_self_sell_reentry` | 是否开启“自账户 SELL 成交后将 token 重新加入 burst 队列”的能力。 | 布尔 | `false` | 需要发挥 aggressive 回补能力时设为 `true`。 |
| `aggressive_reentry_source` | reentry 数据源。当前支持 `self_account_fills_only`。 | 枚举字符串 | `self_account_fills_only` | 保持默认即可。 |
| `aggressive_first_sell_fill_only` | 同一 token 仅处理首笔 SELL 成交回补，避免重复触发。 | 布尔 | `true` | 建议保持 `true`。 |
| `aggressive_sell_fill_poll_sec` | 查询自账户 SELL 成交的轮询周期。 | 浮点（秒） | `15.0` | 常用 `10~30` 秒。 |

aggressive 典型配置（示例）：

```json
"scheduler": {
  "strategy_mode": "aggressive",
  "aggressive_burst_slots": 5,
  "aggressive_enable_self_sell_reentry": true,
  "aggressive_reentry_source": "self_account_fills_only",
  "aggressive_first_sell_fill_only": true,
  "aggressive_sell_fill_poll_sec": 15.0
}
```

### 2.3 `scheduler.total_liquidation` 总清仓参数

> 当活跃度长期偏低时触发“全局清仓 +（可选）硬重置 + 重启”，或在 `restart_only` 模式下仅重启。默认关闭。

| 字段 | 含义 | 类型/格式 | 默认 |
| --- | --- | --- | --- |
| `enable_total_liquidation` | 是否开启总清仓。 | 布尔 | `false` |
| `execution_mode` | 触发后的执行模式：`liquidate_then_restart`=执行清仓流程并重启；`restart_only`=仅重启，不清仓、不删除 JSON。 | 枚举字符串 | `restart_only` |
| `min_interval_hours` | 两次总清仓最小间隔。 | 浮点（小时） | `72` |
| `trigger.idle_slot_ratio_threshold` | 空闲槽位比例触发阈值。 | `0~1` 浮点 | `0.5` |
| `trigger.idle_slot_duration_minutes` | 空闲槽位持续时长阈值。 | 浮点（分钟） | `120` |
| `trigger.startup_grace_hours` | 启动保护期（保护期内不记空闲槽位条件）。 | 浮点（小时） | `6` |
| `trigger.no_trade_duration_minutes` | 长时间无成交/无行情更新阈值。 | 浮点（分钟） | `180` |
| `trigger.min_free_balance` | 可用 USDC 余额下限阈值。 | 浮点 | `20` |
| `trigger.enable_low_balance_force_trigger` | 是否启用“低余额持续超时即单独触发总清仓”。开启后满足持续时长会直接触发，不受 `require_conditions` 约束。 | 布尔 | `true` |
| `trigger.low_balance_force_hours` | 低余额持续触发阈值（小时）。当可用余额持续低于 `min_free_balance` 且达到该时长时，直接触发总清仓。 | 浮点（小时） | `6` |
| `trigger.balance_poll_interval_sec` | 余额采样间隔。 | 浮点（秒） | `120` |
| `trigger.require_conditions` | 触发所需命中条件数（常规条件计数；不含上述“低余额强制触发”分支）。 | 整数 | `2` |
| `liquidation.token_scope_mode` | 总清仓范围模式：`copytrade`=仅清仓 copytrade 记录 token；`all_positions`=清仓全部持仓 token。 | 枚举字符串 | `copytrade` |
| `liquidation.position_value_threshold` | 仅清仓价值不低于该值的仓位。 | 浮点 | `3` |
| `liquidation.spread_threshold` | 点差阈值：大于该值优先 maker，小于等于该值走 taker。 | 浮点 | `0.01` |
| `liquidation.maker_timeout_minutes` | maker 清仓超时，超时后回退 taker。 | 浮点（分钟） | `20` |
| `liquidation.taker_slippage_bps` | **taker 价格滑点缓冲（基点）**。卖出 taker 价格按 `base * (1 - bps/10000)` 计算，`base` 优先取 best bid。 | 浮点（bps） | `30` |
| `reset.hard_reset_enabled` | 总清仓后是否执行硬重置。 | 布尔 | `false` |
| `reset.remove_logs` | 硬重置时是否删日志。 | 布尔 | `false` |
| `reset.remove_json_state` | 硬重置时是否清理状态 JSON。 | 布尔 | `false` |

`taker_slippage_bps` 例子：
- 30 bps = 0.30%
- 若 `best_bid=0.60`，则 taker 卖出保护价约为 `0.60 * (1 - 0.003) = 0.5982`
- 该值越大，越容易成交，但均价可能更差。

`token_scope_mode` 例子：
- `copytrade`：只会清仓 copytrade 文件里出现过的 token（更保守，避免误清其他策略持仓）。
- `all_positions`：会清仓当前账户全部持仓 token（紧急风控场景推荐）。

`global_config.json` 配置示例：

```json
"total_liquidation": {
  "enable_total_liquidation": true,
  "execution_mode": "restart_only",
  "trigger": {
    "min_free_balance": 20.0,
    "enable_low_balance_force_trigger": true,
    "low_balance_force_hours": 6.0,
    "require_conditions": 2
  },
  "liquidation": {
    "token_scope_mode": "all_positions",
    "position_value_threshold": 3.0,
    "spread_threshold": 0.01,
    "maker_timeout_minutes": 30.0,
    "taker_slippage_bps": 30.0
  }
}
```

### 2.4 `maker` 子进程参数

| 字段 | 含义 | 类型/格式 |
| --- | --- | --- |
| `poll_sec` | maker 卖单跟踪轮询间隔。 | 浮点（秒） |
| `position_sync_interval` | 仓位同步间隔。 | 浮点（秒） |

### 2.5 `debug` 调试参数

| 字段 | 含义 | 类型/格式 |
| --- | --- | --- |
| `ws_debug_raw` | 是否输出原始 WS 调试信息。 | 布尔 |

### 2.6 `paths` 路径参数（可选）

| 字段 | 含义 | 类型/格式 |
| --- | --- | --- |
| `log_directory` | autorun 日志目录。 | 字符串路径 |
| `data_directory` | autorun 数据目录。 | 字符串路径 |
| `run_state_file` | 运行状态文件。 | 字符串路径 |
| `copytrade_tokens_file` | copytrade token 文件路径。 | 字符串路径 |
| `copytrade_sell_signals_file` | copytrade 卖出信号文件路径。 | 字符串路径 |

---

## 3) strategy_defaults.json —— 话题策略默认模板

| 字段 | 含义 | 类型/格式 |
| --- | --- | --- |
| `default.min_edge` | 最小优势阈值。 | `0~1` 浮点 |
| `default.max_position_per_market` | 单市场最大持仓。 | 浮点 |
| `default.order_size` | 默认下单量。 | 浮点 |
| `default.spread_target` | 目标点差。 | `0~1` 浮点 |
| `default.refresh_interval_seconds` | 刷新周期。 | 整数/浮点（秒） |
| `default.max_open_orders` | 最大挂单数。 | 整数 |
| `topics.<topic_id>.*` | 对特定话题覆盖默认参数。 | 与对应字段同类型 |

---

## 4) trading.yaml —— 执行引擎参数

用于 `trading/execution.py` 的 `ExecutionConfig`。

| 字段 | 含义 | 类型/格式 | 默认 |
| --- | --- | --- | --- |
| `order_slice_min` | 单笔拆单最小数量。 | 浮点 | `1.0` |
| `order_slice_max` | 单笔拆单最大数量。 | 浮点 | `2.0` |
| `retry_attempts` | 价格退让重试次数（总尝试=`retry_attempts+1`）。 | 整数 | `2` |
| `price_tolerance_step` | 每次重试价格退让步长。 | 浮点 | `0.01` |
| `wait_seconds` | 每次下单后等待成交的时间。 | 浮点（秒） | `5.0` |
| `poll_interval_seconds` | 订单状态轮询间隔。 | 浮点（秒） | `0.5` |
| `order_interval_seconds` | 拆单之间间隔；为空则等于 `wait_seconds`。 | 浮点（秒）/空 | `null` |
| `min_quote_amount` | 单笔最小金额限制（避免过小订单）。 | 浮点 | `1.0` |
| `min_market_order_size` | 单笔最小市价数量限制。 | 浮点 | `0.0` |

---

## 5) copytrade_config.json —— copytrade 抓取参数

用于 `copytrade/copytrade_run.py`。

| 字段 | 含义 | 类型/格式 | 建议 |
| --- | --- | --- | --- |
| `poll_interval_sec` | copytrade 轮询间隔。 | 浮点（秒） | `30~120`。 |
| `targets` | 监控账户列表。 | 数组 | 至少 1 个。 |
| `targets[].account` | 目标钱包地址。 | 字符串 | `0x...` 小写更稳妥。 |
| `targets[].min_size` | 该地址最小成交量过滤阈值。 | 浮点 | 过滤噪音小单。 |
| `targets[].enabled` | 是否启用该目标。 | 布尔 | 可临时关闭某地址。 |
| `initial_lookback_sec` | 预留字段（当前脚本版本未实际使用）。 | 浮点/整数 | 可保留，不影响运行。 |

---

## 6) 常见误解速查

1. `taker_slippage_bps` 不是“手续费”，而是 taker 卖出时的**价格让步缓冲**。
2. `spread_threshold` 不是百分比字符串，直接写价格差（如 `0.01`）。
3. `max_buy_price` 与 `buy_price_threshold` 都能限制买入：前者更偏“硬上限”，后者偏策略条件。
4. `initial_lookback_sec` 当前实现未消费，不会改变启动时“忽略历史仓位”的行为。
