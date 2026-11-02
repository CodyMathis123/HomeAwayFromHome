#!/usr/bin/env bash
# Add LAN HTTPS :443 without disabling the established :8443 entry point.
# Preserve the exact active gateway and Authelia SMTP/recovery overlays.
set -euo pipefail
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $# == 1 && ( $1 == --plan || $1 == --apply ) ]] ||
  fail "Usage: bash scripts/deploy-https443-preview.sh --plan|--apply"
mode="$1"
[[ "$EUID" != 0 ]] || fail "Run as your normal Pi login, not with sudo"
for bin in git sudo python3 docker ss; do command -v "$bin" >/dev/null || fail "$bin is required"; done
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] || fail "Existing .env is required"
[[ "$(git symbolic-ref --short HEAD)" == Production ]] || fail "Switch to Production first"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  fail "Working tree not clean; review changes without discarding them"
base=(sudo docker compose
  -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml
  -f compose.computers.yaml -f compose.production.yaml)
gateway="$(sudo docker ps -q --filter name=^/homeawayfromhome-gateway-1$ --filter status=running)"
[[ -n "$gateway" ]] || fail "The existing production gateway is not running"
mount="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$gateway")"
case "$mount" in
  "$(realpath control/gateway/Caddyfile.production)") mode_name=production ;;
  "$(realpath control/gateway/Caddyfile.account-recovery)")
    [[ -f compose.account-recovery.yaml ]] || fail "Recovery overlay missing"
    base+=( -f compose.account-recovery.yaml ); mode_name=account-recovery ;;
  *) fail "Unknown live Caddyfile mount; refusing to change the gateway" ;;
esac
authelia="$(sudo docker ps -q --filter name=^/homeawayfromhome-authelia-1$ --filter status=running)"
[[ -n "$authelia" ]] || fail "Authelia is not running; do not bypass login"
if sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$authelia" |
     grep -q '^AUTHELIA_NOTIFIER_SMTP_ADDRESS='; then
  [[ -f compose.authelia-smtp.yaml ]] ||
    fail "Live SMTP overlay missing"
  sudo test -f secrets/control/authelia_smtp_password ||
    fail "Root-protected SMTP secret missing"
  base+=( -f compose.authelia-smtp.yaml )
fi
compose=( "${base[@]}" --profile control --profile accounts --profile metrics )
"${compose[@]}" config -q || fail "Compose validation failed"
# Validate the desired bind before touching anything. Never print resolved
# Compose JSON: it can contain secret-bearing environment variables.
"${compose[@]}" config --format json | python3 -c '
import ipaddress,json,sys
c=json.load(sys.stdin); gw=c["services"]["gateway"]
ports=gw["ports"]; bindings={(str(x.get("published")),int(x["target"])):x.get("host_ip") for x in ports}
assert ("8443",8443) in bindings and ("443",8443) in bindings, "Expected dual 443 and 8443 binds"
addr=bindings["443",8443]
assert addr==bindings["8443",8443] and ipaddress.ip_address(addr).is_private,"Ports must share private LAN IP"
api=c["services"]["pc-control"]["environment"]
assert api["HOMEAWAY_CONTROL_ORIGIN"]=="https://home.homeawayfromhome.house:8443"
assert api["HOMEAWAY_CONTROL_ALT_ORIGIN"]=="https://home.homeawayfromhome.house"
assert "ports" not in c["services"]["pc-control"],"Control API must remain unexposed"
print("PASS: private LAN 443 -> Caddy 8443; legacy 8443 retained; strict dual CSRF origins")
' || fail "Resolved private 443 configuration invalid"
# Do not attempt to replace a service already using host port 443.
if [[ -n "$(sudo ss -H -ltn '( sport = :443 )')" ]]; then
  if ! sudo docker port "$gateway" 8443/tcp 2>/dev/null | grep -Eq '(^|[[:space:]])[0-9.]+:443$'; then
    fail "Port 443 is occupied by another service"
  fi
fi
echo "Gateway mode: $mode_name"
echo "PLAN: only gateway and pc-control may be reconciled; existing 8443 stays available"
if [[ "$mode" == --plan ]]; then
  echo "PLAN ONLY: nothing restarted or changed"
  exit 0
fi
# The gateway uses its existing image, Caddyfile and certificates.
"${compose[@]}" up -d --no-deps --no-build gateway ||
  fail "Gateway update failed. Inspect gateway logs and restore the prior Compose configuration"
# Rebuild ONLY control API so its new strictly allowlisted origin is active.
"${compose[@]}" up -d --no-deps --build pc-control ||
  fail "pc-control update failed. The old :8443 origin remains configured"
for service in gateway pc-control; do
  [[ -n "$("${compose[@]}" ps --status running --quiet "$service")" ]] ||
    fail "$service is not running after the change"
done
echo "PASS: targeted services running. Validate portless home/auth/metrics in a browser."
echo "NOTE: Authelia and Grafana still generate :8443 links until the second-stage migration."
