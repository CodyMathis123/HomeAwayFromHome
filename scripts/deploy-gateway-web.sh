#!/usr/bin/env bash
# Stage 1: make all web UI destinations work via private HTTPS before closing
# their old LAN listeners. Only gateway, portal and (last) Pi-hole are reconciled.
set -euo pipefail
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $# == 1 && ( "$1" == "--plan" || "$1" == "--apply" || "$1" == "--status" || "$1" == "--rollback-dns" ) ]] ||
  fail "Usage: bash scripts/deploy-gateway-web.sh --plan|--apply|--status|--rollback-dns"
mode="$1"
[[ "$EUID" != 0 ]] || fail "Run as normal Pi user; sudo is invoked for narrowly scoped operations."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git symbolic-ref --short -q HEAD)" == "Production" ]] || fail "Production checkout required."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || fail "Clean worktree required."
[[ -f .env ]] || fail ".env is required."
ip="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)"
[[ "$ip" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || fail "Invalid Pi IP."

# This restore path does not depend on a working gateway or SMTP overlay.
if [[ "$mode" == "--rollback-dns" ]]; then
  echo "ROLLBACK: restoring the original Pi-hole URL prefix; DNS may pause briefly."
  sudo docker compose -f compose.yaml -f compose.dns-prefix-rollback.yaml \
    --profile metrics up -d --no-deps --no-build --force-recreate pihole ||
    fail "Pi-hole rollback failed. Inspect only the pihole container."
  echo "Pi-hole original admin path: http://$ip:8080/admin/ (temporary LAN fallback)."
  exit 0
fi

gateway="$(sudo docker ps -q --filter 'name=^/homeawayfromhome-gateway-1$' --filter status=running)"
pihole="$(sudo docker ps -q --filter 'name=^/homeawayfromhome-pihole-1$' --filter status=running)"
portal="$(sudo docker ps -q --filter 'name=^/homeawayfromhome-portal-1$' --filter status=running)"
[[ -n "$gateway" && -n "$pihole" && -n "$portal" ]] ||
  fail "Gateway, Pi-hole and portal must already be running; nothing changed."
mounted="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$gateway")"
extra=()
case "$mounted" in
  "$(realpath control/gateway/Caddyfile.production)") flavor=production ;;
  "$(realpath control/gateway/Caddyfile.account-recovery)")
    flavor=account-recovery
    extra=(-f compose.account-recovery.yaml) ;;
  *) fail "Unexpected mounted Caddyfile; never switch a live gateway variant implicitly." ;;
esac
compose=(sudo docker compose -f compose.yaml -f compose.control.yaml
  -f compose.accounts.yaml -f compose.computers.yaml -f compose.production.yaml
  "${extra[@]}" --profile control --profile accounts --profile metrics)
"${compose[@]}" config -q || fail "Resolved production configuration invalid."

# Verify that rollout cannot silently change host-facing ports or tunnel flags.
"${compose[@]}" config --format json | python3 -c '
import json,sys
c=json.load(sys.stdin)["services"]
p=c["pihole"]
assert p["environment"]["FTLCONF_webserver_paths_prefix"]=="/dns"
ports={(str(x["published"]),x["target"],x["protocol"]) for x in p["ports"]}
assert ("53",53,"tcp") in ports and ("53",53,"udp") in ports and ("8080",80,"tcp") in ports
assert any(str(x["published"])=="8088" for x in c["portal"]["ports"])
assert any(str(x["published"])=="3000" for x in c["grafana"]["ports"])
assert any(str(x["published"])=="443" for x in c["gateway"]["ports"])
assert any(x["target"]==51820 and x["protocol"]=="udp" for x in c["wg-easy"]["ports"])
' || fail "DNS/VPN/web fallback port contract invalid."

