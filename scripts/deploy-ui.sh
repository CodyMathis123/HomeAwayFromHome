#!/usr/bin/env bash
# Targeted, operator-triggered UI deployment for an already-running HomeAway Pi.
# Does NOT pull images or restart Pi-hole, WireGuard, Grafana or other services.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: bash scripts/deploy-ui.sh --plan|--apply [--services both|portal|pc-control]

  --plan         Read-only preflight. Shows last-fetched Production versions.
  --apply        Fetch and fast-forward Production, then build/reconcile only
                 the selected already-running UI containers.
  --services     Default both (portal and pc-control); one service is allowed.

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
  portal|pc-control) services=("$selection") ;;
  *) usage; exit 2 ;;
esac

[[ "${EUID}" -ne 0 ]] || fail "Run this script as your normal user, not sudo."
command -v git >/dev/null || fail "git is required."
command -v python3 >/dev/null || fail "python3 is required."
command -v sudo >/dev/null || fail "sudo is required for Docker."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] || fail "Missing .env; refusing to initialize or change configuration."
[[ -f compose.yaml && -f compose.control.yaml && -f compose.accounts.yaml && -f compose.computers.yaml ]] ||
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
  --profile control
  --profile accounts
  --profile metrics)

# Validate all overlays without printing values that might contain credentials.
"${compose[@]}" config -q || fail "Compose validation failed; no changes applied."
project="$("${compose[@]}" config --format json |
  python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))')" ||
  fail "Cannot determine Compose project; no changes applied."
[[ "$project" == "homeawayfromhome" ]] ||
  fail "Unexpected Compose project name; refusing to touch containers."

# Do not unintentionally start an optional service or adopt another project.
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
