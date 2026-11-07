# Cooling-only Linux backend — isolated proof of concept

**Status: prototype on a review branch. Not production validated.**
This is not the full HomeAway installer, a .deb, an RPM or a migration script.
It can be tested on a fresh Linux machine with Docker Engine, Compose v2,
Python 3, OpenSSL and a static/reserved LAN IPv4 address. **Do not merge or
deploy onto the household Pi as part of design review.**

## What is included

Only these containers run, under the separate Compose project
'homeaway-cooling':

- **victoria-metrics** — 90-day telemetry retention, self-scrape only
- **grafana** — login-required SensorDash/cooling/electrical/reporting/events dashboards
- **telemetry-ingest** — original write-only SensorDash schema validator and token auth
- **gateway** — Caddy HTTPS with private CA; only host-bound 9443/tcp by default

No Pi-hole, WireGuard, portal, systemd PC agents, Authelia, LLDAP, domain
registration, Cloudflare account, host OS changes, or router changes.
No backend fan control. Windows SensorDash owns the collector and PWM.

### Security architecture

Windows ─ HTTPS (private Caddy CA; bearer token) ─ Caddy ─ verified HTTPS ─
telemetry-ingest ─ private bridge ─ VictoriaMetrics ─ Grafana ─ Caddy HTTPS

Caddy's public root certificate MUST be trusted explicitly by the clients.
Caddy internally verifies the separately generated upstream ingest cert by
its private CA and matching IP SAN. Caddy's root **private key**, the ingest
CA signing key, token, and passwords remain private. Never disable TLS
verification or use --insecure for normal operation.

Grafana uses its own admin login. One HTTPS port is published on the chosen
host LAN IP. It is NOT safe to forward that port from the public internet
without designing and validating a separate public exposure policy.

## Fresh-install procedure

Use a **separate test host** at first (generic Linux x86_64 or Pi OS aarch64).
Checkout the reviewed feature branch. Do not combine compose.cooling-only.yaml
with the household compose.yaml; they are intentionally independent.

1. Review the read-only bootstrap plan:

   ```bash
   python3 scripts/init-cooling-standalone.py --plan
   ```

2. Generate local credentials ONCE, choosing the host's current LAN IPv4.
   Default port 9443 is intentionally separate from your full Pi port 443:

   ```bash
   sudo python3 scripts/init-cooling-standalone.py \
     --init --bind-ip 192.168.1.50 --https-port 9443
   ```

   Optionally add `--tls-name cooling.example.lan` if that DNS name already
   resolves correctly for clients. Otherwise an IP-address certificate is
   issued. No system DNS configuration is created by the installer.

   The init script requires interactive typed confirmation; it NEVER
   overwrites existing secrets, starts containers or configures the router.
   It creates `deploy/cooling/.env` and `data/cooling/secrets/` with
   restricted file ownership for container users (Grafana UID 472, ingester
   UID 65534). Back up these credentials securely. If initialization fails
   partway through, inspect the partially created state instead of rerunning
   blindly.

3. Validate the isolated Compose project, then start deliberately:

   ```bash
   sudo docker compose --env-file deploy/cooling/.env \
     -f compose.cooling-only.yaml config -q
   sudo docker compose --env-file deploy/cooling/.env \
     -f compose.cooling-only.yaml config --services
   sudo docker compose --env-file deploy/cooling/.env \
     -f compose.cooling-only.yaml up -d --build
   sudo docker compose --env-file deploy/cooling/.env \
     -f compose.cooling-only.yaml ps
   ```

   Review that the services are ONLY victoria-metrics, grafana,
   telemetry-ingest and gateway before `up`.

4. The gateway generates its own public root CA when it first starts.
   After it is running, export ONLY that public certificate (not the key):

   ```bash
   cid="$(sudo docker compose --env-file deploy/cooling/.env \
     -f compose.cooling-only.yaml ps -q gateway)"
   sudo docker cp \
     "$cid:/data/caddy/pki/authorities/local/root.crt" data/cooling/caddy-root.crt
   ```

   Protect the Caddy Docker volume; it contains the local CA's private key.
   Compare certificate fingerprints and install the root certificate only
   where needed. SensorDash may use its explicitly configured CA trust
   option, or a reviewed Windows certificate-store import when using
   'Use Windows certificate trust'. Do not publish the private CA key.

5. Open `https://192.168.1.50:9443` on a client which trusts the public
   Caddy CA. Sign into Grafana as `admin`. Retrieve the generated
   Grafana password locally with
   `sudo cat data/cooling/secrets/grafana_admin_password`;
   do not paste it into logs, tickets or screenshots.

6. In **SensorDash → Settings → Pi telemetry export**, opt in to sending
   data to `https://192.168.1.50:9443/ingest`. Configure the receiver bearer
   token from `sudo cat data/cooling/secrets/telemetry/token` and the
   trusted public Caddy CA. No direct VictoriaMetrics port is exposed.
   Windows sensor identities and cooling bindings remain Windows-owned.

7. Verify a current sample timestamp, one host's CPU/GPU readings, one
   fan/cooling series, and an intentional transient backend outage while
   confirming Windows fan-control continuity. If the source uses a custom
   hostname, replace the example IP in the URL consistently.

## Stopping without deleting data

```bash
sudo docker compose --env-file deploy/cooling/.env \
  -f compose.cooling-only.yaml down
```

`down` retains the named metrics, Grafana and Caddy volumes.
**Do not use `down -v`** unless you have separately approved destruction of
historical data and the Caddy local CA.

Persistent named volumes are owned by the `homeaway-cooling` Docker
project, separate from HomeAway's original `data/` directories.
Backup and isolated restore of these volumes are not implemented in this
proof of concept. Do not treat container recreation as disaster recovery.

## Acceptance checklist

- `config --services` outputs exactly four expected services.
- No Docker ports published except the one selected HTTPS listener.
- Pi-hole and WireGuard are neither installed nor required for scraping.
- Grafana requires login and cannot be reached except via Caddy HTTPS.
- Anonymous POST to `/ingest` is 401; malformed authorized metrics are 422;
  valid SensorDash telemetry is accepted.
- Windows SensorDash fan curves still operate when the backend is down.
- No credentials appear in CI, command previews, log output or Git.
- Port/hostname, certificate rotation, container upgrades, snapshot and
  recovery are documented and live tested before a supported release.

## Known limitations and future work

- Caddy's local root CA must be distributed and trusted intentionally;
  a custom trusted certificate mode is future scope.
- The upstream ingest certificate is valid for **365 days**; there is no
  automatic rotation workflow yet. Plan renewal, not silent replacement.
- Fixed Grafana dashboard files are selected from the current repository;
  future manifest resolution will provision only installed features.
- Metrics and Grafana use Docker named volumes without a component-aware
  encrypted backup or restoration workflow.
- Fresh-install demonstration only: existing full-stack adoption, RPM/.deb,
  cross-distro packaging and component toggles are not implemented.
- The Caddy/Grafana auth pair is not a replacement for the advanced
  household accounts and role-control modules.
