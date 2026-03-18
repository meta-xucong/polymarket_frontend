# Polymarket Frontend Workspace

这个仓库包含两套本地可用的 Polymarket 控制台方案：

- `POLYMARKET_MAKER_copytrade_v2`
- `POLY_SMARTMONEY/copytrade_v3_muti`

当前已经整理出适合普通用户使用的启动入口。

## 快速开始

双击以下文件即可：

- `1_打开网页控制台.bat`
- `2_打开本地桌面版.bat`

其中：

- 网页控制台会自动打开 `http://127.0.0.1:8787`
- 本地桌面版会以桌面窗口形式启动同一套控制台

## 目录说明

- `1_打开网页控制台.bat`
  网页版启动入口
- `2_打开本地桌面版.bat`
  本地桌面入口
- `POLYMARKET_MAKER_copytrade_v2/`
  `v2` 主程序、前端面板、桌面入口相关代码
- `POLY_SMARTMONEY/`
  `v3 multi` 跟单程序及其依赖
- `docs/`
  开发规则、方案文档与内部说明
- `tools/`
  校验和辅助脚本
- `使用说明.txt`
  面向普通用户的简版说明

## 发布说明

- 仓库默认不提交本地日志、PID、临时缓存和构建产物
- 账户私钥、API 密钥等敏感配置请仅保留在本地
- GitHub 版本中的账户配置应使用占位值示例
