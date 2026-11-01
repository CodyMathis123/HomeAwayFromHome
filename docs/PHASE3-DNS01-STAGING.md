# Phase 3 — DNS-01 staging preparation (not a live migration)

**Status: opt-in code only.** This PR packages a pinned Caddy build with the
Cloudflare DNS provider and a staging Caddyfile. It does not change any
running container, live DNS, nameserver, router, Authelia cookie,
`.env` setting, SensorDash certificate, or certificate in production.

The existing internal-CA sites are imported unchanged from
`control/gateway/Caddyfile`. The proposed public names get *temporary
staging validation responses*, not the authenticated HomeAway app.
**Do not use the staging names for real computer controls or sign-in.**

## Intended addresses, with no new WAN access

- `home.homeawayfromhome.house:8443`
- `auth.homeawayfromhome.house:8443`
- `metrics.homeawayfromhome.house:8443`

The Namecheap registration can stay where it is. Cloudflare is the
**proposed authoritative DNS host**, not an assumption about the
currently active public nameservers. Pi-hole will eventually resolve
these names to the Pi's reserved LAN IP, and WireGuard clients will
use the same split-horizon DNS. No new public A/AAAA record, Cloudflare
proxy/Tunnel, port 80/443 forwarding or public gateway is needed for
DNS-01. WireGuard's existing UDP 51820 remains unchanged.

The pinned build uses `caddy:2.11.7-builder-alpine` and
`github.com/caddy-dns/cloudflare@v0.2.4`, with a small
`caddy:2.11.7-alpine` runtime. The existing Caddy data/config volumes,
private :8443 binding, Authelia forward-auth and header stripping are
preserved. The Cloudflare token is **only** mounted as a Docker secret
in the gateway, never included in Compose environment values, CLI
arguments, Git, Grafana or browser output. The fixed entrypoint reads
the secret into Caddy's process environment and does not log its value.

## Verified migration context — November 1, 2026

The current Namecheap registrar screen for `homeawayfromhome.house`
shows **Custom DNS** with six Dynu nameservers:

- `ns1.dynu.com`, `ns2.dynu.com`, `ns3.dynu.com`
- `ns4.dynu.com`, `ns5.dynu.com`, `ns6.dynu.com`

Dynu was previously used for a changing home IP address. The operator
reports the home now has a **static public IP** and no longer needs Dynu's
dynamic-IP updater. The intended move is **authoritative DNS only**:
Namecheap continues to be registrar, Cloudflare becomes DNS host, and
the HomeAway Pi remains private on the LAN/WireGuard network.

This screenshot confirms the configured nameservers but **not** the
contents of the Dynu zone, whether a DS record is published, whether
mail/forwarding records exist, or the values of old WireGuard peers.
Do not infer any of those settings from the screenshot.

### Dynu → Cloudflare migration gate (operator actions)

1. In **Dynu DNS Control Panel**, inspect the exact
   `homeawayfromhome.house` zone. Save/export all records; include
   apex, wildcard, subdomain, MX, TXT (SPF/DKIM/DMARC/verification),
   CAA, CNAME, redirects and any service-specific DNS records.
   Retain a copy outside Git; don't post tokens or private keys.
2. In **Cloudflare**, select **Add a domain** and enter
   `homeawayfromhome.house` (Free DNS is sufficient for the proposed
   DNS-01 workflow). Let the initial scan run, then compare all
   imported records against the Dynu record inventory.
   **Autoscanning is not a complete zone transfer.**
3. Do not add public A/AAAA records for private `home`,
   `auth`, or `metrics` services just to issue certificates.
   DNS-01 uses public TXT records, while local Pi-hole/WireGuard DNS
   will eventually resolve the private names.
4. Before switching NS, check for DNSSEC DS records at **Namecheap**.
   If an old Dynu DS record exists, safely disable/remove it and wait
   for caches to expire before switching. Plan to re-enable DNSSEC
   with Cloudflare's assigned DS data only after activation.
5. In **Namecheap → Domain List → Manage → Domain → Nameservers**,
   replace all six Dynu entries **only after** Cloudflare's zone
   records match the Dynu inventory and the DNSSEC gate is cleared.
   Select the exact two (or other number explicitly provided by
   Cloudflare) nameservers assigned to *this* zone. Save once.
6. Verify Cloudflare zone status **Active**, public NS delegation,
   important old website/email record resolution, and the absence
   of unexpected redirects. Do not delete Dynu records or shut down
   an existing updater until the cutover and dependents are verified.
7. Confirm that existing WireGuard peers and any outside apps do not
   still use a Dynu-updated host name; a static IP makes the old
   updater unnecessary, but does not automatically rewrite saved
   WireGuard endpoint settings.

**Rollback:** Record the six Dynu nameservers and all original records.
If Cloudflare delegation fails, restoring old nameservers in Namecheap
is an emergency DNS rollback, not an instantaneous fix: resolvers may
continue caching records and delegation until TTL expiry. This DNS
rollback is separate from the later Caddy/Authelia TLS rollback.

