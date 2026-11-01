# Phase 3 — Read-only TLS certificate observations

This milestone extends administrator-only `/control/#setup-readiness`
with four fixed, private TLS observations. It does **not** change live
Caddy configuration, issue certificates, access private keys, modify
DNS, or change Windows SensorDash trust.

## Observation architecture

The existing, unprivileged `metrics-diagnostics` container already
joins the **internal** `pc-control` and `infra` networks. It now
connects only to:

- `gateway:8443` with SNI from the configured
  `HOMEAWAY_DOMAIN`, `HOMEAWAY_AUTH_DOMAIN`, and
  `HOMEAWAY_METRICS_DOMAIN`;
- `telemetry-ingest:8443` for the separate Windows collector endpoint.

No LAN or WAN port is published, and the probe makes no request to a
user-supplied hostname, arbitrary URL or external certificate authority.
DNS names affect **SNI only**, never the fixed internal TCP destinations.

The observer extracts the **public leaf certificate** and its expiry,
issuer and SAN entries using a pinned X.509 library. For browser
hostnames, it checks SAN match and also performs a separate public
system-root verification attempt. Failure to verify against *public*
roots is **expected** for the existing Caddy private CA: this is not
automatically a HomeAway incident.

The separate telemetry receiver is probed without SNI or hostname
verification. The result shows its certificate's expiry but cannot
establish whether a Windows SensorDash installation trusts the separate
telemetry CA or whether the receiver certificate has the right private-IP
SAN for the Windows endpoint. These remain manually verified.

An expired certificate, certificate within 14 days of expiry, or a
browser SAN mismatch is **Attention**. Endpoint failure, missing/invalid
SNI, malformed leaf, or other unverified data is **Unknown**. The
observer container's public-root verification is reported as such,
not as client-browser trust or Caddy renewal success.

The certificate observations are cached for five minutes. The public
portal cannot retrieve them. Only existing authenticated administrators
may access `GET /control/api/admin/certificates`; the control API
validates a fixed versioned schema and excludes extra observer fields.
No raw DER, PEM, private key, token, or certificate file path is
returned to the browser.

## Operator deployment after reviewed merge

From the clean `Production` checkout on the Pi:

```bash
cd ~/HomeAwayFromHome
bash scripts/deploy-ui.sh --plan
bash scripts/deploy-ui.sh --apply
bash scripts/deploy-diagnostics.sh --plan
bash scripts/deploy-diagnostics.sh --apply
```

The first update changes only `portal` and `pc-control`. The
second changes only `metrics-diagnostics`; it doesn't request
a restart of Pi-hole, WireGuard, Grafana or VictoriaMetrics.
The official `python:3.13-alpine` observer image now builds with
`cryptography==50.0.2`, which has published musllinux ARM64 wheels.
No host Python packages are installed on the Pi.

Verify the four certificate cards with an authenticated admin session.
The current `.home.arpa` certificate should show **public trust not
established** rather than a false green public-trust claim. If the
observer stops, the cards become Unknown while other control actions
remain operational.

## Still outstanding

- Live verification of browser trust on iOS, Windows and VPN clients.
- Caddy issuance logs, automatic renewal success and ACME challenge history.
- Let's Encrypt DNS-01 staging and production certificates.
- Public authoritative DNS record/NS/DNSSEC migration and private split DNS.
- SensorDash collector CA renewal and Windows certificate trust validation.

These are separate reviewed cutover stages. The HTTPS migration does not
require new WAN port forwards or migrate the collector's CA.
