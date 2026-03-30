#!/usr/bin/env bash
set -euo pipefail

# =============================================
# Polymarket Frontend in-place update (Debian)
# =============================================
# Run as root:
#   sudo bash debian_update_and_restart.sh
#
# Optional env overrides before running:
#   APP_USER=polymarket
#   APP_DIR=/opt/polymarket_frontend
#   REPO_URL=https://github.com/meta-xucong/polymarket_frontend.git
#   REPO_BRANCH=main
#   RESTART_INACTIVE_SERVICES=0

APP_USER="${APP_USER:-polymarket}"
APP_DIR="${APP_DIR:-/opt/polymarket_frontend}"
REPO_URL="${REPO_URL:-https://github.com/meta-xucong/polymarket_frontend.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
RESTART_INACTIVE_SERVICES="${RESTART_INACTIVE_SERVICES:-0}"

POLY_CONF_DIR="/etc/polymarket"
PANEL_ENV_FILE="$POLY_CONF_DIR/panel.env"
TRADING_ENV_FILE="$POLY_CONF_DIR/trading.env"

SERVICES=(
  "polymarket-panel.service"
  "polymaker-copytrade.service"
  "polymaker-autorun.service"
  "copytrade-v3-multi.service"
)

step() {
  echo
  echo "[STEP] $*"
}

run_as_app() {
  local cmd=("$@")
  if command -v runuser >/dev/null 2>&1; then
    runuser -u "$APP_USER" -- "${cmd[@]}"
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -u "$APP_USER" "${cmd[@]}"
    return
  fi
  su -s /bin/bash "$APP_USER" -c "$(printf '%q ' "${cmd[@]}")"
}

service_exists() {
  local service="$1"
  [[ "$(systemctl show "$service" --property LoadState --value 2>/dev/null || true)" != "not-found" ]]
}

service_should_restart() {
  local service="$1"
  if [[ "$service" == "polymarket-panel.service" ]]; then
    return 0
  fi
  if [[ "$RESTART_INACTIVE_SERVICES" == "1" ]]; then
    return 0
  fi
  systemctl is-active --quiet "$service"
}

if [[ "$EUID" -ne 0 ]]; then
  echo "[ERROR] Please run as root: sudo bash $0"
  exit 1
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "[ERROR] $APP_DIR is not a git checkout. Run deploy/debian_oneclick_install.sh first."
  exit 1
fi

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "[ERROR] Missing virtualenv at $APP_DIR/.venv. Run deploy/debian_oneclick_install.sh first."
  exit 1
fi

step "Verify local checkout points to expected repository"
current_url="$(run_as_app git -C "$APP_DIR" remote get-url origin)"
if [[ "$current_url" != "$REPO_URL" ]]; then
  echo "[WARN] origin url mismatch"
  echo "       current: $current_url"
  echo "       expect : $REPO_URL"
fi

step "Fetch latest source and fast-forward update"
run_as_app git -C "$APP_DIR" fetch --all --tags
run_as_app git -C "$APP_DIR" checkout "$REPO_BRANCH"
run_as_app git -C "$APP_DIR" pull --ff-only origin "$REPO_BRANCH"

step "Refresh Python dependencies"
run_as_app "$APP_DIR/.venv/bin/pip" install --upgrade pip setuptools wheel
run_as_app "$APP_DIR/.venv/bin/pip" install \
  requests \
  pyyaml \
  websocket-client \
  "eth-hash[pycryptodome]" \
  pycryptodome
if ! run_as_app "$APP_DIR/.venv/bin/pip" install py-clob-client; then
  run_as_app "$APP_DIR/.venv/bin/pip" install py_clob_client
fi
if ! run_as_app "$APP_DIR/.venv/bin/pip" install py-order-utils; then
  run_as_app "$APP_DIR/.venv/bin/pip" install py_order_utils
fi
if ! run_as_app "$APP_DIR/.venv/bin/pip" install poly-eip712-structs; then
  run_as_app "$APP_DIR/.venv/bin/pip" install poly_eip712_structs
fi

step "Sanity check imports"
run_as_app "$APP_DIR/.venv/bin/python" - <<'PY'
import requests, yaml, websocket
from py_clob_client.client import ClobClient
print("[OK] Python deps are importable")
PY

step "Ensure runtime env files exist"
if [[ ! -f "$PANEL_ENV_FILE" || ! -f "$TRADING_ENV_FILE" ]]; then
  echo "[ERROR] Missing $PANEL_ENV_FILE or $TRADING_ENV_FILE. Run deploy/debian_oneclick_install.sh first."
  exit 1
fi

step "Reload systemd and restart running services"
systemctl daemon-reload
for service in "${SERVICES[@]}"; do
  if ! service_exists "$service"; then
    echo "[SKIP] $service not installed"
    continue
  fi
  if service_should_restart "$service"; then
    systemctl restart "$service"
    echo "[OK] restarted $service"
  else
    echo "[SKIP] $service inactive"
  fi
done

step "Show service states"
for service in "${SERVICES[@]}"; do
  if service_exists "$service"; then
    systemctl --no-pager --full status "$service" | sed -n '1,8p' || true
    echo
  fi
done

echo "[OK] Source update and service restart completed."
