#!/usr/bin/env bash
# scripts/update.sh
# v0 field update: git pull + restart, with guardrails.
#
#   sudo bash scripts/update.sh              # update to latest master
#   sudo bash scripts/update.sh v0.12.0      # update to a specific tag
#
# Guardrails:
#   - Backs up config/ before touching anything
#   - Only reinstalls Python deps if requirements.txt changed
#   - Only rebuilds dashboard if package-lock.json changed
#   - Health-check after restart with explicit rollback instructions on failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REF="${1:-master}"
BACKUP_DIR="/var/backups/flemingo/$(date +%Y%m%d-%H%M%S)"

[[ "$EUID" -eq 0 ]] || { echo "ERROR: run as root — sudo bash scripts/update.sh"; exit 1; }

SERVICE_USER="${SUDO_USER:-$(whoami)}"
[[ "$SERVICE_USER" == "root" ]] && SERVICE_USER="$(stat -c '%U' "$REPO_ROOT")"

echo "============================================================"
echo " Flemingo — field update to $REF"
echo "============================================================"

echo "[1/6] Backing up config/ to $BACKUP_DIR..."
mkdir -p "$BACKUP_DIR"
cp -a "$REPO_ROOT/config" "$BACKUP_DIR/"
echo "      Done."

BEFORE_REQ=$(sha256sum "$REPO_ROOT/requirements.txt" 2>/dev/null | cut -d' ' -f1)
BEFORE_PKG=$(sha256sum "$REPO_ROOT/dashboard/package-lock.json" 2>/dev/null | cut -d' ' -f1)

echo "[2/6] Fetching and checking out $REF..."
cd "$REPO_ROOT"
sudo -u "$SERVICE_USER" git fetch --all --tags -q
sudo -u "$SERVICE_USER" git checkout "$REF" -q
echo "      Now on $(git rev-parse --short HEAD)."

AFTER_REQ=$(sha256sum "$REPO_ROOT/requirements.txt" 2>/dev/null | cut -d' ' -f1)
AFTER_PKG=$(sha256sum "$REPO_ROOT/dashboard/package-lock.json" 2>/dev/null | cut -d' ' -f1)

if [[ "$BEFORE_REQ" != "$AFTER_REQ" ]]; then
    echo "[3/6] requirements.txt changed — reinstalling Python deps..."
    sudo -u "$SERVICE_USER" "$REPO_ROOT/venv/bin/pip" install -r "$REPO_ROOT/requirements.txt" -q
else
    echo "[3/6] requirements.txt unchanged — skipping."
fi

if [[ "$BEFORE_PKG" != "$AFTER_PKG" ]]; then
    echo "[4/6] Dashboard deps changed — rebuilding..."
    sudo -u "$SERVICE_USER" bash -c "cd '$REPO_ROOT/dashboard' && npm ci --silent && npm run build --silent"
else
    echo "[4/6] Dashboard unchanged — skipping."
fi

echo "[5/6] Restarting service..."
systemctl restart flemingo
sleep 3

echo "[6/6] Health check..."
if curl -sf http://localhost:5000/api/health > /dev/null; then
    echo ""
    echo "============================================================"
    echo " Updated to $REF."
    echo "============================================================"
else
    echo ""
    echo "============================================================"
    echo " Health check FAILED — Flask did not respond."
    echo ""
    echo " Config backup: $BACKUP_DIR"
    echo ""
    echo " Roll back:"
    echo "   cd $REPO_ROOT"
    echo "   git checkout <previous-ref>"
    echo "   systemctl restart flemingo"
    echo "============================================================"
    exit 1
fi
