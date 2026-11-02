#!/usr/bin/env bash
# Retire the legacy LAN telemetry port after canonical HTTPS delivery is proven.
# Only the receiver container is reconciled; no gateway/VM/DNS/VPN/fan restarts.
set -euo pipefail
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $# == 1 && ( "$1" == "--plan" || "$1" == "--apply" || "$1" == "--status" || "$1" == "--rollback" ) ]] ||
  fail "Usage: bash scripts/deploy-telemetry-private.sh --plan|--apply|--status|--rollback"
mode="$1"
[[ "$EUID" != 0 ]] || fail "Run as normal Pi login (script uses sudo)."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(git symbolic-ref --quiet --short HEAD)" == Production ]] ||
  fail "Clean Production checkout required."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  fail "Worktree not clean; inspect changes before running."
[[ -f .env ]] || fail "Existing .env is required."
ip="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)" ||
  fail "Could not read private Pi IPv4 from config."

# The running gateway must be the already-tested private production TLS route.
gateway="$(sudo docker ps -q --filter 'name=^/homeawayfromhome-gateway-1$' --filter status=running)"
[[ -n "$gateway" ]] || fail "Private HTTPS gateway is not running."
mounted="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$gateway")"
extra=()
case "$mounted" in
  "$(realpath control/gateway/Caddyfile.production)") ;;
  "$(realpath control/gateway/Caddyfile.account-recovery)")
    [[ -f compose.account-recovery.yaml ]] || fail "Recovery overlay missing."
    extra=(-f compose.account-recovery.yaml) ;;
  *) fail "Unexpected live Caddy mount; no telemetry changes." ;;
esac
base=(sudo docker compose -f compose.yaml -f compose.control.yaml
  -f compose.accounts.yaml -f compose.computers.yaml
  -f compose.production.yaml "${extra[@]}" --profile control
  --profile accounts --profile metrics)
rollback=(sudo docker compose -f compose.yaml -f compose.control.yaml
  -f compose.accounts.yaml -f compose.computers.yaml
  -f compose.production.yaml "${extra[@]}"
  -f compose.telemetry-direct-rollback.yaml --profile control
  --profile accounts --profile metrics)
"${base[@]}" config -q || fail "Private-only Compose configuration invalid."
"${rollback[@]}" config -q || fail "Rollback Compose configuration invalid."

# Validate the precise port change without echoing the full resolved Compose
# configuration (which could include SMTP credentials).
check_config(){
  local expected="$1"
  if [[ "$expected" == direct ]]; then
    "${rollback[@]}" config --format json |
      python3 -c 'import json,sys
s=json.load(sys.stdin)["services"]["telemetry-ingest"]
p=s.get("ports",[])
assert len(p)==1 and str(p[0]["published"])=="8429" and p[0]["target"]==8443
assert p[0]["protocol"]=="tcp" and p[0]["host_ip"]==sys.argv[1]
assert "infra" in s["networks"]
' "$ip" || fail "Rollback port binding is not the original private mapping."
  else
    "${base[@]}" config --format json |
      python3 -c 'import json,sys
s=json.load(sys.stdin)["services"]["telemetry-ingest"]
assert not s.get("ports"),"Receiver still publishes host ports"
assert "infra" in s["networks"],"Caddy must retain private upstream reachability"
assert s.get("read_only") is True
' || fail "Private-only receiver contract failed."
  fi
}
check_config private
check_config direct

ingest_running(){
  cid="$(sudo docker ps -q --filter 'name=^/homeawayfromhome-telemetry-ingest-1$' --filter status=running)"
  [[ -n "$cid" ]] || { echo "Telemetry receiver container is not running." >&2; return 1; }
}
route_ok(){
  local code grafana
  code="$(curl --silent --show-error --max-time 10 \
    --resolve "metrics.homeawayfromhome.house:443:$ip" \
    --output /dev/null --write-out '%{http_code}' \
    -X POST -H 'Content-Type: text/plain' --data-binary 'invalid metric' \
    https://metrics.homeawayfromhome.house/ingest)" || return 1
  if [[ "$code" != 401 ]]; then
    echo "Canonical private ingress must return 401 without bearer; received $code" >&2
    return 1
  fi
  grafana="$(curl --silent --show-error --max-time 10 \
    --resolve "metrics.homeawayfromhome.house:443:$ip" \
    --output /dev/null --write-out '%{http_code}' \
    https://metrics.homeawayfromhome.house/login)" || return 1
  [[ "$grafana" == 200 || "$grafana" == 302 ]] ||
    { echo "Grafana /login returned $grafana" >&2; return 1; }
  return 0
}
no_published_ports(){
  local published
  published="$(sudo docker port "$cid" 2>/dev/null)" || return 1
  [[ -z "$published" ]] || { echo "Receiver still has a published port: $published" >&2; return 1; }
}

