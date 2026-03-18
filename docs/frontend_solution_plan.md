# POLYMARKET_MAKER_copytrade_v2 前端化方案

## 1. 这次方案的约束

按你的要求，方案调整为：

1. 只新增一个 `account.json`
2. 这个文件只负责替代原先从环境变量加载账户信息的方式
3. 其他现有配置文件尽量保留
4. 其他程序代码能少动就少动
5. 要方便后续让“终端运行版本”和“前端版本”持续同步优化

这意味着本次前端化不做“大规模配置重构”，而是走最小侵入方案。

## 2. 当前项目里真正需要替换的部分

从代码看，当前项目配置来源分两类：

### 2.1 已经是文件配置的部分

这些本来就是文件配置，不需要大改：

- [copytrade_config.json](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/copytrade/copytrade_config.json)
- [global_config.json](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json)
- [run_params.json](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/run_params.json)
- [strategy_defaults.json](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/strategy_defaults.json)
- [trading.yaml](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/trading.yaml)

所以前端完全可以直接编辑这些现有文件，没必要再新增一套 `app_config.json`。

### 2.2 仍依赖环境变量的部分

这部分才是需要替换的重点，主要是：

- `POLY_KEY`
- `POLY_FUNDER`
- `POLY_API_KEY`
- `POLY_API_SECRET`
- `POLY_API_PASSPHRASE`
- `POLY_HOST`
- `POLY_CHAIN_ID`
- `POLY_SIGNATURE`
- 可能还包括 `POLY_DATA_ADDRESS`

这些变量主要出现在：

- [Volatility_arbitrage_main_rest.py](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_main_rest.py)
- [Volatility_arbitrage_run.py](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/Volatility_arbitrage_run.py)
- [poly_maker_autorun.py](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/poly_maker_autorun.py)
- [total_liquidation_manager.py](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/total_liquidation_manager.py)

## 3. 方案调整后的核心思路

不再做“统一配置层”。

改为：

1. 新增一个 `account.json`
2. 增加一个很薄的账户加载模块
3. 在原先读取环境变量的地方，改成“优先读 `account.json`，没有再回退到环境变量”

这样做的结果是：

- 终端版本仍然能跑
- 前端版本也能跑
- 两者仍共用同一套业务逻辑
- 后续如果你继续优化核心策略，只需要维护一套主代码

这是目前最适合你的路径。

## 4. 推荐的 `account.json` 设计

建议只放“账户和连接相关信息”，不要把业务参数也塞进去。

文件建议放在：

`POLYMARKET_MAKER_copytrade_v2/account.json`

建议结构：

```json
{
  "account_name": "main",
  "funder": "0x...",
  "private_key": "0x...",
  "api_key": "",
  "api_secret": "",
  "api_passphrase": "",
  "host": "https://clob.polymarket.com",
  "chain_id": 137,
  "signature_type": 2,
  "data_address": ""
}
```

说明：

- `funder` 对应原来的 `POLY_FUNDER`
- `private_key` 对应原来的 `POLY_KEY`
- `api_key` / `api_secret` / `api_passphrase` 对应原来的 `POLY_API_*`
- `host` / `chain_id` / `signature_type` 对应原来的连接参数
- `data_address` 可选；如果为空，默认回退用 `funder`

## 5. 代码层面的最小改动方式

## 5.1 新增一个公共账户加载模块

建议新增：

`POLYMARKET_MAKER_copytrade_v2/account_loader.py`

它只做几件事：

1. 读取 `account.json`
2. 提供统一取值函数
3. 如果 `account.json` 不存在，就回退到环境变量

例如提供这类接口：

- `load_account_config()`
- `get_poly_key()`
- `get_poly_funder()`
- `get_api_creds()`
- `get_host_chain_signature()`

这样主逻辑代码不用大改，只要把散落的 `os.getenv(...)` 或 `os.environ[...]` 替换成这个模块。

## 5.2 修改原则

只改“取配置”的位置，不改交易逻辑，不改状态机，不改调度逻辑。

也就是：

- 不改下单逻辑
- 不改 copytrade 信号逻辑
- 不改 autorun 调度逻辑
- 不改 maker 核心逻辑
- 只改账户信息的读取入口

## 5.3 最好采用“优先 JSON，兼容 env”模式

推荐优先级：

1. `account.json`
2. 环境变量
3. 默认值

这样有三个好处：

1. 前端可直接管理 `account.json`
2. 终端老用法仍然保留，便于回滚和对照
3. 后续排查问题时更稳

## 6. 前端方案也要随之收缩

由于你现在不想大改配置结构，所以前端应该做成“现有配置文件的可视化编辑器 + 账户管理器”，而不是“重新定义整套配置系统”。

