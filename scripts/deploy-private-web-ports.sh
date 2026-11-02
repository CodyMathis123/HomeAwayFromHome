#!/usr/bin/env bash
# Stage 2: close four redundant LAN-facing web listeners after stage-1
# browser acceptance. DNS (53), VPN (51820/udp), Caddy (443) remain open.
set -euo pipefail
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $# == 1 && ( "$1" == "--plan" || "$1" == "--apply" || "$1" == "--status" || "$1" == "--rollback" ) ]] ||
  fail "Usage: bash scripts/deploy-private-web-ports.sh --plan|--apply|--status|--rollback"
mode="$1"
[[ "$EUID" != 0 ]] || fail "Run as normal Pi login; use LAN SSH (Pi-hole briefly restarts)."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git symbolic-ref --short -q HEAD)" == "Production" ]] || fail "Production checkout required."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || fail "Clean worktree required."
[[ -f .env ]] || fail ".env required."
ip="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)"
[[ "$ip" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || fail "Invalid private Pi IP."

# Honor the *actual* live Caddy configuration, including recovery-enabled
# account directory. This does not change LLDAP, Authelia or SMTP services.
gateway="$(sudo docker ps -aq --filter 'name=^/homeawayfromhome-gateway-1$')"
[[ -n "$gateway" ]] || fail "Existing gateway container unavailable."
mount="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$gateway")"
extra=()
case "$mount" in
  "$(realpath control/gateway/Caddyfile.production)") flavor=production ;;
  "$(realpath control/gateway/Caddyfile.account-recovery)")
    [[ -f compose.account-recovery.yaml ]] || fail "Recovery overlay missing."
    flavor=account-recovery
    extra=(-f compose.account-recovery.yaml) ;;
  *) fail "Unexpected mounted Caddyfile; refusing to change ports." ;;
esac
common=(-f compose.yaml -f compose.control.yaml
  -f compose.accounts.yaml -f compose.computers.yaml
  -f compose.production.yaml "${extra[@]}")
base=(sudo docker compose "${common[@]}" --profile control --profile accounts --profile metrics)
old=(sudo docker compose "${common[@]}" -f compose.web-lan-rollback.yaml
  --profile control --profile accounts --profile metrics)
"${base[@]}" config -q || fail "Private production Compose config invalid."
"${old[@]}" config -q || fail "Explicit rollback overlay invalid."

# Validate minimal attack surface and unchanged essential DNS/VPN sockets.
validate_ports(){
  local which="$1"
  if [[ "$which" == private ]]; then
    "${base[@]}" config --format json |
      python3 -c '
import json,sys
c=json.load(sys.stdin)["services"]
ports=lambda s: c[s].get("ports",[])
named=lambda s: {(str(p["published"]),p["target"],p["protocol"],p.get("host_ip","")) for p in ports(s)}
gateway=named("gateway"); pihole=named("pihole")
ip=next(p[3] for p in gateway if p[0]=="443")
assert gateway=={("443",8443,"tcp",ip)}
assert pihole=={("53",53,"tcp",ip),("53",53,"udp",ip)}
assert not ports("portal") and not ports("grafana") and not ports("telemetry-ingest")
assert ("51820",51820,"udp","") in named("wg-easy") or any(
  p[0]=="51820" and p[1]==51820 and p[2]=="udp" for p in named("wg-easy"))
assert ("51821",51821,"tcp","127.0.0.1") in named("wg-easy")
assert ("17170",17170,"tcp","127.0.0.1") in named("lldap")
assert c["pihole"]["environment"]["FTLCONF_webserver_paths_prefix"]=="/dns"
' || fail "Private port surface or DNS/VPN/loopback contract invalid."
  else
    "${old[@]}" config --format json |
      python3 -c '
import json,sys
c=json.load(sys.stdin)["services"]
ports=lambda s: c[s].get("ports",[])
bind=lambda s: {(str(p["published"]),p["target"],p["protocol"],p.get("host_ip","")) for p in ports(s)}
g=bind("gateway")
ip=next(p[3] for p in g if p[0]=="443")
assert g=={("443",8443,"tcp",ip),("8443",8443,"tcp",ip)}
assert bind("pihole")=={("53",53,"tcp",ip),("53",53,"udp",ip),("8080",80,"tcp",ip)}
assert bind("portal")=={("8088",8080,"tcp",ip)}
assert bind("grafana")=={("3000",3000,"tcp",ip)}
' || fail "Rollback bindings are not the exact original LAN configuration."
  fi
}
validate_ports private
validate_ports old

