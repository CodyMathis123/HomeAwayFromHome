#!/usr/bin/env bash
# Initial TLS identity for an opt-in, LAN-only telemetry receiver.
# Run with sudo to set restrictive root/65534-owned secret permissions.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
if [[ "$EUID" -ne 0 ]]; then
  echo "Use sudo bash scripts/init-telemetry.sh" >&2
  exit 1
fi
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
IP="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)"
mkdir -p secrets
if [[ -e secrets/telemetry-ca.key || -e secrets/telemetry/server.key ]]; then
  echo "Telemetry credentials exist: refusing to replace them. See docs/TELEMETRY.md." >&2
  exit 1
fi
temp="$(mktemp -d)"
trap 'rm -rf "$temp"' EXIT
openssl req -x509 -new -nodes -newkey rsa:3072 -sha256 -days 1825 \
  -subj "/CN=HomeAway telemetry local CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -keyout "$temp/ca.key" -out "$temp/ca.crt" >/dev/null 2>&1
openssl req -new -nodes -newkey rsa:3072 -sha256 \
  -subj "/CN=$IP" -keyout "$temp/server.key" -out "$temp/server.csr" >/dev/null 2>&1
cat > "$temp/server.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:$IP
EOF
openssl x509 -req -in "$temp/server.csr" -CA "$temp/ca.crt" -CAkey "$temp/ca.key" \
  -CAcreateserial -out "$temp/server.crt" -days 365 -sha256 -extfile "$temp/server.ext" >/dev/null 2>&1
openssl verify -CAfile "$temp/ca.crt" -verify_ip "$IP" "$temp/server.crt"
install -m 600 "$temp/ca.key" secrets/telemetry-ca.key
mkdir -p secrets/telemetry
install -m 600 "$temp/ca.crt" secrets/telemetry/ca.crt
install -m 600 "$temp/server.crt" secrets/telemetry/server.crt
install -m 600 "$temp/server.key" secrets/telemetry/server.key
openssl rand -hex 32 > secrets/telemetry/token
chmod 600 secrets/telemetry/token
# Container is uid 65534; CA private key is NEVER mounted in the receiver.
chown -R 65534:65534 secrets/telemetry
chmod 700 secrets/telemetry
echo "Created LAN TLS identity for $IP, 365-day server certificate and 256-bit bearer token."
echo "Copy only secrets/telemetry/ca.crt to the Windows collector."
echo "Never commit or expose the token, CA private key or server key."
