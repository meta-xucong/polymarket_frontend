#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${1:-/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade}"
RUN_USER="${2:-root}"
PYTHON_BIN="${3:-/root/.pyenv/versions/poly312/bin/python}"
ENV_FILE="${4:-/root/.polymarket.env}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python 不存在或不可执行: $PYTHON_BIN" >&2
  exit 1
fi

TEMPLATE_DIR="$APP_ROOT/POLYMARKET_MAKER_copytrade_v2/systemd"
if [[ ! -d "$TEMPLATE_DIR" ]]; then
  echo "[ERROR] 模板目录不存在: $TEMPLATE_DIR" >&2
  exit 1
fi

PYTHON_BIN_DIR="$(dirname "$PYTHON_BIN")"

render_template() {
  local src="$1"
  local dst="$2"
  sed \
    -e "s|__APP_ROOT__|$APP_ROOT|g" \
    -e "s|__RUN_USER__|$RUN_USER|g" \
    -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
    -e "s|__PYTHON_BIN_DIR__|$PYTHON_BIN_DIR|g" \
    -e "s|__ENV_FILE__|$ENV_FILE|g" \
    "$src" > "$dst"
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

render_template "$TEMPLATE_DIR/polymaker-autorun.service.template" "$TMP_DIR/polymaker-autorun.service"
render_template "$TEMPLATE_DIR/polymaker-copytrade.service.template" "$TMP_DIR/polymaker-copytrade.service"

install -m 644 "$TMP_DIR/polymaker-autorun.service" /etc/systemd/system/polymaker-autorun.service
install -m 644 "$TMP_DIR/polymaker-copytrade.service" /etc/systemd/system/polymaker-copytrade.service

systemctl daemon-reload
systemctl enable polymaker-copytrade.service
systemctl enable polymaker-autorun.service

systemctl restart polymaker-copytrade.service
systemctl restart polymaker-autorun.service

echo "[OK] 服务已安装并重启"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[WARN] 未检测到环境变量文件: $ENV_FILE"
  echo "[WARN] 请创建该文件并写入 export POLY_KEY/POLY_FUNDER 等配置"
fi
echo "[INFO] 查看状态:"
echo "  systemctl status polymaker-copytrade.service --no-pager -l"
echo "  systemctl status polymaker-autorun.service --no-pager -l"
