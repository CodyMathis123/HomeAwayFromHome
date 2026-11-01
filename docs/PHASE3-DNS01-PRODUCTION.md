# Phase 3 — Opt-in production certificate issuance, NOT application cutover

**Status: review/CI first.** This is a deliberately narrow milestone after
all three Let's Encrypt **staging** DNS-01 authorizations and certificates
succeeded on the Pi (November 1, 2026). No production-certificate issuance,
live gateway restart or DNS/Authelia changes are performed by GitHub.

## First live rollout incident and recovery guard

The November 1, 2026 production DNS-01 `--apply` recreated the
gateway, but it immediately entered a Docker restart loop
(`Restarting (1)`); desktop `curl --resolve` to the Pi LAN IP timed
out. The operator restored the original `.home.arpa` gateway using
`--rollback`. The **root cause is not yet verified**, because the
earlier `--status` command printed only successful certificate
events and did not expose startup failures. Do not blame Cloudflare's
missing public A records: `curl --resolve` bypasses DNS, and ACME
DNS-01 staging had already succeeded.

The recovery hardening in this change adds the following safeguards:

- `--plan` requires a successful private HTTPS response from all
  three original `.home.arpa` SNI hostnames before any production
  rollout is considered.
- `--probe` performs a **real Caddy config validation** in a
  throwaway container with the three original Pi hostname variables,
  synthetic Cloudflare token, **no network, no published ports and no
  access to live Caddy state**. `--apply` requires the same probe
  before changing the live gateway.
- After a live restart, the script checks Docker restart count is
  **zero** and all three original HTTPS endpoints continue responding
  repeatedly. A momentary `running` state is not success.
- If the rollout fails, the script captures startup logs to a
  root-only `backups/phase3-https/gateway-startup-failed-*.log` file
  **before** the failed gateway is recreated, then automatically
  rolls back the gateway to the original image/config.
- The failure log is privileged diagnostic information. Review it
  locally and redact it before sharing. The rollback remains
  available manually if automatic recovery is incomplete.

This does **not** identify the first incident's root cause. The
next gateway rollout should not be attempted until the guarded
probe passes; if the same startup failure recurs, the saved logs
will allow a focused correction without losing diagnostic evidence.

## Exactly what the opt-in overlay changes

The existing live Caddy gateway is `caddy:2.11.7-alpine` with private
`.home.arpa` websites and internal CA certs. The reviewed opt-in overlay
`compose.dns01-production.yaml` replaces **only that gateway's** image,
entrypoint, mounted Caddyfile, and mounted **gateway-only** Cloudflare
token secret.

The new Caddyfile imports the existing original
`control/gateway/Caddyfile` unchanged and adds **three distinct sites**:

- `home.homeawayfromhome.house:8443`
- `auth.homeawayfromhome.house:8443`
- `metrics.homeawayfromhome.house:8443`

They obtain **publicly trusted** certificates through Let's Encrypt's
production ACME server using Cloudflare's DNS-01 API. Each new hostname
currently responds **HTTP 503 — application routing not yet enabled**.
**None** of the new names reverse-proxies HomeAway, Authelia, Grafana,
the control API or any login page. This is intentional, to avoid a
partially migrated login/session/cookie origin.

The existing `homeaway.home.arpa:8443`,
`auth.homeaway.home.arpa:8443`, and
`metrics.homeaway.home.arpa:8443` retain their current Caddy
`tls internal` certificates and authenticated routing, which should be
tested immediately after the change.

No external A/AAAA record or WAN port 80/443 is required. Port 8443
remains bound only to the configured `PI_LAN_IP`. Cloudflare DNS-01
temporarily creates public TXT records. Pi-hole/WireGuard DNS,
Authelia TOTP and SQLite, LLDAP groups, Grafana links, PC controls,
SensorDash certificates, and all other service configurations stay
untouched.

## Important operational gate

This is **different from isolated staging**: `--apply` and `--rollback`
each **recreate the live Caddy gateway**, causing a brief interruption
to current browser HTTPS. Do not do it during an active control session.
You must retain SSH access to the Pi independently of Caddy.

Before the operator chooses `--apply`:

1. Verify Cloudflare authoritative DNS is Active, and that all three
   staging certificates succeeded (the log output is already recorded).
2. Stop the separate staging container to prevent overlapping issuance:
   `bash scripts/deploy-dns01-staging.sh --stop`.
