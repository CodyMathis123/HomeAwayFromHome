# Phase 3: dual-domain application cutover (operator-run, opt-in)

Status: **not deployed by merging this branch**. This uses the already-issued DNS-01 certificates and private Pi-hole records. Keep router ports 8443/53 closed to WAN.

## Prerequisites

- The production DNS-01 Caddy gateway is healthy, using `compose.dns01-production.yaml`.
- All three `*.homeawayfromhome.house` names resolve privately to the Pi and have trusted HTTPS certificates.
- Authelia has been **manually** configured and validated to keep its existing `homeaway.home.arpa` cookie and to add:
  ```yaml
  - domain: "homeawayfromhome.house"
    authelia_url: "https://auth.homeawayfromhome.house:8443"
    default_redirection_url: "https://home.homeawayfromhome.house:8443"
  ```
- Authelia access rules retain `default_policy: deny` and the `two_factor` control-resource rule for both `homeaway.home.arpa` and `home.homeawayfromhome.house`. Confirm the original TOTP flow still works.
- The working tree is clean and the branch has been manually selected for this rollout. Do not use `scripts/deploy-dns01-production.sh --apply` for Phase 3 application routing: that script is certificate-only and its rollback recreates the original gateway.

## Preflight (no service changes)

```bash
cd ~/HomeAwayFromHome
git status --short
sudo docker exec homeawayfromhome-authelia-1 authelia config validate --config /config/configuration.yml
sudo grep -nE 'domain:|authelia_url:|default_redirection_url:|default_policy:|policy: two_factor' data/control/authelia/configuration.yml
python3 -m py_compile control/api/app.py
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml -f compose.computers.yaml -f compose.dns01-production.yaml -f compose.phase3-domain.yaml --profile control --profile accounts --profile metrics config -q
```

Check both actual cookie entries and the two-factor rule; `config -q` alone does not validate Authelia semantics. The new Caddyfile must be world-readable on the Pi because this gateway uses restricted container privileges. Git does not track file read bits, so perform this on every fresh checkout **before** validation or restart:

```bash
chmod 644 control/gateway/Caddyfile.phase3-domain
stat -c '%a %n' control/gateway/Caddyfile.phase3-domain
test "$(stat -c '%a' control/gateway/Caddyfile.phase3-domain)" = 644 || exit 1
```

Do not change `secrets/control/cloudflare_dns01_token` permissions. A mode-600 Phase 3 Caddyfile caused an actual live Caddy restart loop; validate offline before trying the gateway again.

Validate the exact Caddyfile offline, using a synthetic token and no networking:

```bash
set -e
cd ~/HomeAwayFromHome
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml -f compose.computers.yaml --profile control --profile accounts --profile metrics config --format json > "$tmp/base.json"
python3 scripts/extract_dns01_gateway_env.py "$tmp/base.json" > "$tmp/hosts.env"
sudo docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true --read-only \
  --tmpfs /data --tmpfs /config --tmpfs /tmp \
  --env-file "$tmp/hosts.env" -e CF_API_TOKEN=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA \
  -v "$PWD/control/gateway/Caddyfile.phase3-domain:/etc/caddy/Caddyfile:ro" \
  -v "$PWD/control/gateway/Caddyfile:/etc/caddy/Caddyfile.legacy:ro" \
  --entrypoint /usr/local/bin/homeaway-caddy homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 \
  validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

## Back up and stage

```bash
cd ~/HomeAwayFromHome
stamp=$(date -u +%Y%m%dT%H%M%SZ)
sudo install -d -m 700 backups/phase3-https
sudo tar -czf "backups/phase3-https/domain-cutover-$stamp.tar.gz" \
  data/control/authelia data/control/caddy data/control/caddy-config \
  control/gateway/Caddyfile
sudo chmod 600 "backups/phase3-https/domain-cutover-$stamp.tar.gz"
```

That archive contains sensitive private material. Store it off-device securely for disaster recovery. No Authelia database or secret replacement is needed for this release.

## Apply (brief interruption of gateway, portal, control API and Grafana)

Build the control API before touching the running gateway:

```bash
cd ~/HomeAwayFromHome
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml -f compose.computers.yaml -f compose.dns01-production.yaml -f compose.phase3-domain.yaml --profile control --profile accounts --profile metrics build pc-control
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml -f compose.computers.yaml -f compose.dns01-production.yaml -f compose.phase3-domain.yaml --profile control --profile accounts --profile metrics up -d --no-deps --no-build gateway pc-control portal grafana
```

Do not use an unscoped `docker compose up`: DNS/VPN and account services must remain untouched.

## Verify

1. `curl -I https://home.homeawayfromhome.house:8443/` should answer portal (note its Python server may return 501 to HEAD; test GET in browser).
2. `https://auth.homeawayfromhome.house:8443` shows Authelia; complete username/password + TOTP.
3. `https://home.homeawayfromhome.house:8443/control/` requires authentication, and admins/operators/viewers retain existing role restrictions. Verify a harmless GET and, with an appropriate test PC, a single permitted action. Verify cross-origin POSTs and anonymous requests are rejected.
4. `https://metrics.homeawayfromhome.house:8443` routes to Grafana with a trusted certificate; dashboard embeds still load.
5. Old `https://homeaway.home.arpa:8443/control/`, `auth.homeaway.home.arpa:8443` and `metrics.homeaway.home.arpa:8443` remain reachable.
6. Check `docker ps` and browser workflows, not just container process states.

## Roll back application routing (leave certificates and Authelia storage intact)

Use the original production DNS-01 overlay **without** `compose.phase3-domain.yaml`; this restores the 503 placeholders and legacy app environment. The new control API binary retains compatibility with the old origin.

```bash
cd ~/HomeAwayFromHome
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml -f compose.computers.yaml -f compose.dns01-production.yaml --profile control --profile accounts --profile metrics up -d --no-deps --no-build --force-recreate gateway pc-control portal grafana
```

Verify the legacy Authelia TOTP and controls after rollback. Do not restore the sensitive archive on top of running containers. If Authelia itself is broken, recover its prior verified configuration separately and restart only Authelia.
