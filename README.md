# Polymarket 一体化版本（Web / Desktop / Android）

这个仓库提供三个可用版本：

- 桌面版：`PolymarketDesktop_Final/PolymarketDesktop.exe`
- 网页版：`PolymarketDesktop_Final/PolymarketWebPanel.exe`
- 安卓版：`android/`（APK 由 GitHub Release 提供）

## 1. 下载后先改这两个账号文件

只需要替换你自己的账户信息和私钥：

- `POLYMARKET_MAKER_copytrade_v2/account.json`
  - `POLY_KEY`
  - `POLY_FUNDER`
- `POLY_SMARTMONEY/copytrade_v3_muti/accounts.json`
  - `my_address`
  - `private_key`

其他参数已经保留当前默认值，开箱可用。

## 2. 怎么打开

推荐直接双击 `exe`：

- 桌面版：`PolymarketDesktop_Final/PolymarketDesktop.exe`
- 网页版：`PolymarketDesktop_Final/PolymarketWebPanel.exe`

备用入口（根目录）：

- `LaunchDesktop.bat`
- `LaunchWeb.bat`
- `备用启动_本地桌面版.bat`
- `备用启动_网页控制台.bat`

网页版默认地址：`http://127.0.0.1:8787`

## 3. 安卓版说明

安卓版是 Web 壳，必须先有可访问的 Web 面板（本机或 VPS）。

流程：

1. 先启动网页版并确认可访问。
2. 再安装 APK。
3. 安卓端访问你的面板地址。

## 4. VPS 部署（简版）

部署目录：`deploy/`

核心文件：

- `deploy/linux/install_instance.sh`
- `deploy/panel.env.example`
- `deploy/nginx/polymarket_panel.conf.example`

先跑安装脚本，再配置域名反代，最后用手机访问域名验证。

## 5. 安全提醒

- 仓库默认账户文件已脱敏。
- 不要把真实私钥提交到 GitHub。