cid(){
  sudo docker ps -q --filter "name=^/homeawayfromhome-$1-1$" --filter status=running
}
routes_ok(){
  local home dns grafana ingest
  home="$(curl -sS --max-time 10 --resolve "home.homeawayfromhome.house:443:$ip" \
    -o /dev/null -w '%{http_code}' https://home.homeawayfromhome.house/)" || return 1
  [[ "$home" == 200 ]] || return 1
  dns="$(curl -sS --max-time 10 --resolve "home.homeawayfromhome.house:443:$ip" \
    -o /dev/null -w '%{http_code}' https://home.homeawayfromhome.house/dns/admin/)" || return 1
  # A 403 is not healthy: it previously concealed a broken DNS policy.
  [[ "$dns" == 302 ]] || return 1
  local location
  location="$(curl -sS --max-time 10 --resolve "home.homeawayfromhome.house:443:$ip" \
    -D - -o /dev/null https://home.homeawayfromhome.house/dns/admin/ \
    | tr -d '\r' | grep -i '^location:' | head -1)" || return 1
  [[ "$location" == *"https://auth.homeawayfromhome.house/"* ]] || return 1
  grafana="$(curl -sS --max-time 10 --resolve "metrics.homeawayfromhome.house:443:$ip" \
    -o /dev/null -w '%{http_code}' https://metrics.homeawayfromhome.house/login)" || return 1
  [[ "$grafana" == 200 || "$grafana" == 302 ]] || return 1
  ingest="$(curl -sS --max-time 10 --resolve "metrics.homeawayfromhome.house:443:$ip" \
    --data-binary 'invalid metric' -o /dev/null -w '%{http_code}' \
    https://metrics.homeawayfromhome.house/ingest)" || return 1
  [[ "$ingest" == 401 ]] || return 1
  return 0
}
dns_prefix_live(){
  sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
    homeawayfromhome-pihole-1 | grep -Fxq 'FTLCONF_webserver_paths_prefix=/dns'
}
private_ports_live(){
  local p
  for p in portal grafana; do
    local id; id="$(cid "$p")"
    [[ -n "$id" && -z "$(sudo docker port "$id")" ]] || return 1
  done
  local pi gw
  pi="$(cid pihole)"; gw="$(cid gateway)"
  [[ -n "$pi" && -n "$gw" ]] || return 1
  local piport gwport
  piport="$(sudo docker port "$pi")" || return 1
  gwport="$(sudo docker port "$gw")" || return 1
  [[ "$piport" == *"53/tcp -> $ip:53"* && "$piport" == *"53/udp -> $ip:53"* ]] || return 1
  [[ "$piport" != *"8080"* && "$gwport" == *"8443/tcp -> $ip:443"* ]] || return 1
  [[ "$gwport" != *"$ip:8443"* ]] || return 1
  return 0
}
rollback(){
  echo "Restoring only portal, Grafana, Pi-hole admin and legacy gateway port bindings." >&2
  for service in portal grafana pihole gateway; do
    "${old[@]}" up -d --no-deps --no-build --force-recreate "$service" ||
      fail "Could not restore $service. Inspect Docker and rerun --rollback."
  done
  echo "RESTORED: direct old web ports; no unrelated services restarted." >&2
}

if [[ "$mode" == "--rollback" ]]; then
  rollback
  exit 0
fi
if [[ "$mode" == "--status" ]]; then
  private_ports_live || fail "At least one redundant web listener remains or a service is down."
  routes_ok || fail "Canonical private HTTPS route unavailable."
  echo "PASS: only gateway 443, Pi-hole DNS 53, VPN and SSH-loopback admin remain published."
  exit 0
fi

dns_prefix_live || fail "Stage-1 Pi-hole /dns prefix is not active; do not remove admin fallback."
routes_ok || fail "Stage-1 HTTPS portal/Pi-hole/Grafana/ingest routes are not all healthy."
echo "PLAN: remove Pi-hole web :8080, portal :8088, Grafana :3000 and Caddy legacy :8443."
echo "PLAN: preserve Pi-hole DNS :53 tcp+udp, WireGuard :51820 udp and Caddy HTTPS :443."
echo "PLAN: retain SSH-loopback admin only on :51821 (wg-easy) and :17170 (LLDAP)."
echo "PLAN: recreate only portal, Grafana, Pi-hole (brief DNS gap), then Caddy."
echo "PLAN: if any post-check fails, restore those four bindings from compose.web-lan-rollback.yaml."
echo "PLAN: requires prior real-browser acceptance: Pi-hole login/assets/API, HomeAway, Grafana embeds, TOTP and PC control."
if [[ "$mode" == "--plan" ]]; then
  echo "PLAN ONLY: no services changed."
  exit 0
fi

for service in portal grafana pihole gateway; do
  echo "Recreating only $service with canonical private web bindings."
  if ! "${base[@]}" up -d --no-deps --no-build --force-recreate "$service"; then
    echo "WARN: failed to reconcile $service; restoring previous web ports." >&2
    rollback
    fail "Cutover failed; original web ports were restored."
  fi
done
for attempt in {1..20}; do
  if private_ports_live && routes_ok >/dev/null 2>&1; then
    echo "PASS: redundant web ports retired; canonical private HTTPS routes work."
    echo "VERIFY: real-browser Pi-hole login, portal, Grafana, PC control, DNS and VPN."
    exit 0
  fi
  if [[ "$attempt" -lt 20 ]]; then sleep 2; fi
done
echo "WARN: web cutover checks failed; restoring old bindings." >&2
rollback
fail "Post-cutover validation failed; old listeners restored. Inspect gateway logs."