3. Keep the zone-scoped API token at
   `secrets/control/cloudflare_dns01_token`, owner `root`,
   mode `600` (or `400`). Do not paste it or check it into Git.
4. Confirm the normal `Production` checkout is clean and includes
   the merged reviewed production-certificates PR. Confirm existing
   `.home.arpa` login and control screen are working.
5. Know the immediate rollback command below. No client private
   DNS settings or browser favorites should be changed yet.

## Pi deployment

Run **as the normal Pi user**, not `sudo bash`:

```bash
cd ~/HomeAwayFromHome
git pull --ff-only origin Production
bash scripts/deploy-dns01-production.sh --plan
bash scripts/deploy-dns01-production.sh --probe
```

The plan validates the complete resolved Compose configuration against
the original, rather than silently changing other services. It checks
that the live gateway is running, the token is protected and the custom
Caddy image exists. It creates no backup and restarts nothing.

**Only after explicitly deciding to restart the live gateway**, run:

```bash
bash scripts/deploy-dns01-production.sh --apply
bash scripts/deploy-dns01-production.sh --status
```

The script creates a narrow, root-only snapshot of
`control/gateway/Caddyfile`, `data/control/caddy` and
`data/control/caddy-config` under
`backups/phase3-https/caddy-before-dns01-*.tar.gz` **before**
restarting Caddy. That archive contains **private keys**: never share
or upload it. It is intentionally not a full-system backup.

The custom Caddy build is the one validated and used in the standalone
staging container. The operator script requests `--no-build` so the Pi
does **not** recompile the plugin for the production cutover.

The `--status` helper prints only allowlisted certificate summaries.
For each of the three names, look for **Production certificate issued**.
A missing line may just mean old Caddy log entries rolled out of the
200-line status window. Browser certificate verification below is the
final check.

## Direct TLS verification without changing Pi-hole yet

From a Windows/Linux/macOS machine that can reach the Pi LAN IP,
use curl's temporary per-request DNS override. Substitute the **actual
private Pi IP** in place of `PI_LAN_IP`:

```bash
curl -I --resolve home.homeawayfromhome.house:8443:PI_LAN_IP https://home.homeawayfromhome.house:8443/
curl -I --resolve auth.homeawayfromhome.house:8443:PI_LAN_IP https://auth.homeawayfromhome.house:8443/
curl -I --resolve metrics.homeawayfromhome.house:8443:PI_LAN_IP https://metrics.homeawayfromhome.house:8443/
```

Expect an **HTTP 503** response over **TLS with no certificate trust
error**, and verify each site's SAN and issuing CA with your browser or
TLS client. The 503 is the *intended placeholder*, not an application
outage. Do not use `curl -k` here: bypassing certificate verification
would defeat the trust test.

**Immediately recheck** the existing `homeaway.home.arpa:8443` page,
Authelia TOTP and role-gated /control operations using the normal
client browser. Confirm Pi-hole, WireGuard, Grafana and SensorDash
remain normal. Do not attempt real destructive PC actions solely to
validate this certificates-only milestone.

## Targeted rollback

If old `.home.arpa` authentication or routing regresses, from SSH run:

```bash
cd ~/HomeAwayFromHome
bash scripts/deploy-dns01-production.sh --rollback
```

This uses the original `compose.yaml`, `compose.control.yaml`,
`compose.accounts.yaml`, and `compose.computers.yaml` without the
new overlay. It recreates **only** the original gateway image and
Caddyfile; it preserves the original internal CA data/config mounts.
The old `.home.arpa` names should recover without editing DNS,
Authelia secrets or certificates.

**Rollback is a brief gateway restart, not an instantaneous operation.**
The narrow snapshot is there for manual recovery if Caddy's persistent
data is damaged; rollback does **not** automatically overwrite
certificate databases or restore old keys.

## After successful production issuance

The **next separate reviewed change** moves the actual applications
to the new names, including:

- private Pi-hole A records and remote WireGuard DNS resolution;
- both old and new Authelia session cookie domains and their allowed
  redirect URLs, retaining TOTP and LLDAP first-factor state;
- controller origin/CSRF rules, Grafana root URL and portal links;
- real desktop/mobile browser and remote WireGuard regression tests.

No production application cutover is authorized by this PR. Windows
SensorDash telemetry remains separate, at `https://PI_LAN_IP:8429/ingest`
using its own private CA.