## Operator gate 0 — offline build and validation

Safe to run on a separate development computer or isolated Pi worktree;
it neither issues certificates nor changes DNS:

```bash
docker build -f control/gateway/Dockerfile.dns01 \
  -t homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 \
  ./control/gateway

docker run --rm --entrypoint caddy \
  homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4 \
  list-modules | grep -Fx dns.providers.cloudflare
```

GitHub CI also adapts the staging Caddyfile **offline** with
synthetic hostnames and an intentionally fake token and checks that the
resolved Compose overlay has no new published ports or secrets outside
the gateway. This does **not** prove live ACME issuance, Cloudflare
zone authority or client trust.

## Operator gate 1 — public DNS inventory and approval

Before touching the registrar or provisioning a token, identify and
record the **actual** public DNS provider and registrar. Export every
existing DNS record and verify parity (A, AAAA, CNAME, MX, TXT, SPF,
DKIM, DMARC, CAA, SRV, forwarding/redirect and third-party services)
before a nameserver migration. Cloudflare's quick import is not proof
of complete parity. Make sure existing website/email behavior is
accounted for.

If a DNSSEC DS record exists at the registrar, plan the safe DS
transition before changing authoritative nameservers. Never change
NS/DNSSEC just because the staging files are present. A narrowly
delegated `_acme-challenge` CNAME is another option if full
nameserver migration is unnecessary; that alternative would need its
own provider configuration and review.

Confirm that the domain is yours and that Cloudflare is truly
authoritative before attempting DNS-01 with Cloudflare. Obtain an
explicit approval for every actual DNS/NS change. No GitHub workflow
performs provider writes.

## Operator gate 2 — staging token and private configuration

Only after the DNS provider/records are reviewed, create a Cloudflare
API token scoped to the `homeawayfromhome.house` zone, with
`Zone.Zone:Read` and `Zone.DNS:Edit`. Do not use a global key.
The token can edit other records in its assigned zone; store it
accordingly.

On the Pi, create the token file with user-restricted access,
**without typing its value into a command or committing it**:

```bash
cd ~/HomeAwayFromHome
umask 077
read -r -s -p 'Cloudflare zone DNS token: ' cf_token
printf '\n'
printf '%s\n' "$cf_token" > secrets/control/cloudflare_dns01_token
unset cf_token
chmod 600 secrets/control/cloudflare_dns01_token
```

The example names are in `.env.dns01.example`; they do not modify the
existing `.env`. In the *current terminal* supply the three new
variables for an **explicit opt-in staging invocation**:

```bash
export HOMEAWAY_PUBLIC_HOME=home.homeawayfromhome.house
export HOMEAWAY_PUBLIC_AUTH=auth.homeawayfromhome.house
export HOMEAWAY_PUBLIC_METRICS=metrics.homeawayfromhome.house
```

The staging Compose overlay is `compose.dns01-staging.yaml`.
**Never append it to the routine deploy-ui.sh command.**

## Operator gate 3 — staging issuance and narrow rollback

A live staging exercise will require a *separate explicit go-ahead*
because applying its overlay restarts the running gateway and writes
temporary `_acme-challenge` TXT records in public DNS. It will use
`https://acme-staging-v02.api.letsencrypt.org/directory`, not the
production Let's Encrypt CA. Staging certificates are untrusted by
normal browsers. This validates TXT creation, propagation and cleanup,
not production browser trust.

Before any staging cutover, preserve a narrowly scoped local copy of
the current Caddy configuration/data and the active HTTPS/auth
settings, plus a tested path to restart the existing stock gateway
with its current `.home.arpa` names. That is targeted rollback,
**not** Phase 4 full-system backup/restore verification.

Only after this gate passes should a subsequent PR:
- move the new hostnames from staging stubs to full, protected routes;
- configure Let's Encrypt production issuance and Caddy automatic renewal;
- coordinate Authelia cookie/redirect names, Caddy SNI, control API
  Origin/CSRF, Grafana root URL/embeds, Pi-hole LAN/VPN DNS, and iOS links;
- verify login/TOTP, viewer/operator/admin authorization, computer
  power actions and remote WireGuard access;
- retain the working `.home.arpa` endpoints for rollback.

Do not regenerate LLDAP/Authelia account state or rotate Windows
SensorDash telemetry CA as part of browser HTTPS. The telemetry
receiver remains separate on private port 8429.

## References

- [Caddy official custom-module builder](https://hub.docker.com/_/caddy)
- [Cloudflare DNS provider and minimum token permissions](https://github.com/caddy-dns/cloudflare)
- [Caddy TLS DNS challenge and ACME staging](https://caddyserver.com/docs/caddyfile/directives/tls)
- [Cloudflare authoritative nameserver setup](https://developers.cloudflare.com/dns/nameservers/update-nameservers/)
- [Let's Encrypt DNS-01](https://letsencrypt.org/docs/challenge-types/)

**Important:** No domain provider, token, registrar or DNS record has been
queried or changed by this repository stage.
