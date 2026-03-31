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
#   UPDATE_SERVICE_FILES=1

APP_USER="${APP_USER:-polymarket}"
APP_DIR="${APP_DIR:-/opt/polymarket_frontend}"
REPO_URL="${REPO_URL:-https://github.com/meta-xucong/polymarket_frontend.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
RESTART_INACTIVE_SERVICES="${RESTART_INACTIVE_SERVICES:-0}"
UPDATE_SERVICE_FILES="${UPDATE_SERVICE_FILES:-1}"

POLY_CONF_DIR="/etc/polymarket"
PANEL_ENV_FILE="$POLY_CONF_DIR/panel.env"
TRADING_ENV_FILE="$POLY_CONF_DIR/trading.env"

V2_DIR_REL="POLYMARKET_MAKER_copytrade_v2"
V3_DIR_REL="POLY_SMARTMONEY/copytrade_v3_muti"

PANEL_SERVICE="/etc/systemd/system/polymarket-panel.service"
COPYTRADE_SERVICE="/etc/systemd/system/polymaker-copytrade.service"
AUTORUN_SERVICE="/etc/systemd/system/polymaker-autorun.service"
V3MULTI_SERVICE="/etc/systemd/system/copytrade-v3-multi.service"

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

step "Ensure runtime env files exist and have correct format"
if [[ ! -f "$PANEL_ENV_FILE" ]]; then
  echo "[ERROR] Missing $PANEL_ENV_FILE. Run deploy/debian_oneclick_install.sh first."
  exit 1
fi

# Check if trading.env has the new format (contains POLY_KEY)
if [[ ! -f "$TRADING_ENV_FILE" ]] || ! grep -q "^POLY_KEY=" "$TRADING_ENV_FILE" 2>/dev/null; then
  echo "[INFO] Updating $TRADING_ENV_FILE to new format"
  cat > "$TRADING_ENV_FILE" <<'EOF'
# Trading configuration - auto updated from account settings
POLY_HOST=https://clob.polymarket.com
POLY_CHAIN_ID=137
POLY_SIGNATURE=2
POLY_KEY=
POLY_FUNDER=
POLY_API_KEY=
POLY_API_SECRET=
POLY_API_PASSPHRASE=
POLY_DATA_ADDRESS=
EOF
  chmod 640 "$TRADING_ENV_FILE"
  chown root:"$APP_USER" "$TRADING_ENV_FILE"
fi

# Update systemd service files to use EnvironmentFile (if needed)
if [[ "$UPDATE_SERVICE_FILES" == "1" ]]; then
  step "Update systemd service files to use EnvironmentFile"
  
  # Check if services need updating (old format uses bash -lc)
  if grep -q "bash -lc.*source.*TRADING_ENV_FILE" "$AUTORUN_SERVICE" 2>/dev/null; then
    echo "[INFO] Updating service files to use EnvironmentFile directive"
    
    # Update panel service
    if [[ -f "$PANEL_SERVICE" ]]; then
      cat > "$PANEL_SERVICE" <<EOF
[Unit]
Description=Polymarket Panel Backend
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR/$V2_DIR_REL/panel
EnvironmentFile=$PANEL_ENV_FILE
EnvironmentFile=$TRADING_ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python server.py --host 127.0.0.1 --port 8787
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    fi
    
    # Update copytrade service
    if [[ -f "$COPYTRADE_SERVICE" ]]; then
      cat > "$COPYTRADE_SERVICE" <<EOF
[Unit]
Description=Polymaker Copytrade V2
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR/$V2_DIR_REL/copytrade
EnvironmentFile=$PANEL_ENV_FILE
EnvironmentFile=$TRADING_ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python copytrade_run.py --config copytrade_config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    fi
    
    # Update autorun service
    if [[ -f "$AUTORUN_SERVICE" ]]; then
      cat > "$AUTORUN_SERVICE" <<EOF
[Unit]
Description=Polymaker Autorun V2
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO
EnvironmentFile=$PANEL_ENV_FILE
EnvironmentFile=$TRADING_ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python poly_maker_autorun.py --no-repl
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    fi
    
    # Update v3multi service
    if [[ -f "$V3MULTI_SERVICE" ]]; then
      cat > "$V3MULTI_SERVICE" <<EOF
[Unit]
Description=Polymaker SmartMoney V3 Multi
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR/$V3_DIR_REL
EnvironmentFile=$PANEL_ENV_FILE
EnvironmentFile=$TRADING_ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python copytrade_run.py --config copytrade_config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    fi
    
    echo "[OK] Service files updated"
  else
    echo "[OK] Service files already up to date"
  fi
  
  # Ensure log symlinks exist
  step "Ensure log symlinks exist"
  mkdir -p "$APP_DIR/$V2_DIR_REL/copytrade/logs"
  if [[ ! -L "$APP_DIR/$V2_DIR_REL/copytrade/copytrade_systemd.log" ]]; then
    ln -sf "$APP_DIR/$V2_DIR_REL/copytrade/logs/copytrade_$(date +%Y%m%d).log" \
           "$APP_DIR/$V2_DIR_REL/copytrade/copytrade_systemd.log"
    echo "[OK] Created log symlink"
  fi
  
  # Ensure cron job for log links exists
  if [[ ! -f /etc/cron.daily/polymarket-log-links ]]; then
    cat > /etc/cron.daily/polymarket-log-links <<EOF
#!/bin/bash
# Update log symlinks daily
APP_DIR="$APP_DIR"
ln -sf "\$APP_DIR/$V2_DIR_REL/copytrade/logs/copytrade_\$(date +%Y%m%d).log" \
       "\$APP_DIR/$V2_DIR_REL/copytrade/copytrade_systemd.log" 2>/dev/null || true
EOF
    chmod +x /etc/cron.daily/polymarket-log-links
    echo "[OK] Created cron job for log links"
  fi
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
