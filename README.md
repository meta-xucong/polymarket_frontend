# Polymarket Frontend Workspace

这个仓库包含本地可运行的 Polymarket 前端控制台，以及相关策略与跟单代码。

## 推荐启动方式

优先使用最外层两个快捷方式：

- `打开桌面版.lnk`
- `打开网页版.lnk`

其中：

- `打开桌面版.lnk` 会启动桌面版控制台窗口
- `打开网页版.lnk` 会通过浏览器打开 `http://127.0.0.1:8787`

## 备用启动方式

以下 bat 文件仍然保留，但仅作为备用入口：

- `1_打开网页控制台.bat`
- `2_打开本地桌面版.bat`

## 目录说明

- `PolymarketDesktop_Final/`
  已整理好的本地发布目录
- `POLYMARKET_MAKER_copytrade_v2/`
  `v2` 主程序、前端面板和策略相关代码
- `POLY_SMARTMONEY/`
  `v3 multi` 相关代码
- `docs/`
  文档说明
- `tools/`
  辅助工具
- `使用说明.txt`
  面向日常使用的简版说明

## 补充说明

- 当前工作区已经清理掉测试缓存、临时构建目录和废弃备份文件。
- 账户信息默认保留在本地，不应提交真实密钥到 GitHub。
