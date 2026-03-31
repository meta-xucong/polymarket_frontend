#!/usr/bin/env bash
set -euo pipefail

# =============================================
# Polymarket Frontend one-click install (Debian)
# =============================================
# Run as root:
#   sudo bash debian_oneclick_install.sh
#
# Optional env overrides before running:
#   APP_USER=polymarket
#   APP_DIR=/opt/polymarket_frontend
#   REPO_URL=https://github.com/meta-xucong/polymarket_frontend.git
#   REPO_BRANCH=main
#   PANEL_PORT=8787
#   ENABLE_NGINX=1
#

APP_USER="${APP_USER:-polymarket}"
APP_DIR="${APP_DIR:-/opt/polymarket_frontend}"
INSTANCE_DIR="${INSTANCE_DIR:-/var/lib/polymarket_frontend}"
REPO_URL="${REPO_URL:-https://github.com/meta-xucong/polymarket_frontend.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
PANEL_PORT="${PANEL_PORT:-8787}"
ENABLE_NGINX="${ENABLE_NGINX:-1}"
PANEL_BIND_HOST="${PANEL_BIND_HOST:-}"

if [[ -z "$PANEL_BIND_HOST" ]]; then
  if [[ "$ENABLE_NGINX" == "1" ]]; then
    PANEL_BIND_HOST="127.0.0.1"
  else
    PANEL_BIND_HOST="0.0.0.0"
  fi
fi

PANEL_DIR_REL="POLYMARKET_MAKER_copytrade_v2/panel"
V2_DIR_REL="POLYMARKET_MAKER_copytrade_v2"
V3_DIR_REL="POLY_SMARTMONEY/copytrade_v3_muti"
V2_COPYTRADE_CONFIG_REL="$V2_DIR_REL/copytrade/copytrade_config.json"
V2_ACCOUNT_TEMPLATE_REL="$V2_DIR_REL/account.template.json"
V2_ACCOUNT_REL="$V2_DIR_REL/account.json"
V2_GLOBAL_CONFIG_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/global_config.json"
V2_RUN_PARAMS_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/run_params.json"
V2_STRATEGY_DEFAULTS_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/strategy_defaults.json"
V2_TRADING_YAML_REL="$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config/trading.yaml"
V3_COPYTRADE_CONFIG_REL="$V3_DIR_REL/copytrade/copytrade_config.json"
V3_ACCOUNTS_REL="$V3_DIR_REL/accounts.json"
V3_DEFAULT_ACCOUNT_JSON='{"accounts":[{"name":"Account 1","enabled":true,"private_key":"","funder":"","api_key":"","api_secret":"","api_passphrase":"","data_address":""}]}'

POLY_CONF_DIR="/etc/polymarket"
PANEL_ENV_FILE="$POLY_CONF_DIR/panel.env"
TRADING_ENV_FILE="$POLY_CONF_DIR/trading.env"

PANEL_SERVICE="/etc/systemd/system/polymarket-panel.service"
COPYTRADE_SERVICE="/etc/systemd/system/polymaker-copytrade.service"
AUTORUN_SERVICE="/etc/systemd/system/polymaker-autorun.service"
V3MULTI_SERVICE="/etc/systemd/system/copytrade-v3-multi.service"
NGINX_SITE="/etc/nginx/sites-available/polymarket-panel.conf"
SUDOERS_FILE="/etc/sudoers.d/polymarket-panel-systemctl"

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

if [[ "$EUID" -ne 0 ]]; then
  echo "[ERROR] Please run as root: sudo bash $0"
  exit 1
fi

step "Update system packages"
export DEBIAN_FRONTEND=noninteractive
apt update
apt upgrade -y

step "Install required software"
apt install -y \
  git curl ca-certificates \
  openssl \
  sudo \
  python3 python3-venv python3-pip python3-dev \
  build-essential libssl-dev libffi-dev \
  nginx

step "Create app user and directories"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$APP_USER"
fi
mkdir -p "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
mkdir -p "$INSTANCE_DIR"
chown -R "$APP_USER:$APP_USER" "$INSTANCE_DIR"
mkdir -p "$POLY_CONF_DIR"

