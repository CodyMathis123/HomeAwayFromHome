#!/usr/bin/env bash
# Explicit-only Let's Encrypt DNS-01 staging: independent Compose project.
# NEVER replaces or reconfigures the running HomeAway Caddy gateway.
set -euo pipefail
usage() { echo "Usage: bash scripts/deploy-dns01-staging.sh --plan|--apply|--status|--stop" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 ]] || { usage; exit 2; }
mode="$1"
case "$mode" in --plan|--apply|--status|--stop) ;; *) usage; exit 2 ;; esac
[[ "${EUID}" -ne 0 ]] || die "Run as normal user; script calls sudo for Docker only."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)" == "Production" ]] ||
  die "Only clean Production checkout is allowed; no detached previews."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  die "Working tree has changes; inspect git status first."
for command in git python3 sudo docker; do
  command -v "$command" >/dev/null || die "Missing $command."
done
secret=secrets/control/cloudflare_dns01_token
compose=(sudo docker compose -p homeaway-dns01-staging -f compose.dns01-staging.yaml)

if [[ "$mode" == --status ]]; then
  # Include stopped containers: normal ps hides early exec failures.
  "${compose[@]}" ps --all dns01-staging
  echo "Recent staging log events (never share any credentials):"
  "${compose[@]}" logs --tail=50 dns01-staging
  exit 0
fi
if [[ "$mode" == --stop ]]; then
  "${compose[@]}" down --remove-orphans
  echo "Stopped ONLY the standalone staging project; live gateway untouched."
  echo "Staging certificate files remain under data/control/dns01-staging/."
  exit 0
fi
# The gateway token is deliberately kept in a root-owned secret directory.
# Run metadata checks via sudo, matching the root-executed Compose command.
# Never read or print secret contents.
sudo test -s "$secret" ||
  die "Missing/empty token file: $secret. Create it with sudo tee; never paste it."
perms="$(sudo stat -c '%a' "$secret")" ||
  die "Cannot inspect root-owned secret permissions."
owner="$(sudo stat -c '%u' "$secret")" ||
  die "Cannot inspect secret ownership."
[[ "$owner" == "0" ]] ||
  die "Secret must be root-owned; run sudo chown root:root $secret."
[[ "$perms" == "600" || "$perms" == "400" ]] ||
  die "Secret must be owner-only; run sudo chmod 600 $secret."
# Only the extra isolated Compose project is resolved. Do NOT include
# compose.yaml, compose.control.yaml, or any real gateway service.
"${compose[@]}" config -q || die "Isolated Compose invalid."
"${compose[@]}" config --format json | python3 scripts/check_dns01_resolved.py ||
  die "Staging network/secret isolation contract failed."
echo "Staging target: home/auth/metrics.homeawayfromhome.house via DNS-01."
echo "Only a new, separate container, network and staging storage will be touched."
echo "The active HomeAway gateway, ports 8443/8088, VPN and DNS are untouched."
if [[ "$mode" == --plan ]]; then
  echo "Plan complete; no Docker images built, no certificate requests, no DNS changes."
  exit 0
fi
echo "NOTE: --apply will create temporary public _acme-challenge TXT records via Cloudflare."
echo "Proceeding with the explicitly requested staging issuance."
"${compose[@]}" up -d --no-deps --build dns01-staging ||
  die "Staging failed to launch; inspect status/logs."
sleep 2
"${compose[@]}" ps --all dns01-staging
[[ -n "$("${compose[@]}" ps --status running --quiet dns01-staging)" ]] ||
  die "Staging container exited early; run bash scripts/deploy-dns01-staging.sh --status for logs."
echo "Container started; certificate issuance still must be verified via --status."
echo "Run: bash scripts/deploy-dns01-staging.sh --status"
echo "After validation run: bash scripts/deploy-dns01-staging.sh --stop"
