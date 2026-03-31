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
INSTANCE_DIR="${INSTANCE_DIR:-/var/lib/polymarket_frontend}"
REPO_URL="${REPO_URL:-https://github.com/meta-xucong/polymarket_frontend.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
RESTART_INACTIVE_SERVICES="${RESTART_INACTIVE_SERVICES:-0}"
UPDATE_SERVICE_FILES="${UPDATE_SERVICE_FILES:-1}"
SELF_REEXECED="${POLY_UPDATE_REEXECED:-0}"
SCRIPT_REL="deploy/debian_update_and_restart.sh"

POLY_CONF_DIR="/etc/polymarket"
PANEL_ENV_FILE="$POLY_CONF_DIR/panel.env"
TRADING_ENV_FILE="$POLY_CONF_DIR/trading.env"

V2_DIR_REL="POLYMARKET_MAKER_copytrade_v2"
V3_DIR_REL="POLY_SMARTMONEY/copytrade_v3_muti"
V2_ACCOUNT_REL="$V2_DIR_REL/account.json"
V2_COPYTRADE_CONFIG_REL="$V2_DIR_REL/copytrade/copytrade_config.json"
V2_GLOBAL_CONFIG_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json"
V2_RUN_PARAMS_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/run_params.json"
V2_STRATEGY_DEFAULTS_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/strategy_defaults.json"
V2_TRADING_YAML_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/trading.yaml"
V2_STATUS_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/data/autorun_status.json"
V2_TOKENS_REL="$V2_DIR_REL/copytrade/tokens_from_copytrade.json"
V2_COPYTRADE_STATE_REL="$V2_DIR_REL/copytrade/copytrade_state.json"
V3_COPYTRADE_CONFIG_REL="$V3_DIR_REL/copytrade/copytrade_config.json"
V3_ACCOUNTS_REL="$V3_DIR_REL/accounts.json"
V3_DEFAULT_ACCOUNT_JSON='{"accounts":[{"name":"Account 1","enabled":true,"private_key":"","funder":"","api_key":"","api_secret":"","api_passphrase":"","data_address":""}]}'
PANEL_AUTH_REL="panel/auth.json"

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
before_head="$(run_as_app git -C "$APP_DIR" rev-parse HEAD)"
run_as_app git -C "$APP_DIR" fetch --all --tags
run_as_app git -C "$APP_DIR" checkout "$REPO_BRANCH"
run_as_app git -C "$APP_DIR" pull --ff-only origin "$REPO_BRANCH"
after_head="$(run_as_app git -C "$APP_DIR" rev-parse HEAD)"
if [[ "$SELF_REEXECED" != "1" && "$before_head" != "$after_head" ]]; then
  step "Re-exec latest updater after source refresh"
  exec env POLY_UPDATE_REEXECED=1 \
    APP_USER="$APP_USER" \
    APP_DIR="$APP_DIR" \
    INSTANCE_DIR="$INSTANCE_DIR" \
    REPO_URL="$REPO_URL" \
    REPO_BRANCH="$REPO_BRANCH" \
    RESTART_INACTIVE_SERVICES="$RESTART_INACTIVE_SERVICES" \
    UPDATE_SERVICE_FILES="$UPDATE_SERVICE_FILES" \
    bash "$APP_DIR/$SCRIPT_REL"
fi

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

set -a
source "$PANEL_ENV_FILE"
set +a

PANEL_BIND_HOST="${POLY_PANEL_HOST:-127.0.0.1}"
PANEL_PORT="${POLY_PANEL_PORT:-8787}"
PANEL_AUTH_REQUIRED="${POLY_AUTH_REQUIRED:-1}"
PANEL_AUTH_DEFAULT_USERNAME="${POLY_AUTH_DEFAULT_USERNAME:-admin}"
PANEL_AUTH_DEFAULT_PASSWORD="${POLY_AUTH_DEFAULT_PASSWORD:-admin}"
PANEL_SESSION_SECRET="${POLY_SESSION_SECRET:-}"
PANEL_REVERSE_PROXY_MODE="${POLY_REVERSE_PROXY_MODE:-}"
if [[ -z "$PANEL_SESSION_SECRET" ]]; then
  PANEL_SESSION_SECRET="$(openssl rand -hex 32)"