receiver_socket_ready(){
  # Docker "Started" does not guarantee Python has bound its TLS socket.
  # Probe inside this container only: no credentials and no hardware changes.
  sudo docker exec "$cid" python -B -c \
    'import socket; s=socket.create_connection(("127.0.0.1",8443), timeout=2); s.close()' \
    >/dev/null 2>&1
}
wait_for_private_route(){
  local attempt
  # Receiver restart can change its Docker bridge IP. Allow the TLS listener
  # to start and Caddy to establish a connection using the new Docker DNS IP.
  for attempt in {1..20}; do
    if ingest_running && receiver_socket_ready && route_ok >/dev/null 2>&1; then
      echo "PASS: receiver TLS socket and private Caddy route are healthy."
      return 0
    fi
    if [[ "$attempt" -lt 20 ]]; then sleep 2; fi
  done
  echo "Private Caddy route did not recover after bounded retries." >&2
  route_ok || true
  return 1
}

ingest_running || fail "Telemetry receiver unavailable; no changes made."
if [[ "$mode" == "--status" ]]; then
  no_published_ports || fail "Receiver still exposes a host port."
  route_ok || fail "Canonical HTTPS ingestion failed after port retirement."
  echo "PASS: receiver remains running with no Docker host ports; canonical private HTTPS is healthy."
  exit 0
fi
if [[ "$mode" == "--rollback" ]]; then
  echo "ROLLBACK: restoring original LAN-only 8429 mapping on telemetry-ingest."
  "${rollback[@]}" up -d --no-deps --no-build --force-recreate telemetry-ingest ||
    fail "Rollback recreation failed; inspect only telemetry-ingest."
  ingest_running || fail "Rollback receiver not running."
  sudo docker port "$cid" | grep -Fq "$ip:8429" ||
    fail "Original :8429 binding not restored."
  echo "PASS: direct LAN receiver restored. Keep Windows exporter on the URL that works."
  exit 0
fi

route_ok || fail "Canonical HTTPS route is unhealthy; keep existing port 8429."
echo "PLAN: retire host port $ip:8429; preserve private TLS and bearer authentication."
echo "PLAN: recreate only telemetry-ingest; Caddy, Grafana, VictoriaMetrics, Pi-hole and VPN stay running."
echo "PLAN: a short ingest reconnect is expected; Windows telemetry spool handles retries."
echo "PLAN: rollback restores $ip:8429 from the separate overlay."
if [[ "$mode" == "--plan" ]]; then
  echo "PLAN ONLY: nothing changed."
  exit 0
fi
echo "Recreating only the existing telemetry-ingest container with no published port."
if ! "${base[@]}" up -d --no-deps --no-build --force-recreate telemetry-ingest; then
  echo "WARN: private-only recreation failed; attempting rollback." >&2
  "${rollback[@]}" up -d --no-deps --no-build --force-recreate telemetry-ingest ||
    fail "Automatic rollback failed; inspect telemetry-ingest and run --rollback."
  fail "Private-only recreation failed; original LAN port has been restored."
fi
if ! ingest_running || ! no_published_ports || ! wait_for_private_route; then
  echo "WARN: post-cutover verification failed; automatically restoring original LAN mapping." >&2
  "${rollback[@]}" up -d --no-deps --no-build --force-recreate telemetry-ingest ||
    fail "Automatic rollback failed; run --rollback after reviewing logs."
  echo "Rollback restored port 8429; confirm the live route has recovered before further changes." >&2
  fail "Cutover did not pass checks; original port mapping was restored."
fi
echo "PASS: direct 8429 mapping retired; canonical HTTPS ingestion and Grafana healthy."
echo "VERIFY: SensorDash Settings -> Pi telemetry export should show a fresh last delivery."
