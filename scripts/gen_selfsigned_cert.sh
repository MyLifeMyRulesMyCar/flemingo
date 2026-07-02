#!/usr/bin/env bash
# scripts/gen_selfsigned_cert.sh
# Generate a self-signed TLS certificate for LAN deployment.
#
# Output:
#   certs/flemingo.crt   — certificate (safe to distribute / add to browser trust store)
#   certs/flemingo.key   — private key  (0600, never commit, gitignored)
#
# Validity: 10 years (LAN device, no CA rotation pressure).
# Key size: RSA-2048 (fast keygen + handshake on ARM Cortex-A55).
#
# Usage:
#   bash scripts/gen_selfsigned_cert.sh
#   # or with custom CN (shown in browser cert viewer):
#   CN=flemingo-edge-001 bash scripts/gen_selfsigned_cert.sh
#
# After generation, install_nginx.sh will copy the cert to
# /etc/flemingo/certs/ where nginx reads it from.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$REPO_ROOT/certs"
CERT_FILE="$CERT_DIR/flemingo.crt"
KEY_FILE="$CERT_DIR/flemingo.key"

# Allow CN override via environment variable (useful for fleet naming)
CN="${CN:-flemingo-edge}"

# ─── Check dependency ────────────────────────────────────────────────
if ! command -v openssl &>/dev/null; then
    echo "ERROR: openssl not found. Install with: sudo apt-get install -y openssl"
    exit 1
fi

# ─── Create output directory ─────────────────────────────────────────
mkdir -p "$CERT_DIR"

# ─── Warn if overwriting ─────────────────────────────────────────────
if [[ -f "$CERT_FILE" || -f "$KEY_FILE" ]]; then
    echo "WARNING: existing cert/key files will be overwritten."
    echo "  $CERT_FILE"
    echo "  $KEY_FILE"
    read -r -p "Continue? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

# ─── Generate ────────────────────────────────────────────────────────
echo "Generating self-signed certificate..."
echo "  Common Name: $CN"
echo "  Validity:    10 years"
echo "  Key:         RSA-2048"

openssl req \
    -x509 \
    -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out    "$CERT_FILE" \
    -days   3650 \
    -nodes \
    -subj   "/C=LK/ST=Western/L=Colombo/O=Elephantronics/OU=EdgeForce/CN=${CN}" \
    -addext "subjectAltName=IP:127.0.0.1,DNS:localhost,DNS:${CN}" \
    2>/dev/null

# ─── Secure the private key ──────────────────────────────────────────
chmod 600 "$KEY_FILE"
chmod 644 "$CERT_FILE"

echo ""
echo "Done:"
echo "  Certificate: $CERT_FILE"
echo "  Private key: $KEY_FILE  (mode 0600)"
echo ""
echo "Next step: sudo bash scripts/install_nginx.sh"
echo ""
echo "Browser trust: to silence the 'not trusted' warning, import"
echo "  $CERT_FILE"
echo "  into your browser's certificate store as a trusted CA,"
echo "  or add --insecure to curl / verify=False to requests."