#!/usr/bin/env bash
# Explicit certificate-only production HTTPS migration. NEVER part of routine deploy.
set -euo pipefail
usage() {
  echo "Usage: bash scripts/deploy-dns01-production.sh --plan|--apply|--status|--rollback|--probe" >&2
}
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
(($# == 1)) || { usage; exit 2; }
mode="$1"
case "$mode" in --plan|--apply|--status|--rollback|--probe) ;; *) usage; exit 2 ;; esac
[[ "$EUID" -ne 0 ]] || die "Run as normal user (script invokes sudo only as needed)."
cd "$(dirname "${BASH_SOURCE[0]}")/.."
for command in git python3 sudo docker tar; do
  command -v "$command" >/dev/null || die "Missing command: $command"
done
[[ "$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)" == "Production" ]] ||
  die "Only Production branch is permitted."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] ||
  die "Git working tree has changes."
[[ -f .env ]] || die "Missing existing HomeAway .env"
files=(-f compose.yaml -f compose.control.yaml -f compose.accounts.yaml -f compose.computers.yaml)
profiles=(--profile control --profile accounts --profile metrics)
base=(sudo docker compose "${files[@]}" "${profiles[@]}")
prod=(sudo docker compose "${files[@]}" -f compose.dns01-production.yaml "${profiles[@]}")

# The certificate-only rollout predates canonical Phase 3 routing. Never let
# its --apply/--rollback overwrite a gateway already using the final config.
if [[ "$mode" == --apply || "$mode" == --rollback ]]; then
  live_cid="$(sudo docker ps -q --filter name=^/homeawayfromhome-gateway-1$ --filter status=running)"
  if [[ -n "$live_cid" ]]; then
    live_mount="$(sudo docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$live_cid")"
    if [[ "$live_mount" == "$(realpath control/gateway/Caddyfile.production)" ]]; then
      die "Canonical production gateway active. Certificate-only script cannot replace it."
    fi
  fi
fi

if [[ "$mode" == --status ]]; then
  "${base[@]}" ps --all gateway
  cid="$("${base[@]}" ps --all --quiet gateway)"
  if [[ -n "$cid" ]]; then
    sudo docker inspect --format 'Gateway status: {{.State.Status}}; restarts: {{.RestartCount}}; last exit code: {{.State.ExitCode}}' "$cid"
  fi
  "${base[@]}" logs --no-color --tail=200 gateway |
    python3 scripts/summarize_dns01_certificate_logs.py
  exit 0
fi
if [[ "$mode" == --rollback ]]; then
  "${base[@]}" config -q || die "Baseline Compose invalid; no rollback performed."
  echo "Recreating ONLY the original gateway (other services untouched)."
  "${base[@]}" up -d --no-deps --no-build --force-recreate gateway ||
    die "Rollback failed. Investigate local gateway logs immediately."
  [[ -n "$("${base[@]}" ps --status running --quiet gateway)" ]] ||
    die "Gateway not running after rollback."
  echo "Original gateway is running. Verify .home.arpa TOTP and controls in browser."
  exit 0
fi

secret=secrets/control/cloudflare_dns01_token
sudo test -s "$secret" || die "Root-owned Cloudflare token is missing or empty."
[[ "$(sudo stat -c '%u' "$secret")" == 0 ]] || die "Token must be root-owned."
perms="$(sudo stat -c '%a' "$secret")"
[[ "$perms" == 600 || "$perms" == 400 ]] || die "Token must be mode 600/400."
[[ -n "$("${base[@]}" ps --status running --quiet gateway)" ]] ||
  die "The live gateway must already be running."
sudo docker image inspect homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 >/dev/null ||
  die "Custom Caddy image missing; first complete isolated staging build."

# Temporarily store resolved configs in a private mode-0700 directory.
umask 077
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
"${base[@]}" config --format json > "$tmp/base.json" ||
  die "Existing Compose invalid."
"${prod[@]}" config --format json > "$tmp/production.json" ||
  die "Production opt-in Compose invalid."
python3 scripts/check_dns01_production_resolved.py \
  "$tmp/base.json" "$tmp/production.json" ||
  die "Unexpected gateway/stack changes. Refusing restart."
python3 scripts/check_dns01_gateway_https.py "$tmp/base.json" ||
  die "The old .home.arpa gateway is not healthy; no production change attempted."
echo "Verified: only gateway image, entrypoint, Caddyfile, and scoped token change."
echo "Existing .home.arpa routing is preserved; new names return HTTP 503 placeholders."
if [[ "$mode" == --plan ]]; then
  echo "PLAN ONLY; no snapshot, DNS change or service restart."
  exit 0