fi

legacy_instance_root="${POLY_INSTANCE_ROOT:-}"
legacy_source_mode=0
if [[ -z "$legacy_instance_root" || "$legacy_instance_root" == "$APP_DIR" ]]; then
  INSTANCE_DIR="${INSTANCE_DIR:-/var/lib/polymarket_frontend}"
  legacy_source_mode=1
else
  INSTANCE_DIR="$legacy_instance_root"
fi

step "Ensure instance overlay files exist"
mkdir -p \
  "$INSTANCE_DIR/$V2_DIR_REL/copytrade" \
  "$INSTANCE_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/data" \
  "$INSTANCE_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config" \
  "$INSTANCE_DIR/$V3_DIR_REL/copytrade" \
  "$INSTANCE_DIR/panel"
chown -R "$APP_USER:$APP_USER" "$INSTANCE_DIR"

copy_if_missing() {
  local src="$1"
  local dst="$2"
  if [[ -f "$src" && ! -f "$dst" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chown "$APP_USER:$APP_USER" "$dst"
  fi
}

copy_runtime_if_missing() {
  local rel="$1"
  local src="$APP_DIR/$rel"
  local dst="$INSTANCE_DIR/$rel"
  if [[ -f "$src" && ! -f "$dst" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chown "$APP_USER:$APP_USER" "$dst"
  fi
}

copy_if_missing "$APP_DIR/$V2_ACCOUNT_REL" "$INSTANCE_DIR/$V2_ACCOUNT_REL"
copy_if_missing "$APP_DIR/$V2_COPYTRADE_CONFIG_REL" "$INSTANCE_DIR/$V2_COPYTRADE_CONFIG_REL"
copy_if_missing "$APP_DIR/$V2_GLOBAL_CONFIG_REL" "$INSTANCE_DIR/$V2_GLOBAL_CONFIG_REL"
copy_if_missing "$APP_DIR/$V2_RUN_PARAMS_REL" "$INSTANCE_DIR/$V2_RUN_PARAMS_REL"
copy_if_missing "$APP_DIR/$V2_STRATEGY_DEFAULTS_REL" "$INSTANCE_DIR/$V2_STRATEGY_DEFAULTS_REL"
copy_if_missing "$APP_DIR/$V2_TRADING_YAML_REL" "$INSTANCE_DIR/$V2_TRADING_YAML_REL"
copy_if_missing "$APP_DIR/$V3_COPYTRADE_CONFIG_REL" "$INSTANCE_DIR/$V3_COPYTRADE_CONFIG_REL"
if [[ ! -f "$INSTANCE_DIR/$V3_ACCOUNTS_REL" ]]; then
  mkdir -p "$(dirname "$INSTANCE_DIR/$V3_ACCOUNTS_REL")"
  printf '%s\n' "$V3_DEFAULT_ACCOUNT_JSON" > "$INSTANCE_DIR/$V3_ACCOUNTS_REL"
  chown "$APP_USER:$APP_USER" "$INSTANCE_DIR/$V3_ACCOUNTS_REL"
fi
copy_runtime_if_missing "$PANEL_AUTH_REL"
copy_runtime_if_missing "$V2_STATUS_REL"
copy_runtime_if_missing "$V2_TOKENS_REL"
copy_runtime_if_missing "$V2_COPYTRADE_STATE_REL"

if [[ "$INSTANCE_DIR" != "$APP_DIR" ]]; then
  step "Clean tracked config files from git worktree"
  run_as_app git -C "$APP_DIR" restore -- \
    "$V2_ACCOUNT_REL" \
    "$V2_COPYTRADE_CONFIG_REL" \
    "$V2_GLOBAL_CONFIG_REL" \
    "$V2_RUN_PARAMS_REL" \
    "$V2_STRATEGY_DEFAULTS_REL" \
    "$V2_TRADING_YAML_REL" || true
fi

step "Persist panel runtime env"
cat > "$PANEL_ENV_FILE" <<EOF
POLY_APP_ROOT=$APP_DIR
POLY_INSTANCE_ROOT=$INSTANCE_DIR
POLY_FORCE_SOURCE_SERVICES=1

POLY_PANEL_HOST=$PANEL_BIND_HOST
POLY_PANEL_PORT=$PANEL_PORT

POLY_AUTH_REQUIRED=$PANEL_AUTH_REQUIRED
POLY_AUTH_DEFAULT_USERNAME=$PANEL_AUTH_DEFAULT_USERNAME
POLY_AUTH_DEFAULT_PASSWORD=$PANEL_AUTH_DEFAULT_PASSWORD
POLY_SESSION_SECRET=$PANEL_SESSION_SECRET
EOF

if [[ -n "$PANEL_REVERSE_PROXY_MODE" ]]; then
  printf 'POLY_REVERSE_PROXY_MODE=%s\n' "$PANEL_REVERSE_PROXY_MODE" >> "$PANEL_ENV_FILE"
else
  cat >> "$PANEL_ENV_FILE" <<'EOF'
# If nginx serves HTTPS, set to: https
# POLY_REVERSE_PROXY_MODE=https
EOF
fi

cat >> "$PANEL_ENV_FILE" <<'EOF'

PYTHONUTF8=1
PYTHONIOENCODING=utf-8
EOF
chmod 640 "$PANEL_ENV_FILE"
chown root:"$APP_USER" "$PANEL_ENV_FILE"

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

if [[ "$UPDATE_SERVICE_FILES" == "1" ]]; then
  step "Rewrite systemd service files to current layout"

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
ExecStart=$APP_DIR/.venv/bin/python server.py --host $PANEL_BIND_HOST --port $PANEL_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  fi

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
ExecStart=$APP_DIR/.venv/bin/python copytrade_run.py --config $INSTANCE_DIR/$V2_COPYTRADE_CONFIG_REL
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  fi

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
ExecStart=$APP_DIR/.venv/bin/python poly_maker_autorun.py --no-repl --global-config $INSTANCE_DIR/$V2_GLOBAL_CONFIG_REL --strategy-config $INSTANCE_DIR/$V2_STRATEGY_DEFAULTS_REL --run-config-template $INSTANCE_DIR/$V2_RUN_PARAMS_REL
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  fi

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
ExecStart=$APP_DIR/.venv/bin/python copytrade_run.py --config $INSTANCE_DIR/$V3_COPYTRADE_CONFIG_REL
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  fi

  # Ensure log symlinks exist
  step "Ensure log symlinks exist"
  mkdir -p "$APP_DIR/$V2_DIR_REL/copytrade/logs"
  mkdir -p "$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/logs/autorun"
  ln -sf "$APP_DIR/$V2_DIR_REL/copytrade/logs/copytrade_$(date +%Y%m%d).log" \
         "$APP_DIR/$V2_DIR_REL/copytrade/copytrade_systemd.log"
  latest_autorun_log="$(find "$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/logs/autorun" -maxdepth 1 -type f -name 'autorun_main_*.log' | sort | tail -n 1)"
  if [[ -n "$latest_autorun_log" ]]; then
    ln -sf "$latest_autorun_log" \
           "$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/autorun_systemd.log"
  fi
  echo "[OK] Refreshed log symlinks"
  
  # Ensure cron job for log links exists
  if [[ ! -f /etc/cron.daily/polymarket-log-links ]]; then
    cat > /etc/cron.daily/polymarket-log-links <<EOF
#!/bin/bash
# Update log symlinks daily
APP_DIR="$APP_DIR"
ln -sf "\$APP_DIR/$V2_DIR_REL/copytrade/logs/copytrade_\$(date +%Y%m%d).log" \
       "\$APP_DIR/$V2_DIR_REL/copytrade/copytrade_systemd.log" 2>/dev/null || true
latest_autorun_log="\$(find \"\$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/logs/autorun\" -maxdepth 1 -type f -name 'autorun_main_*.log' | sort | tail -n 1)"
if [[ -n "\$latest_autorun_log" ]]; then
  ln -sf "\$latest_autorun_log" \
         "\$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/autorun_systemd.log" 2>/dev/null || true
fi
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