## 6.1 前端负责的内容

前端只做三件事：

1. 编辑 `account.json`
2. 编辑现有 JSON/YAML 配置中的关键字段
3. 启停和查看服务状态

## 6.2 前端不做的内容

当前阶段不建议做：

1. 新造一套中间配置层
2. 重写 systemd 运行链路
3. 重写策略配置结构
4. 把所有底层参数一次性搬上页面

## 7. 参数展示方式也要调整

既然现有配置文件保留，那么前端只需要“有选择地映射”几个关键参数。

## 7.1 账户页

来自 `account.json`：

- 账户名称
- 钱包地址
- 私钥
- API Key
- API Secret
- API Passphrase
- Host
- Chain ID
- Signature Type
- Data Address

## 7.2 跟单设置页

来自 [copytrade_config.json](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/copytrade/copytrade_config.json)：

- 跟单目标地址
- `poll_interval_sec`
- `initial_lookback_sec`
- `targets[].min_size`

如果后面你决定参考 `copytrade_v3_muti` 的部分写法，也建议只借鉴它“账户从 JSON 加载”的思路，不要把它整套多账户配置强行迁过来。

## 7.3 调度设置页

来自 [global_config.json](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json)：

- 最大子进程数 `max_concurrent_tasks`
- `copytrade_poll_seconds`
- `command_poll_seconds`
- `strategy_mode`
- `burst_slots`

## 7.4 策略设置页

来自 [run_params.json](D:/AI/vibe_coding3/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/run_params.json)：

- 每个话题最大下单量
- 下跌观察阈值 `drop_pct`
- 盈利观察阈值 `profit_pct`
- `sell_mode`
- `shock_guard.enabled`
- `shock_guard.shock_window_sec`
- `shock_guard.shock_drop_pct`

如果“每个话题最大下单量”最终对应的是 topic override 或 `strategy_defaults.json` 中的字段，前端只要做成清晰的映射即可，不需要改变原文件组织方式。

## 8. 架构建议

依然建议：

- 后端：`FastAPI`
- 前端：`React + Vite`
- 运行：继续用 `systemd`

但这次后端职责要更轻：

### 后端职责

1. 读写 `account.json`
2. 读写现有配置文件中的指定字段
3. 做字段校验
4. 提供服务启停接口
5. 提供日志和状态接口

### 前端职责

1. 展示关键配置
2. 保存配置
3. 重启服务
4. 查看运行状态

## 9. 页面建议

现在不需要太重的后台系统，做 4 个页面就够了：

### 9.1 Dashboard

- copytrade 状态
- autorun 状态
- 当前账户
- 当前目标地址
- 最近错误

### 9.2 账户设置

专门编辑 `account.json`

### 9.3 策略设置

把几个关键字段从现有配置文件里映射出来

### 9.4 日志与服务

- 启动
- 停止
- 重启
- 查看日志

## 10. 与终端版本同步优化的关键原则

这部分是这次方案里最重要的。

为了保证后续“终端版”和“前端版”能长期同步，建议坚持下面 4 条：

1. 前端不要引入新的业务逻辑
2. 前端不要维护独立配置格式，除了 `account.json`
3. 业务脚本继续直接读取原有配置文件
4. 对账户的改造只做成兼容层，不改核心逻辑

换句话说：

- 终端版仍然可以直接跑原项目
- 前端版只是帮用户写配置文件、管理 `account.json`、调用服务重启

这才不会形成“两套实现”。

## 11. 安全建议

既然私钥会进 `account.json`，至少要做：

1. 文件权限限制
2. 前端页面默认掩码显示私钥
3. 后端日志不打印敏感字段
4. 导出接口自动脱敏
5. Web 控制台增加一个简单管理员登录

但这里的“账号系统”建议先理解为：

- 控制台登录账号
- 交易账户配置表单

不建议一上来做多用户平台式账号体系。

## 12. 实施顺序

建议现在改成 3 步。

### 第 1 步

只做 `account.json` 替代 env：

- 新增 `account.json`
- 新增 `account_loader.py`
- 在少数 env 读取点接入兼容层

这是最优先的，也是对现有代码侵入最小的部分。

### 第 2 步

做 Web 控制台最小版：

- 账户设置
- 关键参数设置
- 服务启停
- 日志查看

### 第 3 步

再按需要补运行状态可视化和更完整的参数面板。

## 13. 最终结论

基于你现在的新约束，之前那种“统一配置层 + 配置编译器”的方案可以收掉，不适合当前目标。

更合适的方案是：

