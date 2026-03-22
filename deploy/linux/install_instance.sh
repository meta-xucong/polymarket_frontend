#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 5 ]]; then
  echo "Usage: $0 <app_root> <instance_root> <linux_user> <panel_port> <python_bin>"
  echo "Example: $0 /opt/polyapp/current /opt/polyapp/instances/user01 trader 8787 /usr/bin/python3"
  exit 1
fi

APP_ROOT="$1"
INSTANCE_ROOT="$2"
LINUX_USER="$3"
PANEL_PORT="$4"
PYTHON_BIN="$5"

PANEL_DIR="$APP_ROOT/POLYMARKET_MAKER_copytrade_v2/panel"
RUNTIME_REQUIREMENTS="$APP_ROOT/deploy/linux/requirements-runtime.txt"
VENV_DIR="${APP_ROOT%/}/venv"
SERVICE_NAME="polymarket-panel-$(basename "$INSTANCE_ROOT")"
ENV_FILE="/etc/default/${SERVICE_NAME}.env"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "[1/6] Preparing instance directories..."
sudo mkdir -p "$INSTANCE_ROOT"/{v2,smartmoney,logs,run}
sudo chown -R "$LINUX_USER":"$LINUX_USER" "$INSTANCE_ROOT"

echo "[2/7] Preparing Python environment..."
if [[ ! -d "$VENV_DIR" ]]; then
  sudo "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
sudo "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel
if [[ -f "$RUNTIME_REQUIREMENTS" ]]; then
  sudo "$VENV_DIR/bin/pip" install -r "$RUNTIME_REQUIREMENTS"
fi

echo "[3/7] Writing env file template..."
sudo tee "$ENV_FILE" >/dev/null <<EOF
POLY_APP_ROOT=$APP_ROOT
POLY_INSTANCE_ROOT=$INSTANCE_ROOT
POLY_V2_ROOT=$APP_ROOT/POLYMARKET_MAKER_copytrade_v2
POLY_V3_ROOT=$APP_ROOT/POLY_SMARTMONEY/copytrade_v3_muti
POLY_PANEL_HOST=127.0.0.1
POLY_PANEL_PORT=$PANEL_PORT
POLY_AUTH_REQUIRED=1
POLY_AUTH_DEFAULT_USERNAME=admin
POLY_AUTH_DEFAULT_PASSWORD=admin
POLY_SESSION_SECRET=change_me_to_random_secret
POLY_SESSION_TTL_SEC=43200
POLY_LOG_ROOT=$INSTANCE_ROOT/logs
POLY_RUN_ROOT=$INSTANCE_ROOT/run
POLY_REVERSE_PROXY_MODE=1
POLY_FORCE_SOURCE_SERVICES=1
EOF
sudo chmod 600 "$ENV_FILE"

echo "[4/7] Writing systemd unit..."
sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=Polymarket Integrated Panel ($(basename "$INSTANCE_ROOT"))
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$LINUX_USER
WorkingDirectory=$PANEL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python server.py --host \$POLY_PANEL_HOST --port \$POLY_PANEL_PORT
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

echo "[5/7] Reloading systemd..."
sudo systemctl daemon-reload

echo "[6/7] Enabling and starting service..."
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "[7/7] Current status:"
sudo systemctl status "$SERVICE_NAME" --no-pager -l || true

echo "Done."
echo "Edit credentials in: $ENV_FILE"
echo "Service name: $SERVICE_NAME"
