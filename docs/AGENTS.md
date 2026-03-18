# Polymarket Maker Copytrade - AI Agent Guide

## Project Overview

这是一个基于 Polymarket 预测市场平台的自动化做市与跟单交易系统。系统通过监控"聪明钱"账户的交易活动，自动跟随其交易决策，在波动性套利中执行 maker-only 策略（仅挂单，不吃单）。

项目包含两大核心模块：
- **Copytrade 模块**：监控目标账户的交易活动，生成 token 信号
- **Maker Autorun 模块**：调度并执行波动性套利策略，管理多个并发交易任务

## Project Structure

```
POLYMARKET_MAKER_copytrade/
├── .env.template                    # 环境变量模板
├── .gitignore                       # Git 忽略规则
├── polymaker-autorun.service        # systemd 服务配置（已渲染）
├── CODING_RULES.md                  # 编码规范（必读）
└── POLYMARKET_MAKER_copytrade_v2/   # 主代码目录
    ├── copytrade/                   # 跟单监控模块
    │   ├── copytrade_run.py         # 跟单主程序入口
    │   ├── copytrade_config.json    # 监控目标配置
    │   ├── copytrade_state.json     # 状态跟踪
    │   ├── tokens_from_copytrade.json    # 产出的 token 列表
    │   └── manual_intervention_tokens.json   # 手动干预记录
    │
    ├── POLYMARKET_MAKER_AUTO/       # 主调度模块
    │   ├── poly_maker_autorun.py    # 主调度器（核心）
 │   ├── total_liquidation_manager.py  # 全局清仓管理
    │   ├── healthcheck.sh           # 健康检查脚本
    │   ├── data/                    # 运行时数据
    │   │   ├── exit_tokens.json     # 退出 token 记录
    │   │   ├── handled_topics.json  # 已处理话题
    │   │   ├── token_cycle_gate.json    # Token 周期状态
    │   │   └── autorun_status.json  # 运行状态
    │   │
    │   └── POLYMARKET_MAKER/        # 核心交易逻辑
    │       ├── Volatility_arbitrage_run.py       # 策略运行入口
    │       ├── Volatility_arbitrage_strategy.py  # 波动性套利策略
    │       ├── maker_execution.py                # 挂单执行逻辑
    │       ├── market_state_checker.py           # 市场状态检测
    │       ├── shock_guard.py                    # 市场冲击保护
    │       ├── Volatility_arbitrage_main_ws.py   # WebSocket 客户端
    │       ├── Volatility_arbitrage_main_rest.py # REST 客户端
    │       └── tests/               # 测试目录（pytest）
    │
    ├── smartmoney_query/            # 数据 API 客户端包
    │   ├── __init__.py
    │   ├── api_client.py            # DataApiClient 实现
    │   └── models.py                # Trade 数据模型
    │
    └── systemd/                     # 部署脚本
        ├── install_services.sh      # 一键安装脚本
        ├── README.md                # 部署文档
        ├── polymaker-autorun.service.template
        └── polymaker-copytrade.service.template
```

## Technology Stack

- **Language**: Python 3.12+
- **Environment**: pyenv + 虚拟环境（`poly312`）
- **Key Dependencies**:
  - `requests` - HTTP 客户端
  - `websocket-client` - WebSocket 连接
  - `pytest` - 测试框架

## Core APIs

系统对接 Polymarket 官方 API：

1. **Data API**: `https://data-api.polymarket.com`
   - `/positions` - 查询持仓
   - `/trades` - 查询成交记录
   - `/activity` - 账户活动

2. **CLOB API**: `https://clob.polymarket.com`
   - 订单簿查询
   - 下单/撤单

3. **Gamma API**: `https://gamma-api.polymarket.com`
   - 市场元数据查询
   - 市场状态检测

## Key Concepts

### 1. Token 生命周期

```
发现（Copytrade） → 调度（Autorun） → 执行（Maker） → 退出 → 回填（Refill）
```

- **回填（Slot Refill）**: 退出的 token 经过冷却期后可重新进入调度队列
- **Token 周期状态**: 记录每个 token 的买卖轮次和下次可买入时间

### 2. 策略模式

- **Classic 模式**: 标准波动性套利，下跌买入、上涨卖出
- **Aggressive 模式**: 更激进的重新进入策略

### 3. 市场状态检测

系统主动查询 API 检测市场状态：
- `ACTIVE` - 正常交易
- `CLOSED` - 市场关闭（等待结算）
- `RESOLVED` - 已结算
- `NOT_FOUND` - 市场不存在

### 4. 全局清仓（Total Liquidation）

当系统长期空闲或余额不足时，自动清仓所有持仓并可选硬重置。

## Configuration

### 环境变量（.env）

