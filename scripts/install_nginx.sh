#!/usr/bin/env bash
# scripts/install_nginx.sh
# Install nginx, install the flemingo TLS config, and enable the service.
#
# Run once on the Purple Pi after first boot:
#   sudo bash scripts/install_nginx.sh
#
# What it does:
#   1. apt-get install nginx (if not already present)
#   2. Run gen_selfsigned_cert.sh if certs/flemingo.crt doesn't exist yet
#   3. Copy certs to /etc/flemingo/certs/ (0600 key)
#   4. Install nginx/flemingo.conf → /etc/nginx/sites-available/flemingo
#   5. Disable the default nginx site (avoids port 80/443 conflict)
#   6. Enable flemingo site: symlink sites-available → sites-enabled
#   7. nginx -t (config test — aborts if invalid)
#   8. systemctl enable + restart nginx
#
# After any change to nginx/flemingo.conf:
#   sudo nginx -t && sudo systemctl reload nginx
#
# Flask still runs on port 5000 on loopback.
# From outside the box, use https://<device-ip> (port 443).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NGINX_CONF="$REPO_ROOT/nginx/flemingo.conf"
CERT_DIR="$REPO_ROOT/certs"
SYSTEM_CERT_DIR="/etc/flemingo/certs"

# ─── Must run as root ────────────────────────────────────────────────
if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: this script must be run as root."
    echo "  sudo bash scripts/install_nginx.sh"
    exit 1
fi

echo "============================================================"
echo " PurpleIO / Flemingo — nginx TLS installer"
echo "============================================================"

# ─── 1. Install nginx ────────────────────────────────────────────────
if command -v nginx &>/dev/null; then
    echo "[1/8] nginx already installed: $(nginx -v 2>&1)"
else
    echo "[1/8] Installing nginx..."
    apt-get update -qq
    apt-get install -y -qq nginx
    echo "      nginx installed."
fi

# ─── 2. Generate self-signed cert if missing ────────────────────────
if [[ -f "$CERT_DIR/flemingo.crt" && -f "$CERT_DIR/flemingo.key" ]]; then
    echo "[2/8] Certificate already exists at $CERT_DIR — skipping generation."
else
    echo "[2/8] Generating self-signed certificate..."
    # Run as the original user (not root) so the repo-local cert files
    # are owned by the invoking user, not root.
    INVOKING_USER="${SUDO_USER:-$USER}"
    su -c "bash '$SCRIPT_DIR/gen_selfsigned_cert.sh'" "$INVOKING_USER"
fi

# ─── 3. Copy certs to system location ───────────────────────────────
echo "[3/8] Copying certs to $SYSTEM_CERT_DIR..."
mkdir -p "$SYSTEM_CERT_DIR"
cp "$CERT_DIR/flemingo.crt" "$SYSTEM_CERT_DIR/flemingo.crt"
cp "$CERT_DIR/flemingo.key" "$SYSTEM_CERT_DIR/flemingo.key"
chmod 644 "$SYSTEM_CERT_DIR/flemingo.crt"
chmod 600 "$SYSTEM_CERT_DIR/flemingo.key"
chown root:root "$SYSTEM_CERT_DIR/flemingo.key"
echo "      Certs installed."

# ─── 4. Install nginx config ────────────────────────────────────────
echo "[4/8] Installing nginx config..."
cp "$NGINX_CONF" /etc/nginx/sites-available/flemingo
echo "      Config installed at /etc/nginx/sites-available/flemingo"

# ─── 5. Disable default site ────────────────────────────────────────
echo "[5/8] Disabling default nginx site..."
if [[ -L /etc/nginx/sites-enabled/default ]]; then
    rm /etc/nginx/sites-enabled/default
    echo "      Default site disabled."
else
    echo "      Default site was not enabled."
fi

# ─── 6. Enable flemingo site ────────────────────────────────────────
echo "[6/8] Enabling flemingo site..."
ln -sf /etc/nginx/sites-available/flemingo /etc/nginx/sites-enabled/flemingo
echo "      Symlink created: sites-enabled/flemingo"

# ─── 7. Test config ─────────────────────────────────────────────────
echo "[7/8] Testing nginx configuration..."
if nginx -t 2>&1; then
    echo "      Config test passed."
else
    echo "ERROR: nginx config test failed. Check the output above."
    echo "       No changes made to the running nginx service."
    exit 1
fi

# ─── 8. Enable and restart ──────────────────────────────────────────
echo "[8/8] Enabling and restarting nginx..."
systemctl enable nginx
systemctl restart nginx
echo "      nginx enabled and restarted."

# ─── Summary ─────────────────────────────────────────────────────────
DEVICE_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "============================================================"
echo " Done. PurpleIO is now accessible at:"
echo ""
echo "   https://${DEVICE_IP}         (HTTPS — browsers will warn"
echo "                                  about self-signed cert)"
echo "   http://${DEVICE_IP}          (redirects to HTTPS)"
echo ""
echo " Flask still runs on port 5000 on loopback only."
echo " nginx terminates TLS and proxies to it."
echo ""
echo " WebSocket: wss://${DEVICE_IP}/socket.io/"
echo ""
echo " To reload after nginx/flemingo.conf changes:"
echo "   sudo nginx -t && sudo systemctl reload nginx"
echo "============================================================"