1. 只新增一个 `account.json`
2. 只新增一个很薄的账户加载模块
3. 让代码优先从 `account.json` 读账户和 API 信息，保留 env 兼容
4. 前端直接编辑现有配置文件中的关键字段
5. 不改核心交易逻辑和配置结构

这套方案更贴近你的真正需求：

- 改动小
- 风险低
- 易于和终端版长期同步
- 后面继续优化核心脚本时不会分叉出两条路线

## 14. 下一步建议

如果按这个思路继续，下一步最合理的是：

1. 先确定 `account.json` 的最终字段
2. 再实现最小版 `account_loader.py`
3. 然后把 env 读取点逐步替换成“JSON 优先，env 回退”
4. 最后再接前端页面

这样路径最稳。

## 15. copytrade_v3_muti 整合记录

### 15.1 总体方向

`copytrade_v3_muti` 已经天然适合前端化，因为它本来就是：

- `accounts.json` 管多账户
- `copytrade_config.json` 管全局策略参数

因此这部分不需要像 `POLYMARKET_MAKER_copytrade_v2` 那样再增加 `account.json` 兼容层。

整合建议：

1. 保留当前 panel，不新开第二个独立系统
2. 在现有 panel 顶部增加模式切换
3. 至少支持两个模式入口：
   - `POLYMARKET_MAKER v2`
   - `Copytrade v3 Multi`

优先建议使用标签页式切换，而不是完全跳新网页。这样后续继续扩展时更一致。

### 15.2 页面结构建议

`Copytrade v3 Multi` 页面建议分成三块：

1. 全局设置
2. 账户选择器
3. 当前账户设置

其中“账户选择器”建议用下拉菜单：

- 先选择账户
- 再显示该账户的参数

这样不会把多账户配置全部平铺到页面上，避免界面混乱。

### 15.3 建议前端体现的关键参数

#### 全局参数

来自 `copytrade_v3_muti/copytrade_config.json`：

- `target_addresses`
- `poll_interval_sec`
- `poll_interval_sec_exiting`
- `min_order_usd`
- `max_order_usd`
- `max_notional_per_token`
- `max_notional_total`
- `taker_enabled`
- `taker_spread_threshold`
- `taker_order_type`
- `maker_max_wait_sec`
- `maker_to_taker_enabled`
- `lowp_guard_enabled`
- `lowp_price_threshold`

#### 账户级参数

来自 `copytrade_v3_muti/accounts.json`：

- `name`
- `my_address`
- `follow_ratio`
- `max_notional_per_token`
- `max_notional_total`
- `enabled`

### 15.4 参数展示方式

前端展示时，不建议直接暴露代码字段名，而应使用业务含义更强的文案，例如：

- 跟单比例 (%)
- 单 token 最大跟单金额 (USD)
- 总持仓金额上限 (USD)
- 点差切换阈值
- Maker 最长等待时间 (秒)

所有关键字段都建议：

- 明确单位
- 支持悬停说明
- 对需要用户按百分数理解的字段，在前端做输入转换

### 15.5 Maker / Taker 当前逻辑确认

已确认 `copytrade_v3_muti` 当前仍保留以下主逻辑：

1. 先计算盘口点差：
   - `spread = best_ask - best_bid`
2. 当满足以下条件时优先走 `taker`：
   - `taker_enabled = true`
   - `spread <= taker_spread_threshold`
3. 当点差大于该阈值时，保留 `maker` 路径

也就是说，当前主规则仍然是：

- 点差大：偏 `maker`
- 点差小：偏 `taker`

相关代码位置：

- `copytrade_v3_muti/ct_exec.py`

### 15.6 当前逻辑的补充点

虽然主逻辑仍按点差阈值切换，但当前代码并不只是这一条规则，还额外存在：

1. `maker_to_taker_enabled`
   - 若启用，并且 `maker_max_wait_sec > 0`
   - Maker 挂单等待太久，会自动切到 Taker

2. 小额单 / 小额退出的 taker override
   - 某些小额场景会强制切到 taker

因此前端不应只暴露一个 `taker_spread_threshold`，还应该一起体现：

- `taker_enabled`
- `taker_spread_threshold`
- `maker_max_wait_sec`
- `maker_to_taker_enabled`

否则用户会误以为 Maker/Taker 行为只由一个点差参数决定。

### 15.7 下一步实施原则

如果后续正式开始整合 `copytrade_v3_muti`，应遵循：

1. 不改它现有的 JSON 配置结构
2. 前端只是可视化编辑 `accounts.json` 和 `copytrade_config.json`
3. 账户参数通过下拉选择器逐个编辑
4. Maker / Taker 逻辑相关参数成组展示
5. 所有说明文案以普通用户能读懂为准，而不是代码作者视角
