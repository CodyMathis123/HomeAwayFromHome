# Public HTTPS migration — Namecheap domain + Cloudflare DNS + Let's Encrypt

**Phase 3 design, not deployed.** Existing `*.home.arpa` Caddy internal CA and port 8443 remain the current integration-test configuration. Do not change live DNS, router, certificates or accounts before baseline validation and operator authorization.

## Chosen target

**Domain:** `homeawayfromhome.house` (verify ownership/records before any live change). Keep Namecheap **registrar** initially; transfer registration to Cloudflare is optional and independent of DNS/certificate functionality. Prefer Cloudflare Free for **authoritative DNS** and a reviewed Caddy Cloudflare plugin for Let's Encrypt DNS-01. Let's Encrypt publicly trusted browser certs and automatic Caddy renewal are the new default; keep the private Caddy CA as local recovery/fallback.

| Private browser service | Preferred FQDN | Existing baseline |
| --- | --- | --- |
| HomeAway, PC controls and future Setup Center | `home.homeawayfromhome.house:8443` | `homeaway.home.arpa:8443` |
| Authelia login and recovery | `auth.homeawayfromhome.house:8443` | `auth.homeaway.home.arpa:8443` |
| Grafana and read-only embedded view | `metrics.homeawayfromhome.house:8443` | `metrics.homeaway.home.arpa:8443` |
| SensorDash telemetry (not migrated yet) | optional `telemetry.homeawayfromhome.house` | `https://PI_LAN_IP:8429/ingest` with dedicated CA and bearer |

**Private traffic stays private:** Pi-hole Local DNS maps the new browser names to the Pi's reserved LAN IP; WireGuard clients use Pi-hole DNS via the VPN. The Caddy gateway stays bound to the LAN address on :8443. Public authoritative DNS only has to answer DNS-01 TXT challenges. **No public A/AAAA records for the private web services, no Cloudflare orange-cloud proxy, Cloudflare Tunnel, or added router forwards.** Only the already-approved WireGuard UDP 51820 remains WAN-forwarded.

## Certificates, keys and privacy

1. **Let's Encrypt browser TLS:** a Caddy-managed server certificate and **private key**, trusted by iOS/Windows browsers without manual private-CA installation. Use a DNS-01 wildcard `*.homeawayfromhome.house` only if it simplifies key/cert reuse for the three one-level names; the wildcard does not cover the apex or deeper subdomains. All publicly issued certificates appear in CT logs; wildcard reduces disclosure of individual service labels but reveals the wildcard and domain.
2. **Legacy Caddy internal CA:** existing separate signing key and certificates for the current `.home.arpa` setup. Preserve for rollback; using it still requires manual trusted-root installation on client devices.
3. **Telemetry TLS:** dedicated private CA/server certificate (currently private-IP SAN and 365-day server cert) and distinct bearer token. Windows SensorDash retains this trust and export configuration; its credentials are NOT reused for Caddy or ACME.
4. **Unrelated secrets:** Pi/Windows SSH client/host keys, WireGuard peer keys, Authelia password hashes/session keys/TOTP, and Grafana shared-link tokens are not replaced by HTTPS/Let's Encrypt certificates.

**Cloudflare API credential:** the Caddy provider currently needs `Zone:Zone:Read` and `Zone:DNS:Edit` restricted to **only the `homeawayfromhome.house` DNS zone**; use a scoped token, not a global API key. The token can still change other DNS records within that zone if stolen. Store it Caddy-only via reviewed runtime secret handling, not in `.env` committed to Git, browser/API output, command logs or Grafana. Consider delegated `_acme-challenge` control as a future narrower option. Do not share server or CA private keys.

## Work packages and operator gates

### H0 — public DNS inventory before migration

Audit the current nameservers, Namecheap domain status/expiry, existing DNSSEC DS records, web redirects, @/www, MX and mail forwarding, SPF/DKIM/DMARC, service TXT, SRV/CAA and any third-party records. Export all live zone records and identify Namecheap-specific features that won't carry over automatically. Do not assume imported Cloudflare quick-scan records are complete. **Namecheap renewal pricing and registrar transfer remain separate decisions.**

### H1 — authoritative DNS migration, only with explicit approval

Prepare a Cloudflare zone and compare *every* record to Namecheap. If DNSSEC is active, disable/remove stale registrar DS records **before** changing the nameservers to avoid resolution outage, then re-enable DNSSEC with new Cloudflare data after the zone is active. Switch Namecheap nameservers only after full record parity and a rollback plan. Verify website/email behaviors and public authoritative NS/CAA/TXT before/after; no auto-edit of Namecheap, router, DHCP or WAN. If a full NS migration is undesirable, evaluate a narrow DNS-01 delegation/alternative provider as a separately reviewed design.

