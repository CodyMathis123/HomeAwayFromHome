#!/usr/bin/env bash
# Explicit certificate-only production HTTPS migration. NEVER part of routine deploy.
set -euo pipefail
usage() {
  echo "Usage: bash scripts/deploy-dns01-production.sh --plan|--apply|--status|--rollback" >&2
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
(($# == 1)) || { usage; exit 2; }
mode="$1"
case "$mode" in --plan|--apply|--status|--rollback) ;; *) usage; exit 2 ;; esac
[[ "$EUID" -ne 0 ]] || die "Run as normal user (script invokes sudo only as needed)."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
for command in git python3 sudo docker tar; do
  command -v "$command" >/dev/null || die "Missing command: $command"
done
[[ "$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)" == "Production" ]] ||
  die "Only Production branch is permitted."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  die "Git working tree has changes."
[[ -f .env ]] || die "Missing existing HomeAway .env"
files=(-f compose.yaml -f compose.control.yaml -f compose.accounts.yaml -f compose.computers.yaml)
profiles=(--profile control --profile accounts --profile metrics)
base=(sudo docker compose "${files[@]}" "${profiles[@]}")
prod=(sudo docker compose "${files[@]}" -f compose.dns01-production.yaml "${profiles[@]}")

if [[ "$mode" == --status ]]; then
  "${base[@]}" ps --all gateway
  "${base[@]}" logs --no-color --tail=200 gateway |
    python3 scripts/summarize_dns01_certificate_logs.py
  exit 0
fi
if [[ "$mode" == --rollback ]]; then
  "${base[@]}" config -q || die "Baseline Compose invalid; no rollback performed."
  echo "Recreating ONLY the original gateway (other services untouched)."
  "${base[@]}" up -d --no-deps --no-build --force-recreate gateway ||
    die "Rollback failed. Investigate local gateway logs immediately."
  [[ -n "$("${base[@]}" ps --status running --quiet gateway)" ]] ||
    die "Gateway not running after rollback."
  echo "Original gateway is running. Verify .home.arpa TOTP and controls in browser."
  exit 0
fi

secret=secrets/control/cloudflare_dns01_token
sudo test -s "$secret" || die "Root-owned Cloudflare token is missing or empty."
[[ "$(sudo stat -c '%u' "$secret")" == 0 ]] || die "Token must be root-owned."
perms="$(sudo stat -c '%a' "$secret")"
[[ "$perms" == 600 || "$perms" == 400 ]] || die "Token must be mode 600/400."
[[ -n "$("${base[@]}" ps --status running --quiet gateway)" ]] ||
  die "The live gateway must already be running."
sudo docker image inspect homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 >/dev/null ||
  die "Custom Caddy image missing; first complete isolated staging build."

# Temporarily store resolved configs in a private mode-0700 directory.
umask 077
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
"${base[@]}" config --format json > "$tmp/base.json" ||
  die "Existing Compose invalid."
"${prod[@]}" config --format json > "$tmp/production.json" ||
  die "Production opt-in Compose invalid."
python3 scripts/check_dns01_production_resolved.py \
  "$tmp/base.json" "$tmp/production.json" ||
  die "Unexpected gateway/stack changes. Refusing restart."
echo "Verified: only gateway image, entrypoint, Caddyfile, and scoped token change."
echo "Existing .home.arpa routing is preserved; new names return HTTP 503 placeholders."
if [[ "$mode" == --plan ]]; then
  echo "PLAN ONLY; no snapshot, DNS change or service restart."
  exit 0
fi

# Operator-triggered live gateway restart. Snapshot Caddy CA/key material first.
sudo test -d data/control/caddy || die "Missing existing Caddy TLS state."
sudo test -d data/control/caddy-config || die "Missing existing Caddy config state."
sudo install -d -m 700 backups/phase3-https
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="backups/phase3-https/caddy-before-dns01-$stamp.tar.gz"
sudo tar -czf "$archive" \
  control/gateway/Caddyfile data/control/caddy data/control/caddy-config ||
  die "Caddy snapshot failed. Gateway left untouched."
sudo chmod 600 "$archive"
echo "Protected Caddy-only snapshot: $archive (contains PRIVATE KEY material)."
echo "Replacing ONLY gateway with production DNS-01 Caddy (brief HTTPS interruption)."
"${prod[@]}" up -d --no-deps --no-build --force-recreate gateway ||
  die "Gateway restart failed; run this script --rollback immediately."
sleep 2
[[ -n "$("${prod[@]}" ps --status running --quiet gateway)" ]] ||
  die "Gateway exited; run --rollback immediately."
echo "Gateway running; production cert issuance NOT verified. Run --status."
echo "Check old .home.arpa login immediately; rollback if any regression."
