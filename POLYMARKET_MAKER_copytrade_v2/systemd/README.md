# systemd 部署说明（copytrade + POLYMARKET_MAKER_AUTO）

## 一键安装（推荐）

```bash
cd /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade
sudo bash POLYMARKET_MAKER_copytrade_v2/systemd/install_services.sh \
  /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade \
  root \
  /root/.pyenv/versions/poly312/bin/python \
  /root/.polymarket.env
```

参数说明：
1. `APP_ROOT`：仓库根目录。
2. `RUN_USER`：systemd 运行用户（默认 `root`）。
3. `PYTHON_BIN`：python 绝对路径。
4. `ENV_FILE`：环境变量文件路径（支持 `export KEY=VALUE` 格式，默认 `/root/.polymarket.env`）。

## 常用命令

```bash
# 状态
systemctl status polymaker-copytrade.service --no-pager -l
systemctl status polymaker-autorun.service --no-pager -l

# 重启
systemctl restart polymaker-copytrade.service
systemctl restart polymaker-autorun.service

# 停止
systemctl stop polymaker-copytrade.service
systemctl stop polymaker-autorun.service

# 日志
journalctl -u polymaker-copytrade.service -f
journalctl -u polymaker-autorun.service -f
```

## 常见问题：服务每 30 秒重启一次（Succeeded）

如果 `journalctl -u polymaker-autorun.service -f` 出现：
- `polymaker-autorun.service: Succeeded`
- 紧接着又被 `Scheduled restart job` 拉起

通常是因为 autorun 进入了交互命令循环（REPL），而 systemd 下无 TTY，
`stdin` 读到 EOF 后触发 `exit`，进程正常退出。

已通过 `--no-repl` 规避该问题。请重新安装/重启服务：

```bash
cd /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade
sudo bash POLYMARKET_MAKER_copytrade_v2/systemd/install_services.sh \
  /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade \
  root \
  /root/.pyenv/versions/poly312/bin/python
```

## 日志文件
- copytrade: `POLYMARKET_MAKER_copytrade_v2/copytrade/copytrade_systemd.log`
- autorun: `POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/autorun_systemd.log`

## 环境变量（重要）

systemd 默认不会继承你在 shell/screen 里的环境变量。若缺失 `POLY_KEY` / `POLY_FUNDER`，会出现：
- `error_rest: 'POLY_KEY'`

模板已改为 `bash -lc 'source ENV_FILE'` 模式，兼容你当前使用的：
- `~/.polymarket.env`（文件内是 `export POLY_KEY=...` 这种写法）

请确认该文件存在并可读，例如：

```bash
cat > /root/.polymarket.env <<'EOF'
POLY_KEY=0x你的私钥
POLY_FUNDER=0x你的资金地址
POLY_API_KEY=你的apiKey
POLY_API_SECRET=你的apiSecret
POLY_API_PASSPHRASE=你的apiPassphrase
EOF
chmod 600 /root/.polymarket.env
```

然后重新安装/重启服务：

```bash
cd /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade
sudo bash POLYMARKET_MAKER_copytrade_v2/systemd/install_services.sh \
  /home/trader/polymarket_api/POLYMARKET_MAKER_copytrade \
  root \
  /root/.pyenv/versions/poly312/bin/python \
  /root/.polymarket.env
```

## 关于路径是否是根因

你从 `/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2` 迁到
`/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade/POLYMARKET_MAKER_copytrade_v2` 本身不是根因。

本次错误的直接原因是：
1. `Volatility_arbitrage_main_ws` 缺少 `get_client` 导出（已在代码中补齐兼容函数）。
2. systemd 环境缺少 `POLY_KEY` 等变量（通过 `source ~/.polymarket.env` 解决）。
