#!/usr/bin/env bash
# scripts/setup.sh
# One-shot installer for Flemingo on a fresh Purple Pi OH2.
#   sudo bash scripts/setup.sh
#
# What it does:
#   1. apt-get install system deps (venv, libgpiod2, build tools, node)
#   2. Create/reuse a Python venv at $REPO_ROOT/venv
#   3. pip install -r requirements.txt (pinned)
#   4. Add the invoking user to gpio/spi/dialout groups
#   5. npm ci && npm run build for the dashboard
#   6. Install + enable the flemingo systemd service
#   7. Optionally run install_nginx.sh
#
# Safe to re-run — every step is idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$REPO_ROOT/venv"
SERVICE_USER="${SUDO_USER:-$(whoami)}"

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: run as root — sudo bash scripts/setup.sh"
    exit 1
fi

echo "============================================================"
echo " Flemingo — one-shot setup for Purple Pi OH2"
echo " Service user: $SERVICE_USER"
echo "============================================================"

echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip libgpiod2 build-essential nodejs npm
echo "      Done."

echo "[2/7] Setting up Python venv at $VENV_DIR..."
if [[ -d "$VENV_DIR" ]]; then
    echo "      venv already exists — skipping."
else
    sudo -u "$SERVICE_USER" python3 -m venv "$VENV_DIR"
    echo "      venv created."
fi

echo "[3/7] Installing pinned Python dependencies..."
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --upgrade pip -q
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -r "$REPO_ROOT/requirements.txt" -q
echo "      Done."

echo "[4/7] Adding $SERVICE_USER to gpio/spi/dialout groups..."
usermod -aG gpio,spi,dialout "$SERVICE_USER" || true
echo "      Done (log out/in for group changes to take effect if not using systemd)."

echo "[5/7] Building dashboard..."
if [[ -d "$REPO_ROOT/dashboard/node_modules" ]]; then
    echo "      node_modules exist — running npm ci..."
else
    echo "      Fresh install — running npm ci (this may take a minute)..."
fi
sudo -u "$SERVICE_USER" bash -c "cd '$REPO_ROOT/dashboard' && npm ci --silent && npm run build --silent"
echo "      Dashboard built."

echo "[6/7] Installing systemd service..."
sed -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
    -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
    -e "s|__VENV_DIR__|$VENV_DIR|g" \
    "$REPO_ROOT/deploy/flemingo.service.template" > /etc/systemd/system/flemingo.service
systemctl daemon-reload
systemctl enable flemingo
systemctl restart flemingo
echo "      flemingo.service installed, enabled, and restarted."

echo ""
echo "[7/7] Install nginx + TLS reverse proxy now?"
echo "      (speeds: nginx proxies HTTPS on 443, Flask runs on 5000 loopback)"
read -r -t 15 -p "      Install? [y/N] " REPLY || REPLY="n"
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    bash "$SCRIPT_DIR/install_nginx.sh"
else
    echo "      Skipped — run 'sudo bash scripts/install_nginx.sh' later."
fi

echo ""
echo "============================================================"
echo " Done."
echo ""
echo "   systemctl status flemingo          — check the service"
echo "   journalctl -u flemingo -f          — follow live logs"
echo "   http://$(hostname -I | awk '{print $1}'):5000   — dashboard"
echo "============================================================"
