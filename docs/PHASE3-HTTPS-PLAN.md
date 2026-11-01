# Phase 3 remaining sequence — private HTTPS and guided setup

**Implementation order accepted November 1, 2026.** This is a design and
operator-gate document only. No certificate, DNS, hostname, registrar, router,
Authelia session, credentials or deployed Compose service changes are made
by this planning note.

## What is built first

The authenticated Setup & Diagnostics screen (PR #25) and the bounded
VictoriaMetrics diagnostic adapter are the read-only foundation. Their
fixed checks must reliably distinguish observed values from unknown,
unverified and stale data. In particular, a sleeping Windows PC must not
look like a failed fan controller.

The initial certificate work should also be **observation-only**:
- Browser gateway leaf expiry, issuer and hostname match, without exposing
  Caddy private keys, internal CA keys or session secrets.
- Separate telemetry receiver certificate expiry, with its existing
  private-IP SAN and unrelated CA; no change to SensorDash export trust.
- Clear difference between private Caddy CA, publicly trusted web
  certificates and the collector's private TLS chain.

## Then migrate private browser HTTPS via DNS-01

The existing Phase 3 design names the target domain
`homeawayfromhome.house`, to be confirmed under the user's control.
The intended private browser names are:
`home.homeawayfromhome.house`,
`auth.homeawayfromhome.house` and
`metrics.homeawayfromhome.house` on private port `8443`.

The registrar may remain Namecheap; Cloudflare is the **proposed**
authoritative DNS provider, not a confirmed current configuration.
Before any domain edits:
1. Read live NS, DNSSEC/DS and all DNS records (including email,
   forwarding and third-party records); verify registrar ownership.
   A Cloudflare migration must not discard records or break mail.
2. Review a pinned, arm64-compatible Caddy image with a specifically
   packaged Cloudflare DNS-01 module. Stock Caddy has no Cloudflare
   provider bundled. Keep the existing gateway auth/header stripping.
3. Provision a **zone-scoped** DNS token to Caddy only, with no copy in
   the public portal, git history, metrics labels or browser responses.
4. Validate Let's Encrypt **staging** DNS-01 TXT issuance and cleanup.
   Use production only after staging passes. No public A/AAAA records,
   Cloudflare Tunnel/proxy, or new WAN HTTPS forwards are required.
5. Create local Pi-hole records for the three names pointing to the Pi's
   private LAN address. Test both home Wi-Fi and remote WireGuard DNS.
6. Coordinate Caddy, Authelia cookie/redirect origin, control API CSRF
   origin, portal links, Grafana root URL/embeds and browser trust.
   Never regenerate or overwrite the working identity database or keys.
7. Keep the current `.home.arpa` route and Caddy internal CA usable as
   a rollback until the new names work on desktop and iPhone. Validate
   TOTP, role revocation, PC controls, Grafana embeds and the iOS shortcut.

Let's Encrypt auto-renewal should be owned by Caddy; there will be no
second certificate cron job. An automatic renewal needs outbound DNS/ACME
access, not an internet-visible HomeAway web server.

The Windows SensorDash receiver continues using its existing separate
telemetry CA and `https://PI_LAN_IP:8429/ingest` in this phase.

## Then guided Setup Center

Iterate on the current admin-only Setup & Diagnostics view rather than
creating another site. Add structured, timestamped readiness checks and
contextual steps for private DNS, Caddy certificate, telemetry pairing and
Grafana links. Each stage should show **Observed / Not configured /
Unknown / Needs operator action / Client verified**, a bounded source,
and exact manual next steps.

Config owners remain canonical: `.env`, Caddy/Authelia, Pi-hole,
wg-easy, Grafana, LLDAP and SensorDash. No generic shell command, root
web process, Docker socket or unreviewed wide write API. If later adding
writes, use a fixed allowlisted operation with preview, confirmation,
conflict detection, audit and rollback.

## Backups and scope

Because HomeAway is actively evolving on a greenfield Pi, broad backup/
restore validation and a production-grade maintenance interface remain
scheduled for **Phase 4**. Do not make unrelated backup-engine changes
while implementing diagnostics or read-only setup screens.

**Exception: a reversible HTTPS/authentication cutover still requires a
narrow pre-change copy of the specific configuration and credentials it
might affect, and a tested way to restore the old working browser URL.**
This is a safety prerequisite to changing live auth/DNS, not an attempt
to declare full-system disaster recovery solved.

Do not perform any live DNS/registrar/TLS migration from GitHub CI.
The operator must explicitly approve each provider/host change and
execute the documented Pi-side cutover.
