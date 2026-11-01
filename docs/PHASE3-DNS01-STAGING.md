# Phase 3 — Private HTTPS DNS-01 staging (opt-in, isolated)

**Status:** This repository provides an independent staging certificate issuer. It
does **not** change the running HomeAway gateway, Authelia, Grafana, Pi-hole,
WireGuard, public nameservers, or production TLS certificates when merged.

## Current DNS and operator decisions

- Registrar: Namecheap.
- Previous authoritative DNS: Dynu (`ns1.dynu.com` through `ns6.dynu.com`).
- Operator: verified the DNS inventory, confirmed Namecheap DNSSEC was off,
  updated the domain nameservers to Cloudflare, and created a Cloudflare API
  token. **Cloudflare's Active status and public NS convergence must still
  be checked before staging issuance.**
- The previous Dynu dynamic IP updater is no longer needed because the home
  has a static public IP. Existing WireGuard peers should still be checked
  for any old Dynu endpoint hostnames.
- Never post the API token in chat, GitHub, logs, or public sites. Existing
  DNS records and email settings should remain in place.

## Why staging is a separate container

`compose.dns01-staging.yaml` defines **only `dns01-staging`**. It is run
under a separate Docker Compose project named `homeaway-dns01-staging`.

- A pinned `caddy:2.11.7-builder-alpine` builds Caddy 2.11.7 with
  `github.com/caddy-dns/cloudflare@v0.2.4`.
- Caddy's only sites are the three staging names:
  `home.homeawayfromhome.house`,
  `auth.homeawayfromhome.house`, and
  `metrics.homeawayfromhome.house`, internally listening on `:8443`.
- Those are **not** application routes. The container publishes **no
  host ports**, cannot receive outside connections, and is on a separate
  outbound-only-purpose bridge rather than HomeAway's `infra` or
  `pc-control` bridge. It can contact Cloudflare DNS/ACME over the internet.
- It has a distinct `./data/control/dns01-staging/` certificate/config
  directory. It does **not** mount live Caddy's state or configuration.
  Nothing overwrites or restarts the live Caddy gateway.
- The zone-scoped Cloudflare token is mounted as a Docker secret **only**
  into the staging container. It is not a Compose environment value and
  is not placed in Git. The entrypoint loads the token privately.
- The only ACME CA used is Let's Encrypt **staging**
  (`acme-staging-v02.api.letsencrypt.org`). These certificates are
  intentionally **not trusted by browsers**. No production certificate
  or application hostname cutover occurs here.

DNS-01 issues certificates by adding/removing temporary public TXT
records under `_acme-challenge.*`. It requires outbound Cloudflare
and ACME access, but **no new public A/AAAA records, WAN ports, Cloudflare
Tunnel or proxy**. Use local Pi-hole and WireGuard DNS for private A
resolution in the *later* production cutover.

## Step 1 — Confirm Cloudflare is authoritative

Check Cloudflare's zone shows **Active**, and confirm externally:

```bash
nslookup -type=NS homeawayfromhome.house 1.1.1.1
```

The two reported nameservers should match those assigned by Cloudflare
for this exact zone, not Dynu. Do not run staging while delegation is
pending. Keep DNSSEC off until Cloudflare can later configure it
correctly with matching registrar DS records.

## Step 2 — Save token only on Pi

Use the custom Cloudflare token restricted to **Zone.DNS Edit** and
**Zone.Zone Read** for the single `homeawayfromhome.house` zone.

From the **clean Production checkout** on the Pi:

```bash
cd ~/HomeAwayFromHome
umask 077
mkdir -p secrets/control
read -r -s -p 'Cloudflare DNS-01 API token: ' cf_token
printf '\n'
printf '%s\n' "$cf_token" > secrets/control/cloudflare_dns01_token
unset cf_token
chmod 600 secrets/control/cloudflare_dns01_token
```

The token is never needed in the current HomeAway gateway; do not add
it to `.env`, Caddy's live configuration or other Compose overlays.

## Step 3 — Offline plan and isolated staging issuance

Only after Step 1 is confirmed and PR #28 is merged:

```bash
cd ~/HomeAwayFromHome
bash scripts/deploy-dns01-staging.sh --plan
bash scripts/deploy-dns01-staging.sh --apply
bash scripts/deploy-dns01-staging.sh --status
```

`--plan` is read-only. `--apply` builds a new, independent container
and causes real **staging** DNS-01 TXT changes at Cloudflare; this is
a narrow externally visible side effect. It **never touches** the
existing HomeAway gateway. Check the logs for successful ACME staging
issuance of all three names, and verify challenge TXT records are
cleaned up after issuance. If issuance fails, stop and inspect
the exact cause; do **not** change live Authelia/Caddy or switch to
production CA blindly.

To stop and remove only the standalone staging container/network:

```bash
bash scripts/deploy-dns01-staging.sh --stop
```

This leaves staged **untrusted** certificate files under
`data/control/dns01-staging/` for evidence; it does not touch the
running gateway. No full-system backup/restore rehearsal is required
for this isolated staging process.

## Step 4 — Production issuance and later hostname migration

Once staging issuance succeeds, a **separate reviewed PR** will switch
the new browser names to Let's Encrypt production certificates and
the actual protected reverse-proxy routes, with narrow rollback of
the existing Caddy/TOTP/Authelia configuration:

- preserve `homeaway.home.arpa` and its current working internal CA;
- keep the current private LAN `:8443` port, with no WAN forwarding;
- update private Pi-hole and WireGuard client DNS for `home`,
  `auth`, and `metrics` on the new domain;
- coordinate Authelia redirects/sessions, Caddy identity-header
  stripping, control API Origin/CSRF, Grafana URL/embeds and shortcuts;
- test TOTP, viewer/operator/admin roles, controls and remote access
  before considering the old URLs deprecated.

The Windows SensorDash TLS receiver keeps its **separate private CA**
and `https://PI_LAN_IP:8429/ingest`. Do not rotate or merge its TLS
identity into the browser migration.

## Acceptance / limitations

GitHub CI builds the pinned plugin and adapts the staging Caddyfile
using synthetic data. It verifies that the resolved Compose project
contains **only** the independent staging service, with no host ports,
no live gateway mounts, no other secrets or production network links.
CI never issues a certificate against the user's domain.

The existing `scripts/deploy-ui.sh` and `scripts/deploy-diagnostics.sh`
do not reference the staging overlay. Mere merging cannot cause a live
DNS or TLS cutover. No Phase 4 full-system disaster-recovery work is
part of this change.