```bash
# 必填 - API 凭证
POLY_KEY=                    # 私钥
POLY_FUNDER=                 # 资金地址（Proxy/Deposit）
POLY_API_KEY=                # API Key
POLY_API_SECRET=             # API Secret
POLY_API_PASSPHRASE=         # API Passphrase

# 可选 - API 端点覆盖
POLY_DATA_API_ROOT=https://data-api.polymarket.com
POLY_CLOB_API_ROOT=https://clob.polymarket.com

# 可选 - 运行时模式
POLY_RUN_MODE=local
```

### 全局配置（poly_maker_autorun.py 内 DEFAULT_GLOBAL_CONFIG）

关键配置项：
- `copytrade_poll_sec`: 30 - 跟单轮询间隔
- `max_concurrent_tasks`: 10 - 最大并发任务数
- `strategy_mode`: "classic" - 策略模式
- `enable_slot_refill`: true - 启用回填
- `refill_cooldown_minutes`: 5 - 回填冷却时间
- `ws_silence_timeout_sec`: 1200 - WS 静默重连阈值

## Build and Run

### 本地开发运行

```bash
# 1. 启动跟单监控
cd POLYMARKET_MAKER_copytrade_v2/copytrade
python copytrade_run.py --config copytrade_config.json

# 2. 启动主调度器
cd ../POLYMARKET_MAKER_AUTO
python poly_maker_autorun.py
```

### 生产部署（systemd）

```bash
# 一键安装服务
cd /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade
sudo bash POLYMARKET_MAKER_copytrade_v2/systemd/install_services.sh \
  /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade \
  root \
  /root/.pyenv/versions/poly312/bin/python \
  /root/.polymarket.env

# 查看状态
systemctl status polymaker-autorun.service --no-pager -l
systemctl status polymaker-copytrade.service --no-pager -l

# 重启服务
systemctl restart polymaker-autorun.service
systemctl restart polymaker-copytrade.service

# 查看日志
journalctl -u polymaker-autorun.service -f
journalctl -u polymaker-copytrade.service -f
```

## Testing

```bash
# 运行所有测试
cd POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER
cd tests
pytest -v

# 运行特定测试
pytest test_maker_execution.py -v
pytest test_strategy_compat.py -v
```

### 主要测试文件

- `test_maker_execution.py` - 挂单执行逻辑测试
- `test_strategy_compat.py` - 策略兼容性测试
- `test_shock_guard.py` - 市场冲击保护测试
- `test_total_liquidation_manager.py` - 清仓管理测试
- `test_market_state_checker.py` - 市场状态检测测试

## Code Style Guidelines

参考 `CODING_RULES.md`：

1. **官方文档合规**: 所有 Polymarket API 使用必须严格遵循官方文档
2. **显式变量命名**: 使用清晰、明确的变量名，与官方 API 命名保持一致
3. **强制自检**: 每次代码变更后运行相关测试
4. **失败处理**: 防御式处理 API/网络错误，记录可操作的上下文
5. **回复后缀**: 每次助手回复必须以 `喵~` 结尾

## Key Modules Explained

### poly_maker_autorun.py

主调度器，职责：
- 轮询 copytrade 产出的 token 文件
- 管理并发任务槽位（slot）
- 调度子进程执行具体策略
- 处理 token 回填（refill）
- 维护 WebSocket 聚合连接

### maker_execution.py

挂单执行核心，提供：
- `maker_buy_follow_bid()`: 跟随最高买价挂单买入
- `maker_sell_follow_ask_with_floor_wait()`: 跟随最低卖价挂单卖出（带价格下限）

### Volatility_arbitrage_strategy.py

策略状态机：
- `FLAT` → `LONG`: 价格下跌超过阈值时触发买入
- `LONG` → `FLAT`: 价格上涨达到盈利目标时触发卖出

### market_state_checker.py

市场状态检测器：
- 查询 Gamma API 获取市场元数据
- 查询 CLOB Book 检测流动性
- 判断市场是否可交易/可回填

## Security Considerations

1. **私钥管理**: 使用环境变量或外部文件（`~/.polymarket.env`），**绝不硬编码**
2. **权限设置**: 私钥文件应设置 `chmod 600` 权限
3. **日志脱敏**: 错误日志中不得包含私钥、API Secret 等敏感信息
4. **网络超时**: 所有 API 调用必须设置超时

## Common Issues

### 服务每 30 秒重启

现象：`journalctl` 显示 `Succeeded` 后立即重启

原因：autorun 进入了交互命令循环（REPL），systemd 下无 TTY，`stdin` 读到 EOF 后触发退出

解决：确保服务使用 `--no-repl` 参数启动

### 环境变量缺失

现象：`error_rest: 'POLY_KEY'`

原因：systemd 默认不继承 shell 环境变量

解决：使用 `bash -lc 'source ~/.polymarket.env'` 模式

## File Operations

系统使用原子写入模式（先写临时文件，再 rename）：

