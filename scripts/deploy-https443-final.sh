#!/usr/bin/env bash
# Operator-triggered portless URL migration. No router, DNS or TLS changes.
# Keeps :8443 as a working fallback and never alters PWM/VPN/Pi-hole.
set -euo pipefail
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ $# == 1 && ( $1 == --plan || $1 == --apply || $1 == --rollback ) ]] ||
  fail "Usage: bash scripts/deploy-https443-final.sh --plan|--apply|--rollback"
mode="$1"
[[ "$EUID" -ne 0 ]] || fail "Run as your normal user; the script calls sudo"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] || fail "Existing .env required"
[[ "$(git symbolic-ref --quiet --short HEAD)" == Production ]] ||
  fail "Checkout must be Production"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  fail "Working tree must be clean; do not discard local changes"
base=(sudo docker compose -f compose.yaml -f compose.control.yaml
  -f compose.accounts.yaml -f compose.computers.yaml -f compose.production.yaml)
gateway="$(sudo docker ps -q --filter name=^/homeawayfromhome-gateway-1$ --filter status=running)"
[[ -n "$gateway" ]] || fail "Active gateway unavailable"
mount="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$gateway")"
recovery=false
case "$mount" in
  "$(realpath control/gateway/Caddyfile.production)") ;;
  "$(realpath control/gateway/Caddyfile.account-recovery)")
    [[ -f compose.account-recovery.yaml ]] || fail "Recovery overlay missing"
    base+=( -f compose.account-recovery.yaml ); recovery=true ;;
  *) fail "Unknown gateway mount; no changes" ;;
esac
authelia="$(sudo docker ps -q --filter name=^/homeawayfromhome-authelia-1$ --filter status=running)"
[[ -n "$authelia" ]] || fail "Authelia must already be running"
if sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$authelia" |
     grep -q '^AUTHELIA_NOTIFIER_SMTP_ADDRESS='; then
  [[ -f compose.authelia-smtp.yaml ]] || fail "Existing SMTP overlay missing"
  sudo test -f secrets/control/authelia_smtp_password ||
    fail "Existing root-only SMTP secret missing"
  base+=( -f compose.authelia-smtp.yaml )
fi
[[ "$mode" != --rollback ]] || base+=( -f compose.https443-rollback.yaml )
compose=("${base[@]}" --profile control --profile accounts --profile metrics)
"${compose[@]}" config -q || fail "Existing overlays could not be validated"
# Only print static public URL metadata. The Compose environment can contain
# SMTP credentials and must never be dumped to terminal/logs.
"${compose[@]}" config --format json | python3 -c '
import json,ipaddress,sys
c=json.load(sys.stdin); services=c["services"]
ports=services["gateway"]["ports"]
bindings={(str(p["published"]),int(p["target"]),p.get("protocol"),p["host_ip"]) for p in ports}
assert len(ports)==2 and len(bindings)==2
addr=next(p["host_ip"] for p in ports if str(p["published"])=="443")
assert ipaddress.ip_address(addr).is_private
assert bindings=={("443",8443,"tcp",addr),("8443",8443,"tcp",addr)}
pc=services["pc-control"]
portless="https://home.homeawayfromhome.house"; old=portless+":8443"
legacy="--rollback" in sys.argv
assert pc["environment"]["HOMEAWAY_CONTROL_ORIGIN"]==(old if legacy else portless)
assert pc["environment"]["HOMEAWAY_CONTROL_ALT_ORIGIN"]==(portless if legacy else old)
assert not pc.get("ports")
portal=services["portal"]["environment"]
assert portal["HOMEAWAY_CONTROL_URL"]==(old if legacy else portless)+"/control/"
metrics="https://metrics.homeawayfromhome.house"
assert services["grafana"]["environment"]["GF_SERVER_ROOT_URL"]==(metrics+":8443" if legacy else metrics)
print("PASS: strict LAN-only dual port, control CSRF and canonical destination bindings")
' "$mode" || fail "Resolved portless URL configuration failed"
if [[ "$mode" == --plan ]]; then
  sudo python3 scripts/update-authelia-canonical-urls.py --plan ||
    fail "Authelia private configuration preflight failed"
  echo "PLAN: update Authelia redirect URLs; recreate pc-control/portal/grafana"
  if $recovery; then echo "PLAN: also update LLDAP password recovery URL"; fi
  echo "PLAN ONLY: Caddy, DNS, VPN and Pi-hole unchanged"
  exit 0
fi
services=(pc-control portal grafana)
if $recovery; then services+=(lldap); fi
if [[ "$mode" == --apply ]]; then
  sudo python3 scripts/update-authelia-canonical-urls.py --apply ||
    fail "Authelia private configuration not changed; no containers reconciled"
else
  sudo python3 scripts/update-authelia-canonical-urls.py --rollback ||
    fail "Authelia backup restore failed; no containers reconciled"
fi
"${compose[@]}" up -d --no-deps --build "${services[@]}" ||
  fail "Failed to reconcile new URL environments. Use --rollback and inspect logs"
"${compose[@]}" restart authelia ||
  fail "Failed to restart Authelia; use --rollback and inspect logs"
for service in authelia "${services[@]}"; do
  [[ -n "$("${compose[@]}" ps --status running --quiet "$service")" ]] ||
    fail "$service not running. Use --rollback if this was the cutover"
done
echo "PASS: canonical URLs switched. Caddy remains on both ports."
echo "Verify browser login, TOTP, Grafana, PC actions and account recovery."
