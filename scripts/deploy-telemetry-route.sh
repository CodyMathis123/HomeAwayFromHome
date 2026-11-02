#!/usr/bin/env bash
# Opt-in private canonical telemetry endpoint: TLS-only, no router/DNS changes.
# Restarts only Caddy. Existing :8429 listener stays as a fallback.
set -euo pipefail
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# == 1 && ( "$1" == "--plan" || "$1" == "--apply" || "$1" == "--status" ) ]] ||
  fail "Usage: bash scripts/deploy-telemetry-route.sh --plan|--apply|--status"
mode="$1"
[[ "$EUID" != 0 ]] || fail "Run as normal Pi login, not sudo."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git symbolic-ref --short -q HEAD)" == "Production" ]] || fail "Production checkout required."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || fail "Worktree must be clean."
[[ -f .env ]] || fail "Existing .env required."
ip="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)"
[[ "$ip" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || fail "Invalid private IPv4 binding."
sudo test -f secrets/telemetry/ca.crt || fail "Existing telemetry CA missing; do not replace receiver credentials."
sudo test -f secrets/telemetry/server.crt || fail "Existing telemetry server certificate missing."
sudo openssl verify -CAfile secrets/telemetry/ca.crt -verify_ip "$ip" \
  secrets/telemetry/server.crt >/dev/null ||
  fail "Receiver certificate not trusted for current private Pi IP; no deploy."

cid="$(sudo docker ps -q --filter 'name=^/homeawayfromhome-gateway-1$' --filter status=running)"
[[ -n "$cid" ]] || fail "Existing Caddy gateway is not running."
receiver="$(sudo docker ps -q --filter 'name=^/homeawayfromhome-telemetry-ingest-1$' --filter status=running)"
[[ -n "$receiver" ]] || fail "Existing telemetry receiver is not running."
mounted="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$cid")"
standard="$(realpath control/gateway/Caddyfile.production)"
recovery="$(realpath control/gateway/Caddyfile.account-recovery)"
extra=()
case "$mounted" in
  "$standard") flavor=production ;;
  "$recovery") flavor=account-recovery; extra=(-f compose.account-recovery.yaml) ;;
  *) fail "Unexpected Caddy configuration mount; no gateway changes." ;;
esac
compose=(sudo docker compose -f compose.yaml -f compose.control.yaml
  -f compose.accounts.yaml -f compose.computers.yaml -f compose.production.yaml
  "${extra[@]}" --profile control --profile accounts --profile metrics)
"${compose[@]}" config -q || fail "Existing production Compose overlay is invalid."

verify(){
  local code grafana
  code="$(curl --silent --show-error --max-time 10 \
    --resolve "metrics.homeawayfromhome.house:443:$ip" \
    --output /dev/null --write-out '%{http_code}' \
    -X POST -H 'Content-Type: text/plain' --data-binary 'invalid metric' \
    https://metrics.homeawayfromhome.house/ingest)" || return 1
  if [[ "$code" != 401 ]]; then
    echo "Expected 401 for unauthenticated ingestion, got $code." >&2
    if [[ "$code" == 302 ]]; then
      echo "The request appears to reach Grafana's login redirect, not the new ingestion route." >&2
      echo "Check the gateway rollout first; do not change SensorDash." >&2
    fi
    return 1
  fi
  grafana="$(curl --silent --show-error --max-time 10 \
    --resolve "metrics.homeawayfromhome.house:443:$ip" \
    --output /dev/null --write-out '%{http_code}' \
    https://metrics.homeawayfromhome.house/login)" || return 1
  [[ "$grafana" == 200 || "$grafana" == 302 ]] ||
    { echo "Grafana's normal /login path returned $grafana" >&2; return 1; }
  echo "PASS: canonical HTTPS receiver rejects anonymous writes; Grafana still responds."
}

if [[ "$mode" == "--status" ]]; then
  verify || fail "New route unavailable; do not change SensorDash exporter yet."
  exit 0
fi
echo "PLAN: Caddy flavor=$flavor; metrics hostname already exists; only gateway restart requested."
echo "PLAN: old https://$ip:8429/ingest remains live; no SensorDash, Pi-hole, DNS or VPN changes."
echo "PLAN: gateway receives only telemetry/ca.crt (public CA certificate, never any keys)."
# Cloudflare's pinned Caddy DNS module checks the *shape* of a token while
# provisioning, even when all networking is disabled. A short text placeholder
# fails validation before it can inspect the route. Use a fixed synthetic
# 40-character token matching that shape. Never load or print the real token.
# The container has --network none, so it cannot reach Cloudflare.
offline_cf_token="0123456789abcdefghijklmnopqrstuvwxyzABCD"
validate_offline() {
  sudo docker run --rm --network none --cap-drop ALL \
    -e PI_LAN_IP="$ip" -e CF_API_TOKEN="$offline_cf_token" \
    -v "$PWD/control/gateway/Caddyfile.$flavor:/etc/caddy/Caddyfile:ro" \
    -v "$PWD/secrets/telemetry/ca.crt:/run/telemetry-ca.crt:ro" \
    --entrypoint /usr/local/bin/homeaway-caddy \
    homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 \
    validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null ||
    fail "Offline Caddy validation failed; live gateway not restarted."
  echo "PASS: Caddy configuration validated offline without real Cloudflare credentials."
}
python3 scripts/prepare-gateway-permissions.py --plan --config "$flavor"
# Always validate in --plan so a bad Caddy configuration cannot first surface
# after an operator has begun an actual deployment.
validate_offline
if [[ "$mode" == "--plan" ]]; then
  echo "PLAN ONLY: nothing changed."
  exit 0
fi
python3 scripts/prepare-gateway-permissions.py --apply --config "$flavor"
# Public CA cert is intentionally readable inside the gateway. Keys/token stay private.
sudo chmod 0644 secrets/telemetry/ca.crt
echo "Recreating only the existing Caddy gateway (brief HTTPS interruption)."
"${compose[@]}" up -d --no-deps --no-build --force-recreate gateway ||
  fail "Gateway recreation failed; inspect gateway logs. Other services untouched."
[[ -n "$("${compose[@]}" ps --status running --quiet gateway)" ]] ||
  fail "Gateway is not running after restart."
verify || fail "Canonical route verification failed; KEEP the existing :8429 SensorDash URL."
echo "PASS: update SensorDash endpoint only with compatible Windows build."