fi

# Offline runtime validation of the exact combined Caddyfile and Pi hostname
# settings. No live cert state, real token, network, host ports or other
# HomeAway containers are present. The synthetic token can never edit DNS.
python3 scripts/extract_dns01_gateway_env.py "$tmp/base.json" > "$tmp/legacy-hosts.env" ||
  die "Could not safely extract only the legacy hostnames for offline validation."
echo "Checking custom Caddy startup configuration offline (network disabled)."
if ! sudo docker run --rm --network none --cap-drop ALL \
    --security-opt no-new-privileges:true --read-only \
    --tmpfs /data --tmpfs /config --tmpfs /tmp \
    --env-file "$tmp/legacy-hosts.env" \
    -e CF_API_TOKEN=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA \
    -v "$PWD/control/gateway/Caddyfile.dns01-production:/etc/caddy/Caddyfile:ro" \
    -v "$PWD/control/gateway/Caddyfile:/etc/caddy/Caddyfile.legacy:ro" \
    --entrypoint /usr/local/bin/homeaway-caddy \
    homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 \
    validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null; then
  die "Offline Caddy validation failed. Live gateway left untouched."
fi
echo "PASS: combined Caddy config validated without network, DNS or live state."
if [[ "$mode" == --probe ]]; then
  echo "PROBE ONLY; no live gateway changes or certificate requests."
  exit 0
fi

# Operator-triggered live gateway restart. Snapshot Caddy CA/key material first.
sudo test -d data/control/caddy || die "Missing existing Caddy TLS state."
sudo test -d data/control/caddy-config || die "Missing existing Caddy config state."
sudo install -d -m 700 backups/phase3-https
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="backups/phase3-https/caddy-before-dns01-$stamp.tar.gz"
sudo tar -czf "$archive" \
  control/gateway/Caddyfile data/control/caddy data/control/caddy-config ||
  die "Caddy snapshot failed. Gateway left untouched."
sudo chmod 600 "$archive"
echo "Protected Caddy-only snapshot: $archive (contains PRIVATE KEY material)."
# A container that briefly enters the 'running' state can still crash-loop.
# Preserve failed gateway logs before Compose removes the failed container.
# Then restore the original gateway automatically and return nonzero.
rollback_failed_apply() {
  local failure_log="backups/phase3-https/gateway-startup-failed-$stamp.log"
  sudo install -m 600 /dev/null "$failure_log"
  if ! "${prod[@]}" logs --no-color --tail=250 gateway |
      sudo tee "$failure_log" >/dev/null; then
    echo "WARNING: Could not capture complete failed gateway logs." >&2
  fi
  echo "New gateway failed health checks; diagnostic logs saved root-only: $failure_log" >&2
  echo "Automatically restoring ONLY the original gateway now." >&2
  if ! "${base[@]}" up -d --no-deps --no-build --force-recreate gateway; then
    die "Automatic rollback FAILED. Keep SSH access and inspect the gateway locally."
  fi
  sleep 3
  if ! python3 scripts/check_dns01_gateway_https.py "$tmp/base.json"; then
    die "Rollback container started, but legacy HTTPS probe failed. Check gateway logs locally."
  fi
  die "Production cert rollout FAILED and was rolled back; no app URL migration occurred."
}

echo "Replacing ONLY gateway with production DNS-01 Caddy (brief HTTPS interruption)."
if ! "${prod[@]}" up -d --no-deps --no-build --force-recreate gateway; then
  rollback_failed_apply
fi

# 6 consecutive independent observations; require zero Docker restart count
# AND all three old HTTPS names to answer each time (rather than one moment).
for attempt in 1 2 3 4 5 6; do
  sleep 3
  cid="$("${prod[@]}" ps --all --quiet gateway)" || rollback_failed_apply
  [[ -n "$cid" ]] || rollback_failed_apply
  state="$(sudo docker inspect --format '{{.State.Status}} {{.RestartCount}}' "$cid")" ||
    rollback_failed_apply
  [[ "$state" == "running 0" ]] || rollback_failed_apply
  python3 scripts/check_dns01_gateway_https.py "$tmp/base.json" ||
    rollback_failed_apply
done

echo "Gateway stable with zero restarts and all 3 legacy HTTPS names healthy."
echo "Production certificate issuance NOT yet verified. Run --status."
echo "Verify browser TOTP/role-gated control before beginning any hostname migration."