step "Clone or update repository"
if [[ -d "$APP_DIR/.git" ]]; then
  run_as_app git -C "$APP_DIR" fetch --all --tags
  run_as_app git -C "$APP_DIR" checkout "$REPO_BRANCH"
  run_as_app git -C "$APP_DIR" pull --ff-only origin "$REPO_BRANCH"
else
  run_as_app git clone --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
fi

step "Create Python virtual environment"
if [[ ! -d "$APP_DIR/.venv" ]]; then
  run_as_app python3 -m venv "$APP_DIR/.venv"
fi

step "Install Python dependencies"
run_as_app "$APP_DIR/.venv/bin/pip" install --upgrade pip setuptools wheel
run_as_app "$APP_DIR/.venv/bin/pip" install \
  requests \
  pyyaml \
  websocket-client \
  "eth-hash[pycryptodome]" \
  pycryptodome

# Some upstream packages have different pip names across mirrors/versions.
if ! run_as_app "$APP_DIR/.venv/bin/pip" install py-clob-client; then
  run_as_app "$APP_DIR/.venv/bin/pip" install py_clob_client
fi
if ! run_as_app "$APP_DIR/.venv/bin/pip" install py-order-utils; then
  run_as_app "$APP_DIR/.venv/bin/pip" install py_order_utils
fi
if ! run_as_app "$APP_DIR/.venv/bin/pip" install poly-eip712-structs; then
  run_as_app "$APP_DIR/.venv/bin/pip" install poly_eip712_structs
fi

step "Sanity check core imports"
run_as_app "$APP_DIR/.venv/bin/python" - <<'PY'
import requests, yaml, websocket
from py_clob_client.client import ClobClient
print("[OK] Python deps are importable")
PY

step "Prepare runtime config files"
cat > "$PANEL_ENV_FILE" <<EOF
POLY_APP_ROOT=$APP_DIR
POLY_INSTANCE_ROOT=$INSTANCE_DIR
POLY_FORCE_SOURCE_SERVICES=1

POLY_PANEL_HOST=$PANEL_BIND_HOST
POLY_PANEL_PORT=$PANEL_PORT

POLY_AUTH_REQUIRED=1
POLY_AUTH_DEFAULT_USERNAME=admin
POLY_AUTH_DEFAULT_PASSWORD=admin
POLY_SESSION_SECRET=$(openssl rand -hex 32)

# If nginx serves HTTPS, set to: https
# POLY_REVERSE_PROXY_MODE=https

PYTHONUTF8=1
PYTHONIOENCODING=utf-8
EOF

# Trading env with full configuration keys (will be updated by panel)
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

chmod 640 "$PANEL_ENV_FILE" "$TRADING_ENV_FILE"
chown root:"$APP_USER" "$PANEL_ENV_FILE" "$TRADING_ENV_FILE"

step "Grant panel user limited systemd control"
SYSTEMCTL_BIN="$(command -v systemctl)"
if [[ -z "$SYSTEMCTL_BIN" ]]; then
  echo "[ERROR] systemctl not found"
  exit 1
fi
cat > "$SUDOERS_FILE" <<EOF
$APP_USER ALL=(root) NOPASSWD: \
  $SYSTEMCTL_BIN show polymaker-copytrade.service --property LoadState --value, \
  $SYSTEMCTL_BIN is-active polymaker-copytrade.service, \
  $SYSTEMCTL_BIN start polymaker-copytrade.service, \
  $SYSTEMCTL_BIN stop polymaker-copytrade.service, \
  $SYSTEMCTL_BIN restart polymaker-copytrade.service, \
  $SYSTEMCTL_BIN show polymaker-autorun.service --property LoadState --value, \
  $SYSTEMCTL_BIN is-active polymaker-autorun.service, \
  $SYSTEMCTL_BIN start polymaker-autorun.service, \
  $SYSTEMCTL_BIN stop polymaker-autorun.service, \
  $SYSTEMCTL_BIN restart polymaker-autorun.service, \
  $SYSTEMCTL_BIN show copytrade-v3-multi.service --property LoadState --value, \
  $SYSTEMCTL_BIN is-active copytrade-v3-multi.service, \
  $SYSTEMCTL_BIN start copytrade-v3-multi.service, \
  $SYSTEMCTL_BIN stop copytrade-v3-multi.service, \
  $SYSTEMCTL_BIN restart copytrade-v3-multi.service