verify_route(){
  local code control dns api metrics
  code="$(curl -sS --max-time 10 --resolve "home.homeawayfromhome.house:443:$ip" \
    -o /dev/null -w '%{http_code}' https://home.homeawayfromhome.house/)" || return 1
  [[ "$code" == 200 ]] || { echo "Home portal returned $code, expected 200" >&2; return 1; }
  api="$(curl -sS --max-time 10 --resolve "home.homeawayfromhome.house:443:$ip" \
    -o /dev/null -w '%{http_code}' https://home.homeawayfromhome.house/api/control-config)" || return 1
  [[ "$api" == 200 ]] || { echo "Portal API returned $api, expected 200" >&2; return 1; }
  dns="$(curl -sS --max-time 10 --resolve "home.homeawayfromhome.house:443:$ip" \
    -o /dev/null -w '%{http_code}' https://home.homeawayfromhome.house/dns/admin/)" || return 1
  [[ "$dns" == 302 || "$dns" == 401 || "$dns" == 403 ]] ||
    { echo "Unauthenticated Pi-hole admin returned $dns; expected Authelia denial/redirect" >&2; return 1; }
  metrics="$(curl -sS --max-time 10 --resolve "metrics.homeawayfromhome.house:443:$ip" \
    -o /dev/null -w '%{http_code}' https://metrics.homeawayfromhome.house/login)" || return 1
  [[ "$metrics" == 200 || "$metrics" == 302 ]] ||
    { echo "Grafana returned $metrics" >&2; return 1; }
  echo "PASS: portal API, Authelia-protected DNS admin, and Grafana reachable on canonical HTTPS."
}
dns_prefix_running(){
  sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
    homeawayfromhome-pihole-1 | grep -Fxq 'FTLCONF_webserver_paths_prefix=/dns'
}

if [[ "$mode" == "--status" ]]; then
  dns_prefix_running || fail "Pi-hole has not yet been restarted with /dns prefix."
  verify_route || fail "One of the canonical web routes is unavailable."
  echo "STATUS ONLY: direct LAN ports are still published until the separate port-retirement step."
  exit 0
fi

echo "PLAN: active gateway Caddyfile=$flavor; no new DNS record or public port."
echo "PLAN: establish https://home.homeawayfromhome.house/dns/admin/ behind Authelia + Pi-hole login."
echo "PLAN: rebuild portal links/embeds to use private HTTPS, preserving the existing control/auth routes."
echo "PLAN: only Caddy + portal + Pi-hole would be recreated; Pi-hole DNS may pause briefly."
echo "PLAN: 8080, 8088, 3000, 8443 remain published as temporary rollback while browser access is tested."
python3 scripts/prepare-gateway-permissions.py --plan --config "$flavor"
# Check Caddyfile with no external network and a fake, syntactically valid
# Cloudflare token. Never read/display the real DNS-01 credential for this test.
offline_cf_token="0123456789abcdefghijklmnopqrstuvwxyzABCD"
validate_offline(){
  sudo docker run --rm --network none --cap-drop ALL \
    -e PI_LAN_IP="$ip" -e CF_API_TOKEN="$offline_cf_token" \
    -v "$PWD/control/gateway/Caddyfile.$flavor:/etc/caddy/Caddyfile:ro" \
    -v "$PWD/secrets/telemetry/ca.crt:/run/telemetry-ca.crt:ro" \
    --entrypoint /usr/local/bin/homeaway-caddy \
    homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 \
    validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null ||
    fail "Offline Caddy validation failed. No services changed."
}
validate_offline
echo "PASS: offline Caddy configuration validated without real DNS credentials."
if [[ "$mode" == "--plan" ]]; then
  echo "PLAN ONLY: nothing changed."
  exit 0
fi

python3 scripts/prepare-gateway-permissions.py --apply --config "$flavor"
# Gateway recreation keeps the current Caddy variant and certificate volume.
echo "Recreating gateway ONLY (brief HTTPS interruption)."
"${compose[@]}" up -d --no-deps --no-build --force-recreate gateway ||
  fail "Gateway recreation failed; Pi-hole untouched."
echo "Updating only the read-only portal."
"${compose[@]}" up -d --no-deps --build portal ||
  fail "Portal update failed; Pi-hole untouched."
echo "Applying Pi-hole v6 reverse-proxy URL prefix (brief household DNS interruption)."
if ! "${compose[@]}" up -d --no-deps --no-build --force-recreate pihole; then
  echo "WARN: restoring original Pi-hole URL prefix." >&2
  sudo docker compose -f compose.yaml -f compose.dns-prefix-rollback.yaml --profile metrics \
    up -d --no-deps --no-build --force-recreate pihole ||
    fail "Automatic DNS-prefix rollback failed; inspect Pi-hole now."
  fail "Pi-hole prefix deployment failed; direct :8080 remains available."
fi
for attempt in {1..20}; do
  if dns_prefix_running && verify_route >/dev/null 2>&1; then
    echo "PASS: canonical web routes ready; direct web ports intentionally retained."
    echo "NEXT: log into Pi-hole via /dns/admin/ and verify HomeAway, Grafana, TOTP and PC controls."
    exit 0
  fi
  if [[ "$attempt" -lt 20 ]]; then sleep 2; fi
done
echo "WARN: gateway web verification failed; check status before removing old ports." >&2
verify_route || true
fail "Do NOT retire the old web ports; original :8080, :8088, :3000, :8443 are retained."