### H2 — Caddy DNS provider and ACME issuance

Stock pinned `caddy:2.11.7-alpine` in current Compose **does not bundle** `github.com/caddy-dns/cloudflare`. Design a reproducible, **version-pinned/aarch64-compatible** Caddy build with that module and check `caddy list-modules`; don't use an unreviewed rolling third-party image. Keep existing gateway routes, Authelia forward-auth identity-header stripping and private port binding. Provide the token only to Caddy with restrictive permissions. Use Let's Encrypt **staging** first to validate TXT create/propagate/cleanup and prevent production issuance rate-limit surprises; then obtain production certificate with normal TLS verification. Use public resolvers for DNS-01 propagation when Pi-hole/split DNS masks TXT changes. Persist Caddy ACME/cert state. Automatic Caddy renewal replaces any separate cron/certbot job.

### H3 — private split-horizon DNS and coordinated cutover

Add private Pi-hole A records for `home.`, `auth.`, `metrics.` on the domain to the reserved Pi IP. Check IPv6/DoH bypass and WireGuard DNS on cellular. Stage the changes to `HOMEAWAY_DOMAIN`, `HOMEAWAY_AUTH_DOMAIN`, `HOMEAWAY_METRICS_DOMAIN`, control origin/CSRF checks, Authelia cookie domain and login/reset/redirect URLs, Caddy SNI routes, portal control link, Grafana `GF_SERVER_ROOT_URL`, shared-embed URL and CSP/iframe origins. **Do not rerun the one-time Authelia initialization over existing secrets/users.** Reauth may be necessary with the new cookie domain. Never silently enable Grafana anonymous access or change existing shared-dashboard link capability/revocation semantics.

Only activate after: backup current Caddy/Authelia/Grafana settings; explicit operator review; successful new TLS, Authelia/TOTP, Grafana embed and computer controls over LAN/VPN. Preserve `.home.arpa` as a verified recovery route until cutover proven; prepare specific rollback of DNS, hostnames, auth session behavior, Grafana URL and proxy config. The existing iOS SSH Shortcut must still work.

### H4 — ongoing renewal and diagnostics

Health checks must report certificate issuer, SAN/hostname, remaining life, trust chain, renewal attempts and errors; whether Cloudflare zone/NS/DNSSEC and token permissions are suitable; Caddy plugin presence; public TXT propagation; and correct private LAN/VPN DNS. Monitor renewed-cert behavior across Pi restart and internet outage. Issuance/renewal needs outbound ACME + DNS API access, not incoming web traffic; an already-issued cert stays valid until expiry even if Cloudflare is temporarily unreachable. **Alert well before expiry.**

Test failure cases: missing/scoped-wrong/revoked API token, provider unavailable, bad DNSSEC/CAA, TXT negative caching, expired cert, local DNS mismatch, old Authelia cookie domain, wrong Grafana redirect/CSP, public HTTPS port inadvertently exposed, or a new public A/AAAA. Note that issuance makes the domain/wildcard visible via CT. Keep private telemetry CA renewal as a *separate* operation.

## Acceptance and security contract

- Baseline validation completed before making any live changes.
- Domain records and DNSSEC correctly migrated; Namecheap registration unchanged unless separately requested.
- Trusted Let's Encrypt certificate from pinned Caddy Cloudflare DNS-01 module, renewal observable, no HTTP/HTTPS WAN forwards, and all three browser names resolve **privately** on desktop and iPhone via VPN.
- Authelia login, groups/role checks, TOTP, account recovery, control CSRF, Grafana root/embedding and PC power behavior proven on the new names.
- SensorDash telemetry and PWM remain unchanged; no credential, CA signing key, private leaf key or DNS token exposed to portal or source.
- Working tested rollback to the current internal-CA browser endpoint, with account recovery independent of Cloudflare availability.

## Authoritative references

- [Let's Encrypt DNS-01](https://letsencrypt.org/docs/challenge-types/)
- [Caddy TLS and DNS-01 modules](https://caddyserver.com/docs/caddyfile/directives/tls)
- [Cloudflare Caddy DNS module and token permissions](https://github.com/caddy-dns/cloudflare)
- [Cloudflare full DNS zone migration and DNSSEC cautions](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/)