Defaults:$APP_USER !requiretty
EOF
chmod 440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"

step "Initialize account.json template"
mkdir -p \
  "$INSTANCE_DIR/$V2_DIR_REL/copytrade" \
  "$INSTANCE_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/POLYMARKET_MAKER/config" \
  "$INSTANCE_DIR/$V3_DIR_REL/copytrade"

copy_if_missing() {
  local src="$1"
  local dst="$2"
  if [[ -f "$src" && ! -f "$dst" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chown "$APP_USER:$APP_USER" "$dst"
  fi
}

copy_if_missing "$APP_DIR/$V2_ACCOUNT_TEMPLATE_REL" "$INSTANCE_DIR/$V2_ACCOUNT_REL"
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

step "Reset panel auth state (force first-login password change)"
rm -f "$INSTANCE_DIR/panel/auth.json" || true

step "Create systemd services"
cat > "$PANEL_SERVICE" <<EOF
[Unit]
Description=Polymarket Panel Backend
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR/$PANEL_DIR_REL
EnvironmentFile=$PANEL_ENV_FILE
EnvironmentFile=$TRADING_ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python server.py --host $PANEL_BIND_HOST --port $PANEL_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

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

step "Create log symlinks for panel"
mkdir -p "$APP_DIR/$V2_DIR_REL/copytrade/logs"
ln -sf "$APP_DIR/$V2_DIR_REL/copytrade/logs/copytrade_$(date +%Y%m%d).log" \
       "$APP_DIR/$V2_DIR_REL/copytrade/copytrade_systemd.log"
mkdir -p "$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/logs/autorun"
latest_autorun_log="$(find "$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/logs/autorun" -maxdepth 1 -type f -name 'autorun_main_*.log' | sort | tail -n 1)"
if [[ -n "$latest_autorun_log" ]]; then
  ln -sf "$latest_autorun_log" \
         "$APP_DIR/$V2_DIR_REL/POLYMARKET_MAKER_AUTO/autorun_systemd.log"
fi

# Setup cron for daily log link update
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

step "Enable panel service"
systemctl daemon-reload
systemctl enable --now polymarket-panel.service

# Trading services are installed but not auto-enabled by default.
# Use the panel buttons (recommended) or manually enable:
#   systemctl enable --now polymaker-copytrade.service
#   systemctl enable --now polymaker-autorun.service
#   systemctl enable --now copytrade-v3-multi.service

if [[ "$ENABLE_NGINX" == "1" ]]; then
  step "Configure nginx reverse proxy"
  cat > "$NGINX_SITE" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:$PANEL_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

  rm -f /etc/nginx/sites-enabled/default
  ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/polymarket-panel.conf
  nginx -t
  systemctl enable --now nginx
  systemctl restart nginx
fi

step "Deployment completed"
SERVER_IP="$(hostname -I | awk '{print $1}')"
if [[ "$ENABLE_NGINX" == "1" ]]; then
  PANEL_URL="http://$SERVER_IP/"
else
  PANEL_URL="http://$SERVER_IP:$PANEL_PORT/"
fi

cat <<EOF

[OK] Polymarket panel is deployed.

Access URL:
  $PANEL_URL

First login:
  username: admin
  password: admin

After login:
  1) You will be forced to change admin password.
  2) Go to Account Settings and fill your wallet/private key config.
  3) Save settings.
  4) Start services from panel (copytrade / autorun) as needed.

Useful commands:
  systemctl status polymarket-panel --no-pager -l
  journalctl -u polymarket-panel -f
  systemctl status polymaker-copytrade --no-pager -l
  systemctl status polymaker-autorun --no-pager -l

EOF
