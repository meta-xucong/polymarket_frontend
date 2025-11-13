# Polymarket 套利工具商业化蓝图 v0.1

## 0. 目标回顾

你现有：

1. **策略脚本（已在 Linux 后台 CLI 运行）：**
   - （1）taker 吃单做波段套利  
   - （2）maker 挂单做波段套利  
   - （3）临近结束、价格接近 1.00 的扫尾单

2. **现在要做的升级：**
   - （A）一个可视化前端，方便用户填参数、启动/停止脚本  
   - （B）账户看板：当前持仓、历史持仓、胜率、盈亏等  
   - （C）对外提供服务：有域名的网站或 Telegram Bot，多用户注册、各用各的钱包/账号

> 设计原则：**能跑就行、先简单再优雅**。  
> 第一版不追求完美扩展性，而是基于你现有 CLI 脚本，最少改动实现一个可用的 MVP。

---

## 1. 总体技术路线（最简实现思路）

### 1.1 总体架构（推荐）

- **后端语言**：继续用 Python 3.12（复用你现有脚本）
- **Web 框架（推荐）**：  
  - 选 **FastAPI**：轻量、好写、前后端分离自然  
  - 如你更习惯「一体化」+后端模板，可以换成 **Django**（有内置用户系统 & Admin）
- **前端方案（第一版极简）：**
  - 直接用 **服务端渲染 HTML + Bootstrap 表单**（Jinja2 模板 / Django Template）  
  - 后续再考虑 React/Vue
- **数据库（第一版）**：
  - SQLite 起步（单机就够用）  
  - 未来再迁移 PostgreSQL
- **运行策略的方式（最简单）：**
  - Phase 1：直接用 subprocess 调现有 Python 脚本（少量参数）  
  - Phase 2 再慢慢把 CLI 脚本重构成可导入的模块函数（更优雅）

- **对外入口：**
  - 一个 VPS 上跑：
    - uvicorn + FastAPI（或 gunicorn + Django）
    - 前面挂 nginx + SSL
  - Telegram Bot 作为 一个额外的客户端，调用同一套后端 API

---

## 2. Phase 0：整理现有脚本，预备接入 Web

### 2.1 整理仓库结构

目标：把「策略核心逻辑」和「命令行入口」分开，以便 Web 后端可以复用。

推荐结构示例（你可按自己习惯微调）：

```text
polymarket_trader/
  strategies/
    taker_wave.py           # taker 吃单波段
    maker_wave.py           # maker 波段
    end_sweeper.py          # 尾单扫货
    common_client.py        # 统一 get_client / view_positions / claim 等
  cli/
    run_taker_wave.py
    run_maker_wave.py
    run_end_sweeper.py
  webapp/
    main.py                 # FastAPI / Django 项目入口
    ...
```

### 2.2 为每个策略提供一个“函数入口”

在不大改现有逻辑的前提下，在每个脚本里抽一个 run_strategy(config) 函数，例如：

```python
# strategies/taker_wave.py

class TakerWaveConfig(BaseModel):
    market_url: str
    side: str          # YES / NO
    size: float
    drop_pct: float
    profit_pct: float
    # ... 其他参数

def run_taker_wave(cfg: TakerWaveConfig, account_label: str = "default") -> None:
    # 单次运行：直到本轮买入+卖出结束 或 到达结束时间主动停止。
    # 可保持你现在 Volatility_arbitrage_run 的主循环逻辑不变，
    # 只是把 input() 改成从 cfg 里取参数。
    ...
```

命令行入口只负责把命令行参数组装成 cfg 然后调用 run_taker_wave，这样：

- Web 后端可以 import strategies.taker_wave 直接调用
- 你原来在 Linux 终端 python run_taker_wave.py ... 也能照常用

> 第一版如果你不想改太多，也可以先保持 CLI 形式，Web 后端用 subprocess 调用；但从长期看，抽出 run_XXX 是非常值得的。

---

## 3. Phase 1：本机 Web 控制台（单用户版）

目标：先给 你自己 做一个 Web 控制台，可视化操作 3 套脚本，观察账户信息。  
这一步不用管「注册、多个用户」，就假设只有你一个 superuser。

