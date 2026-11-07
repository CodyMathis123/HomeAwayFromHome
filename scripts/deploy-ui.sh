#!/usr/bin/env bash
# Targeted, operator-triggered UI deployment for an already-running HomeAway Pi.
# Does NOT pull images or restart Pi-hole, WireGuard or unrelated services.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: bash scripts/deploy-ui.sh --plan|--apply [--services both|portal|pc-control|grafana]

  --plan         Read-only preflight. Shows last-fetched Production versions.
  --apply        Fetch and fast-forward Production, then build/reconcile only
                 the selected already-running UI or Grafana containers.
  --services     Default both (portal and pc-control); grafana targets dashboards.

Run as your normal Pi login, not with sudo. Docker is called with sudo.
This is NOT a DNS/VPN updater, backup tool or a Phase 2 host installer.
EOF
}
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

mode=""
selection="both"
while (($#)); do
  case "$1" in
    --plan|--apply)
      [[ -z "$mode" ]] || { usage; exit 2; }
      mode="$1" ;;
    --services)
      (($# >= 2)) || { usage; exit 2; }
      selection="$2"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done
[[ -n "$mode" ]] || { usage; exit 2; }
case "$selection" in
  both) services=(portal pc-control) ;;
  portal|pc-control|grafana) services=("$selection") ;;
  *) usage; exit 2 ;;
esac

[[ "${EUID}" -ne 0 ]] || fail "Run this script as your normal user, not sudo."
command -v git >/dev/null || fail "git is required."
command -v python3 >/dev/null || fail "python3 is required."
command -v sudo >/dev/null || fail "sudo is required for Docker."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] || fail "Missing .env; refusing to initialize or change configuration."
[[ -f compose.yaml && -f compose.control.yaml && -f compose.accounts.yaml && -f compose.computers.yaml && -f compose.production.yaml ]] ||
  fail "Missing one or more required Compose overlays."
[[ "$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)" == "Production" ]] ||
  fail "Switch to the Production branch before deploying (no worktrees or feature branches)."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  fail "Working tree is not clean. Inspect git status; do not discard local files automatically."
git rev-parse --verify refs/remotes/origin/Production >/dev/null 2>&1 ||
  fail "Missing origin/Production tracking ref. Review your Git remote before deploying."

compose=(sudo docker compose
  -f compose.yaml
  -f compose.control.yaml
  -f compose.accounts.yaml
  -f compose.computers.yaml
  -f compose.production.yaml
  --profile control
  --profile accounts
  --profile metrics)

# Detect the actual mounted gateway before validating the production overlays.
# The recovery Caddyfile is canonical for PIs with password reset enabled.
# Never silently adopt an unknown or historical mount.
check_gateway() {
  local cid mounted standard recovery
  cid="$(sudo docker ps -q --filter name=^/homeawayfromhome-gateway-1$ --filter status=running)"
  [[ -n "$cid" ]] || fail "The production gateway is not running."
  mounted="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$cid")" ||
    fail "Cannot inspect gateway mount."
  standard="$(realpath control/gateway/Caddyfile.production)"
  recovery="$(realpath control/gateway/Caddyfile.account-recovery)"
  if [[ "$mounted" == "$standard" ]]; then
    gateway_config=production
  elif [[ "$mounted" == "$recovery" ]]; then
    gateway_config=account-recovery
  else
    fail "Unexpected active gateway Caddyfile; refusing a deploy from mismatched overlays."
  fi
}
check_gateway
if [[ "$gateway_config" == account-recovery ]]; then
  [[ -f compose.account-recovery.yaml ]] ||
    fail "Live gateway uses recovery Caddyfile but its Compose overlay is missing."
  compose+=( -f compose.account-recovery.yaml )
fi
# Keep an existing SMTP-enabled Authelia overlay in the resolved project.
# This is read-only detection; never print SMTP credential values.
authelia_cid="$(sudo docker ps -q --filter name=^/homeawayfromhome-authelia-1$ --filter status=running)"
if [[ -n "$authelia_cid" ]] &&
   sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$authelia_cid" |
     grep -q '^AUTHELIA_NOTIFIER_SMTP_ADDRESS='; then
  [[ -f compose.authelia-smtp.yaml ]] ||
    fail "Live Authelia uses SMTP but the optional Compose overlay is missing."
  # File is deliberately root-protected. Never require the login user to
  # traverse the secrets directory or weaken its permissions to inspect it.
  sudo test -f secrets/control/authelia_smtp_password ||
    fail "Live Authelia uses SMTP but its root-protected secret file is missing."
  compose+=( -f compose.authelia-smtp.yaml )
fi

# Validate all overlays without printing values that might contain credentials.
"${compose[@]}" config -q || fail "Compose validation failed; no changes applied."
project="$("${compose[@]}" config --format json |
  python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))')" ||
  fail "Cannot determine Compose project; no changes applied."
[[ "$project" == "homeawayfromhome" ]] ||
  fail "Unexpected Compose project name; refusing to touch containers."

# Do not unintentionally start an optional service or adopt another project.
check_gateway
for service in "${services[@]}"; do
  running="$("${compose[@]}" ps --status running --quiet "$service")" ||
    fail "Cannot inspect $service; no changes applied."
  [[ -n "$running" ]] ||
    fail "$service is not running in the HomeAway Compose project. Start it only through its reviewed setup guide."
done

printf 'Project: %s\nServices: %s\n' "$project" "${services[*]}"
printf 'Checkout: %s\n' "$(git rev-parse --short HEAD)"
printf 'Last fetched Production: %s\n' "$(git rev-parse --short origin/Production)"

if [[ "$mode" == "--plan" ]]; then
  printf 'PLAN ONLY: no fetch, merge, build, restart or other service change performed.\n'
  printf 'Apply with: bash scripts/deploy-ui.sh --apply --services %s\n' "$selection"
  exit 0
fi

printf 'Fetching latest Production from origin...\n'
git fetch origin || fail "Git fetch failed; containers untouched."
git merge-base --is-ancestor HEAD origin/Production ||
  fail "Local Production cannot fast-forward to origin/Production. Resolve Git divergence manually."
printf 'Incoming commit summary (no deploy yet):\n'
git diff --stat HEAD..origin/Production
git merge --ff-only origin/Production || fail "Fast-forward failed; containers untouched."
"${compose[@]}" config -q || fail "Updated Compose config invalid; no containers restarted."
[[ "$("${compose[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))')" == "homeawayfromhome" ]] ||
  fail "Updated Compose project differs; no containers restarted."

check_gateway
# Git checkout umask may leave tracked Caddyfile at 0600 after a pull.
# Repair ONLY this non-secret config before reconciling the selected UI.
python3 scripts/prepare-gateway-permissions.py --apply --config "$gateway_config" ||
  fail "Canonical Caddyfile is unreadable; refusing UI deployment."
printf 'Rebuilding ONLY %s (no dependencies)...\n' "${services[*]}"
"${compose[@]}" up -d --no-deps --build "${services[@]}" ||
  fail "Targeted deployment failed. Inspect selected container logs; other services were not requested."
for service in "${services[@]}"; do
  running="$("${compose[@]}" ps --status running --quiet "$service")" ||
    fail "Cannot verify $service after deployment."
  [[ -n "$running" ]] || fail "$service is not running after deployment; inspect its logs."
done
printf 'Deployed Production %s; targeted containers report running:\n' "$(git rev-parse --short HEAD)"
"${compose[@]}" ps "${services[@]}"
printf 'Browser and account/PC control checks are still required; running != healthy.\n'
