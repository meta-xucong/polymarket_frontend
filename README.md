# Polymarket 一体化版本（Web / Desktop / Android）

这是当前可用版本的最简使用说明。

## 1. 下载后先做这一步

只需要改 2 个配置文件里的“账户/私钥”字段：

- `POLYMARKET_MAKER_copytrade_v2/account.json`
  - `POLY_KEY`
  - `POLY_FUNDER`
- `POLY_SMARTMONEY/copytrade_v3_muti/accounts.json`
  - `my_address`
  - `private_key`

其他默认参数已经按当前版本预置，不用改。

## 2. 怎么打开

### 网页版（推荐）

1. 双击：`备用启动_网页控制台.bat`
2. 浏览器打开：`http://127.0.0.1:8787`

### 桌面版

1. 双击：`备用启动_本地桌面版.bat`
2. 会打开桌面窗口（底层仍是同一个控制面板）

### 安卓版

1. 到 GitHub Release 下载 APK
2. 安装后打开即可
3. 注意：安卓版是远程壳，必须先有可访问的 Web 面板（本地或 VPS）

## 3. VPS 部署（简版）

部署目录：`deploy/`

核心文件：

- `deploy/linux/install_instance.sh`
- `deploy/panel.env.example`
- `deploy/nginx/polymarket_panel.conf.example`

先在 VPS 跑 `install_instance.sh`，再配反向代理域名，最后用手机访问域名确认可用。

## 4. 目录说明（只看这几个）

- `POLYMARKET_MAKER_copytrade_v2/`：主运行时 + 控制面板
- `POLY_SMARTMONEY/`：v3 multi 相关
- `android/`：Android 壳工程（Capacitor）
- `deploy/`：VPS 部署脚本

## 5. 安全提醒

不要把真实私钥提交到 GitHub。仓库中的默认账户文件已做脱敏处理。