### 3.1 快速起一个 FastAPI 项目（示例）

目录结构示意：

```text
webapp/
  main.py
  deps.py
  routes/
    ui.py          # 返回 HTML 页面
    api.py         # 提供 JSON 接口（后面给 Telegram 用）
  templates/
    base.html
    index.html
    taker_wave.html
    maker_wave.html
    end_sweeper.html
    account.html
  static/
    css/ js/
```

main.py 大致：

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .routes import ui, api

app = FastAPI(title="Polymarket Arbitrage Console")

app.mount("/static", StaticFiles(directory="webapp/static"), name="static")
templates = Jinja2Templates(directory="webapp/templates")

app.include_router(ui.router)
app.include_router(api.router, prefix="/api")
```

### 3.2 策略启动流程（最简单版）

#### 3.2.1 Web 表单

例如 /taker_wave 页面上：

- 输入项：
  - 市场 URL
  - 方向（YES/NO）
  - 份数
  - 跌幅阈值
  - 盈利阈值
  - 是否扫尾 / 结束时间等
- 「启动」按钮

表单 POST 到 /api/run/taker_wave。

#### 3.2.2 后端如何运行脚本（两种路径）

路径 A（最简单）—— subprocess 调 CLI 脚本：

- /api/run/taker_wave：
  1. 将表单数据保存到简单的记录（可先写入本地 JSON 或内存）
  2. 用 subprocess.Popen(["python", "cli/run_taker_wave.py", "--market", ..., "--size", ...])
  3. 把 Popen.pid 或一个 run_id 记录下来，以后可以在页面上展示「运行中」

优点：改动最少、最快上线，完全保留你现在 CLI 行为。  
缺点：控制粒度有限：日志、状态需要额外处理（输出重定向到 log 文件，再在页面上 tail）

路径 B（稍优雅）—— 直接调用 run_taker_wave(cfg)：

- 在 /api/run/taker_wave 中：
  - 起一个后台线程，里面跑 run_taker_wave(cfg)
  - 线程 ID / 运行状态存记录
  - 日志可以用 Python logging 写入文件

建议：Phase 1 可以先选路径 A，跑通闭环，然后再慢慢重构到 B。

### 3.3 账户状态页面（复用你已有的查询脚本）

你已经有类似 view_positions.py 之类脚本。思路：

1. 把其中「核心查询逻辑」抽成函数，例如：

   ```python
   # strategies/common_client.py

   def get_current_positions(client) -> List[Position]:
       ...
   ```

2. 在 webapp/routes/api.py 中提供接口：

   ```python
   @router.get("/account/positions")
   def api_positions():
       client = get_client()   # 复用你 Volatility_arbitrage_main 的逻辑
       positions = get_current_positions(client)
       return {"positions": [p.to_dict() for p in positions]}
   ```

3. ui 路由中，/account 页面渲染：

   ```python
   @router.get("/account")
   def account(request: Request):
       # 调用上面的 api 或直接调 common_client
       ...
       return templates.TemplateResponse("account.html", {...})
   ```

### 3.4 胜率与历史仓位统计（第一版）

第一版可以非常粗暴：

- 在每次「策略卖出成功」后，你的脚本里已经拿到了：
  - 买入价 / 卖出价
  - 数量、时间、token_id、市值
- 在脚本中新增一行：

  ```python
  record_trade_to_db(user_id="local_admin", strategy="taker_wave",
                     token_id=token_id, entry_px=buy_px, exit_px=sell_px,
                     size=fill_size, pnl=..., is_win=...)
  ```

- record_trade_to_db() 写入本地 SQLite 表：trades

然后，Web 端只要：

- /api/account/stats：
  - 查询 trades 表，按策略统计：
    - 总笔数、盈利笔数、胜率
    - 总 PnL、平均单笔 PnL

- /account 页面展示简单统计卡片 + 一张折线图（前端可以用 Chart.js）。

---

## 4. Phase 2：多用户 & 账号体系（SaaS 化的核心）

当本机控制台跑稳后，下一步是「让别的用户也能用」。

### 4.1 用户模型与资金模式（需要优先想清楚）

这里是商业化的关键，会影响安全性与合规。最简单有两种模式：

1. 托管模式（你管理资金）：
   - 每个用户往你的 EOA / Safe 充值 USDC
   - 你的系统在 DB 中给每个用户维护「虚拟余额」和「仓位」
   - 所有实盘交易都在你的钱包上发生
   - 提现由你人工/半自动处理

2. 用户自持资金 + 你只帮下单：
   - 用户提供 Polymarket API Key/Secret（如果官方支持）或私钥（风险更高）
   - 你的系统代表用户调用 CLOB 下单
   - 你必须安全存储这些敏感信息（加密 + 权限管理）

如果只做小范围内测，Phase 2 可以先选托管模式，避免私钥存储问题，逻辑也更简单。

### 4.2 数据库设计（简化版）

以托管模式为例：

- users  
  - id（主键）
  - email / telegram_id / 登录名
  - 密码哈希（或使用 OAuth / Telegram 登录）
  - 角色（admin / normal）

- accounts（可选，若一个用户可能有多个资金池）  
  - id, user_id, label, balance_usdc, frozen_usdc

- strategy_runs  
  - id, user_id, account_id
  - strategy_type（taker_wave / maker_wave / end_sweeper）
  - config_json（序列化参数）
  - status（running / finished / failed / cancelled）
  - created_at, updated_at, start_time, end_time
  - 关联进程 ID / 线程 ID / 日志文件路径

- trades  
  - id, user_id, account_id, run_id
  - market_url, token_id, side（BUY/SELL）
  - size, price, notional
  - opened_at, closed_at
  - pnl, is_win

### 4.3 用户注册 / 登录（最简单）

- 若使用 FastAPI：可以用
  - 自己写一个最简用户表 + JWT 登录  
  - 或引入 fastapi-users 这类库
- 若使用 Django：
  - 直接用内置的 User 模型 + Session 登录

第一版不需要复杂权限控制：

- 普通用户：只能看到自己的 strategy_runs 和 trades
- admin：可以看到所有人的，用来排查问题

---

## 5. Phase 3：域名 + 网站上线 + Telegram Bot

### 5.1 域名 & HTTPS

1. 去任意注册商买一个域名（例如 poly-arb.xyz）
2. 将域名 A 记录解析到你 VPS IP
3. 在 VPS 上：
   - 用 nginx 反向代理 uvicorn / gunicorn
   - 用 certbot 或 acme.sh 给域名签发免费 SSL 证书

### 5.2 Telegram Bot（作为额外入口）

- 用户在手机 Telegram 里直接：
  - /start 注册/登录
  - /positions 看当前仓位
  - /run_taker 快速下单常用策略

实现步骤与具体细节可在后续版本补充。

---

## 6. 安全 & 运维最小注意事项

1. 私钥 / API 凭证管理：
   - 第一版尽量只用你自己的托管钱包，对外仅限熟人 & 小额
   - 若未来要支持用户自带钱包，必须做：
     - 加密存储
     - 限制提币权限（机器人只负责交易，不负责大额转账）

2. 风控与限额：
   - 为每个用户设置 单笔 / 单日最大仓位 限制
   - 策略启动前做简单检查：盘口太薄、不符合现有筛选逻辑则拒绝

3. 日志与监控：
   - 系统日志单独目录（按日期/ run_id 分文件）
   - 记录 API 调用失败原因（Polymarket 429 / 5xx 等）

4. 备份：
   - SQLite 定期备份
   - 脚本和配置全入 Git

---

## 7. 总结：一步一步怎么走

如果只看「执行顺序」，可以简化成这几步：

1. 把现有 3 套脚本稍微整理一下  
2. 起一个本地 FastAPI 服务  
3. 抽出账户查询函数，做一个 /account 页面  
4. 在脚本里增加 DB 写入  
5. 引入用户表，实现最简登录  
6. 最后再做域名 + HTTPS + Telegram Bot

到此，就是一条从「本地 CLI 脚本」到「多人可用 SaaS 原型」的路线。