```python
def _atomic_json_write(path: Path, data: Any) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, str(path))
    except:
        os.unlink(tmp_path)
        raise
```

## Data Files

运行时数据文件说明：

| 文件 | 用途 | 更新频率 |
|------|------|----------|
| `data/exit_tokens.json` | 记录退出的 token 及原因 | 每次退出时 |
| `data/handled_topics.json` | 已处理过的 token 列表 | 每次启动新任务时 |
| `data/token_cycle_gate.json` | Token 买卖周期状态 | 每次完成卖出周期时 |
| `data/autorun_status.json` | 运行时状态快照 | 定期更新 |
| `data/ws_cache.json` | WebSocket 行情缓存 | 实时更新 |
| `logs/autorun/error_log.txt` | 错误日志 | 出错时 |
| `logs/autorun/autorun_main_*.log` | 主程序日志 | 持续写入 |

---

*最后更新: 2026-03-03*
## Frontend Change Notes

鍓嶇鐩稿叧浠诲姟闇€棰濆閬靛惊浠ヤ笅鍘熷垯锛?

1. 鍓嶇鏄鐜版湁缁堢鐗堢殑鍖呰锛屼笉鏄噸鍐欎竴濂椾笟鍔＄郴缁熴€傞櫎 `account.json` 澶栵紝涓嶈涓烘柟渚垮墠绔啀寮曞叆鏂扮殑涓棿閰嶇疆灞傘€?
2. 鏈」鐩殑鍓嶇鏀归€犱互 `account.json` 涓轰富锛岀敤鏉ヤ唬鏇?`POLY_KEY`銆?`POLY_FUNDER`銆?`POLY_API_KEY`銆?`POLY_API_SECRET`銆?`POLY_API_PASSPHRASE` 绛夌幆澧冨彉閲忓姞杞芥柟寮忋€?
3. 璐︽埛鍔犺浇蹇呴』淇濇寔鍏煎妯″紡锛?鍏堣 `account.json`锛屽啀鍥為€€鍒扮幆澧冨彉閲忋€傜粓绔増鐨?env 鍏ュ彛涓嶈兘鐩存帴鍒犳帀銆?
4. 鍓嶇鍙礋璐ｇ紪杈?`account.json` 鍜岀幇鏈?JSON/YAML 閰嶇疆涓殑鍏抽敭鍙傛暟锛屼笉鍦ㄥ墠绔噸瀹炵幇浠讳綍浜ゆ槗銆佽皟搴︺€侀鎺ч€昏緫銆?
5. 涓嶈涓哄墠绔崟鐙畾涔変竴濂楁柊鐨勪笟鍔￠厤缃牸寮忋€傞櫎 `account.json` 澶栵紝鍏朵粬閰嶇疆搴斿敖閲忕户缁娇鐢ㄧ幇鏈夋枃浠跺苟鍋氬瓧娈垫槧灏勩€?
6. 鍓嶇鏆撮湶鐨勫弬鏁拌鏈夐€夋嫨锛屼紭鍏堟彁渚涢潪鎶€鏈敤鎴疯兘鐞嗚В鐨勫叧閿殑瀛楁锛屽鏈€澶у瓙杩涚▼鏁般€佹瘡璇濋鏈€澶т笅鍗曢噺銆?`drop_pct`銆?`profit_pct` 绛夈€?
7. 鏁忔劅淇℃伅锛堝 `private_key`銆?`api_secret`銆?`api_passphrase`锛夊湪椤甸潰涓粯璁ゆ巽鐮佹樉绀猴紝鍚庣鏃ュ織鍜屽墠绔晫闈腑閮戒笉寰楁槑鏂囨墦鍗般€?
8. 鍥犱负浠撳簱鍐呭凡鏈夌紪鐮佹晱鎰熸枃浠讹紝淇敼 MD/Python/JSON 鏃朵弗绂佸叏鏂囬噸鍐欙紝蹇呴』浣跨敤灏忚寖鍥?patch銆傛瘡娆′慨鏀瑰悗搴旇繍琛?`python tools/verify_source_integrity.py --root .` 鍋氬畬鏁存€ф鏌ャ€?
9. 鍓嶇鎴栧悗绔閰嶇疆鏂囦欢鐨勫啓鍥炲簲浣跨敤鍘熷瓙鍐欏叆锛堜复鏃舵枃浠?+ replace锛夛紝閬垮厤杩愯涓厤缃枃浠惰鍐欏潖銆?
10. 浠诲姟娑夊強鍓嶇鎺ュ叆銆佽处鎴峰姞杞芥垨鏈嶅姟鍚仠鏃讹紝蹇呴』鍋氳嚜妫€鍜岀浉鍏虫鏌ワ紝纭繚涓嶇牬鍧忕幇鏈夌殑缁堢杩愯璺緞銆?
