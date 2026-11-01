#!/usr/bin/env bash
# One-time/add-on deployment of ONLY the stateless, read-only metrics observer.
# Never automatically starts DNS/VPN, restarts metrics/PC control, or changes .env.
set -euo pipefail
mode="${1:-}"
if [[ $# -ne 1 || "$mode" != --plan && "$mode" != --apply ]]; then
  echo "Usage: bash scripts/deploy-diagnostics.sh --plan|--apply" >&2
  exit 2
fi
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EUID}" -ne 0 ]] || die "Run as normal Pi user, not root."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ -f .env ]] || die "No existing HomeAway deployment .env; refusing to bootstrap."
[[ "$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)" == "Production" ]] ||
  die "Only deployed Production is allowed; do not run from a preview worktree."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  die "Checkout is dirty; inspect git status, do not discard changes."
for command in git python3 sudo docker; do
  command -v "$command" >/dev/null || die "Missing $command"
done
compose=(sudo docker compose -f compose.yaml -f compose.control.yaml
  -f compose.accounts.yaml -f compose.computers.yaml
  --profile control --profile accounts --profile metrics)
"${compose[@]}" config -q || die "Existing Compose config invalid."
project="$("${compose[@]}" config --format json | python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("name",""))')" ||
  die "Cannot identify Compose project."
[[ "$project" == "homeawayfromhome" ]] || die "Unexpected Compose project."
for service in pc-control victoria-metrics; do
  [[ -n "$("${compose[@]}" ps --status running --quiet "$service")" ]] ||
    die "Prerequisite $service is not running; no changes made."
done

echo "Ready: deploy metrics-diagnostics only, no published ports or secrets."
echo "Existing pc-control, VictoriaMetrics, Pi-hole and WireGuard stay untouched."
if [[ "$mode" == --plan ]]; then
  echo "Plan only: no images built and no containers changed."
  exit 0
fi
"${compose[@]}" up -d --no-deps --build metrics-diagnostics ||
  die "Observer launch failed; existing services were not requested."
[[ -n "$("${compose[@]}" ps --status running --quiet metrics-diagnostics)" ]] ||
  die "Observer not running; inspect metrics-diagnostics logs."
"${compose[@]}" ps metrics-diagnostics
echo "Observer started. Inspect Setup & Diagnostics as an admin."
