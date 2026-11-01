# Production consolidation: homeawayfromhome.house only

The canonical production stack is `compose.yaml`, `compose.control.yaml`, `compose.accounts.yaml`, `compose.computers.yaml`, and **`compose.production.yaml`** in that order. Historical `compose.dns01-production.yaml` and `compose.phase3-domain.yaml` are rollback-only and must never be used for routine deployments.

This cutover removes the `.home.arpa` HTTPS sites from the gateway and makes the new hostname the *only* valid browser POST origin for computer controls. Internal LLDAP directory identifiers containing `.home.arpa` do not need to change: changing the directory base DN would be a separate risky data migration.

## Before applying

- Have independent SSH access to the Pi and a local recovery path.
- Confirm `https://home.homeawayfromhome.house:8443/control/` currently works, including TOTP and a permitted control action. Pi-hole must already resolve home/auth/metrics privately.
- Authelia must still have the validated `homeawayfromhome.house` cookie and 2FA access-control rule. **Do not remove the old cookie yet.**
- Verify a clean feature checkout with `git status --short`. No changes to the Cloudflare token, app secrets, or live Authelia database.
- Don't use `scripts/deploy-dns01-production.sh --apply` or an unscoped `docker compose up`.

## Validate on Pi (no live changes)

```bash
cd ~/HomeAwayFromHome
chmod 644 control/gateway/Caddyfile.production
python3 -m py_compile control/api/app.py
sudo docker exec homeawayfromhome-authelia-1 authelia config validate --config /config/configuration.yml

compose=(sudo docker compose -f compose.yaml -f compose.control.yaml \
  -f compose.accounts.yaml -f compose.computers.yaml -f compose.production.yaml \
  --profile control --profile accounts --profile metrics)
"${compose[@]}" config -q
```

Verify the offline Caddyfile without DNS, network, host ports, or real token:

```bash
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
sudo docker compose -f compose.yaml -f compose.control.yaml \
  -f compose.accounts.yaml -f compose.computers.yaml \
  --profile control --profile accounts --profile metrics config --format json \
  > "$tmp/base.json"
python3 scripts/extract_dns01_gateway_env.py "$tmp/base.json" > "$tmp/hosts.env"
sudo docker run --rm --network none --cap-drop ALL \
  --security-opt no-new-privileges:true --read-only \
  --tmpfs /data --tmpfs /config --tmpfs /tmp \
  --env-file "$tmp/hosts.env" \
  -e CF_API_TOKEN=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA \
  -v "$PWD/control/gateway/Caddyfile.production:/etc/caddy/Caddyfile:ro" \
  --entrypoint /usr/local/bin/homeaway-caddy \
  homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 \
  validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

If validation fails, **do not deploy**. Inspect and correct the error in this branch first.

## Cutover (one gateway, one control API)

Create a private backup of the data/certificates and relevant configuration:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
sudo install -d -m 700 backups/phase3-https
sudo tar -czf "backups/phase3-https/house-only-$stamp.tar.gz" \
  data/control/authelia data/control/caddy data/control/caddy-config \
  control/gateway/Caddyfile.phase3-domain
sudo chmod 600 "backups/phase3-https/house-only-$stamp.tar.gz"
```

The archive contains credentials/private material: keep it protected and copy it securely off the Pi for disaster recovery. These commands do not modify Authelia state.

```bash
"${compose[@]}" build pc-control
"${compose[@]}" up -d --no-deps --no-build --force-recreate gateway pc-control
```

The portal and Grafana settings are unchanged from the working Phase 3 deployment; they do **not** need to be restarted for this cutover.

## Validate

- Check `sudo docker ps -a --filter name=homeawayfromhome-gateway` and `sudo docker ps -a --filter name=homeawayfromhome-pc-control` for **running**, zero restarts.
- On Windows, open the new home/auth/metrics URLs. Confirm trusted HTTPS, login + TOTP, portal embedded Grafana, and the control page.
- Verify one permitted command on the intended PC, and confirm unauthenticated access is denied.
- Verify that `https://homeaway.home.arpa:8443/` no longer serves the legacy portal (the old SNI hostname may yield TLS failure or an HTTP error).
- The Pi itself may still use external DNS and fail to resolve the new private hostname. For a local check, use `curl --resolve home.homeawayfromhome.house:8443:192.168.1.43 https://home.homeawayfromhome.house:8443/` instead of changing host DNS during this cutover.

## Rollback (if required)

Restore exactly the previously working dual-domain routes without changing Authelia data:

```bash
legacy=(sudo docker compose -f compose.yaml -f compose.control.yaml \
  -f compose.accounts.yaml -f compose.computers.yaml \
  -f compose.dns01-production.yaml -f compose.phase3-domain.yaml \
  --profile control --profile accounts --profile metrics)
"${legacy[@]}" up -d --no-deps --no-build --force-recreate gateway pc-control
```

The newly built control API retains an explicit allowlist for the old and new origin when the dual-domain overlay sets them. Confirm legacy and new TOTP/control behavior after rollback. If a rollback fails, inspect the gateway container and saved private backup; do not blindly reset Git, overwrite running Authelia storage or restart DNS/VPN.

## After validation and merge

- Switch the Pi back to `Production` after the PR merges; checkout changes alone don't recreate containers.
- Use `bash scripts/deploy-ui.sh --plan` before future UI updates. It verifies the gateway actually mounts `Caddyfile.production`.
- Historical DNS-01 scripts and overlays are for recovery only; do not run legacy certificate-only `--apply`.
- Remove old `.home.arpa` cookie and access rule from the live Authelia YAML **only after** checking all clients/shortcuts, taking a second private backup and validating the edited file before restarting Authelia. The production Caddyfile already stops serving those hostnames, so this cleanup is not necessary for the new routes to function.
