# Phase 3 — System setup, private HTTPS and monitoring onboarding

> **Design only. No runtime change and no deployment.** This phase is stacked on `feature/homeaway-computer-management`, after account/role and computer-onboarding plans. Implement only after real validation of the earlier stack and after phases 1–2 have working, tested authorization. See [shared contracts](ADMIN-CONTRACTS.md) and [four-phase roadmap](ADMIN-PHASE-OVERVIEW.md).

## Goal and operator experience

Create a coherent **HomeAway Setup Center** at a protected, admin-only URL such as `/control/setup/`. Replace routine SSH-driven questions with a step-by-step UI that explains prerequisites, validates the installed configuration, safely stages proposed changes, and identifies which steps require physical/operator participation. Keep a break-glass CLI and don't make household DNS or VPN depend on the wizard being available.

Suggested navigational stages:
1. **This Pi** — OS/architecture, fixed LAN IP, installed Docker/Compose, data-disk free space, existing running services and backup status.
2. **Private networking** — LAN/VPN routes, Pi-hole availability, local hostnames, optional HTTP vs trusted HTTPS access. Tell the operator exactly which client/router actions remain manual.
3. **HTTPS and trust** — publicly trusted Let's Encrypt DNS-01 on `homeawayfromhome.house`, managed by Caddy and Cloudflare DNS, with the current internal CA retained as rollback and telemetry's separate CA unchanged.
4. **Monitoring** — VictoriaMetrics/Grafana readiness, TLS ingest configuration, SensorDash exporter pairing instructions and freshness probes.
5. **Grafana in HomeAway** — an explicit read-only shared-dashboard setup with clear link-based-access disclosure and revocation.
6. **Review / activation** — preview changes, take backup where needed, run safe-only prechecks, invoke narrowly scoped privileged operations only after explicit confirmation; show rollback and audit record.

Progress is **derived from the canonical services/configuration**, not just checkboxes. Status labels must distinguish `not installed`, `not configured`, `configured`, `running`, `verified locally`, `verified from client`, `needs manual action`, `degraded`, `unknown`. A TCP listener or a successful HTTP GET isn't proof of a WireGuard handshake, correct remote DNS, working shared dashboard or successful PC-to-Pi data flow.

## Verified baseline and script inventory

| Existing source | Current action | Future UI responsibility / limitation |
| --- | --- | --- |
| `scripts/init.sh` | Creates .env example, app data directories and first Pi-hole/Grafana secrets without replacing existing ones | Show first-run readiness; keep secret creation behind local bootstrap, not browser GET |
| `scripts/validate_config.py` | Validates explicit .env key/value schema, Pi IP, CIDRs and routes | Reuse parsing/validation and report errors; no independent JS validator as sole authority |
| `scripts/doctor.sh`, `scripts/smoke.sh` | Read-only host checks, Compose config, DNS/portal probes | Convert to structured, bounded diagnostics with codes and severity; keep CLI wrappers |
| `scripts/init-telemetry.sh` | Generates 1825-day dedicated CA, 365-day IP-SAN server cert and 256-bit ingest bearer token; refuses overwrite | Report expiry/trust/ingest health; stage safe rotation plan, never expose server/CA private keys |
| `scripts/init-service-monitoring.sh` | Generates wg-easy metrics bearer and stores Pi-hole API credential | Readiness + secrets-presence state, user-confirmed monitoring setup; no raw credential display |
| `scripts/verify-telemetry.sh`, `verify-service-monitoring.sh`, `verify-pi-health.sh` | Read-only Grafana/VictoriaMetrics/scrape, Windows sample freshness and Pi metrics checks | Present bounded results with why, last verified time, and a repair guide |
| `compose.yaml`, `compose.control.yaml` | Core Pi-hole/wg-easy, optional metrics, portal, and optional Caddy/Authelia/PC controls | Show exact enabled profiles; no blanket restart or Docker API access from browser |
| `control/gateway/Caddyfile` | Current: stock Caddy, `tls internal`, `.home.arpa`, port 8443 | Plan pinned Caddy DNS-01 Cloudflare module, zone-scoped secret and automated Let's Encrypt renewal, retaining private listen address |
| `docs/PC-CONTROL.md` and `docs/INTEGRATION-TEST.md` | Manual private DNS, trusted CA, TOTP, Windows SSH and rollout instructions | Reuse the same requirements as machine-readable readiness steps |
| `docs/PORTAL-METRICS-EMBED.md` | Manual Grafana external share link and .env token configuration/revocation | Consent flow, status of public shared token, safe preview; preserve native Grafana ownership |

**Important current state:** the read-only `:8088` HomeAway portal is unauthenticated and remains so; all future mutating setup APIs are behind Authelia on private `:8443`. Even admins cannot use HomeAway to bypass Grafana admin login or SensorDash local hardware authorization.

## Source-of-truth and configuration ownership

- **Network facts and core Compose:** existing `.env` plus `compose*.yaml`; the UI may hold a validated draft and minimal non-secret setup progress, but must not fork a second active configuration store. Re-use `validate_config.py` and define a versioned allowlist of editable fields. Avoid arbitrary environment-variable/name/value injection, multiline secrets, or shell interpolation. Preserve comments and unknown keys if using a text serializer; on ambiguity, reject rather than normalize destructively.
- **DNS records:** Pi-hole v6 is authoritative for Local DNS. Prefer its supported administrator API when safely scoped and authenticated; *do not* assume API calls to Pi-hole are read-only. A setup wizard can initially give verified copyable instructions and check DNS; no automatic rewrite of household DHCP, router WAN NAT or client configuration.
- **VPN:** wg-easy manages its own peers and tunnel config; HomeAway may show summarized status and guide the user to its existing SSH-tunneled admin interface, but no duplicate peer editor or silent profile/key rotation.
- **Authentication:** phases 1–2 own Authelia users/groups and computer inventory; the Setup Center consumes those admin services rather than adding separate user/PC stores.
- **Monitoring:** Grafana provisioning JSON and scrape YAML remain Git-reviewed; VictoriaMetrics holds time-series data. Setup Center is a configuration/health interface, **not** an independent panel builder or time-series viewer.
- **Windows telemetry:** SensorDash remains the only editor of the Windows export URL, bearer credential and CA trust. HomeAway's wizard can show Pi endpoint and copyable **public** CA/certificate fingerprint and offer an operator-directed secure transfer of the bearer secret, but must not call Windows control endpoints or edit Windows config over SSH. Never put a long-lived bearer token into a public QR, iframe, URL, log, telemetry label or ordinary GET response.
- **Keys and trust:** new Caddy Let's Encrypt browser leaf/private key and Cloudflare DNS API token are protected Caddy-only state; existing Caddy internal CA remains a fallback; the telemetry CA at `secrets/telemetry-ca.key` remains separate. No web process may read/return private keys, API tokens, or CA signing material. Public certificates eliminate routine local root-CA installation on iPhone for the new browser names, not for the old fallback.
- **Account recovery/notifications:** reuse phase 1's Authelia workflow/SMTP configuration; don't add a second account reset flow.

## Architecture and privileged implementation strategy

```text
Browser setup UI (admins only, trusted HTTPS)
    -> Caddy strips spoofed identity headers / Authelia verified 2FA & groups
       -> protected Setup API (unprivileged, read-only observations / redacted drafts)
           -> read-only health checks (bounded API/port/TLS status)
           -> scoped admin configuration broker (separate narrow Unix socket)
               -> allowlisted one-resource transactions, version/ETag preconditions
               -> restricted service actions with preview / rollback when specifically approved
   NOT -> shell, sudo, docker.sock, root filesystem mounts or arbitrary Compose service name
```

Phase 3 should **not** implement a generic "run command" or "write file" admin API to ease UI work. A dedicated host broker may safely validate/provision specific files, but its operations must be enumerated (`validate_network_draft`, `write_known_env_keys`, `validate_cert_chain`, `read_service_state`, etc.), with strict allowlisted paths, file ownership and no attacker-chosen paths/commands. The minimum viable UI can be **read-only / assisted** while retaining human-confirmed local installation commands; privileged apply is a separately reviewed substage.

Write operations need admin RBAC at gateway, API **and broker**, CSRF/origin controls, resource revision, audit actor+requested diff+outcome, bounded inputs, explicit backup before replacing a live .env, atomic write+fsync and restart impact report. If a change needs Pi-hole/wg-easy/Docker restarts, mark that as a planned maintenance operation with confirmed recovery access and a separate Phase 4 safe orchestration contract. A success response must not claim the new service state is proven until checked.

**Do not install the wizard in an unauthenticated "first boot" state.** A local one-time bootstrap remains necessary to establish admin credentials, TLS and the gateway, then the UI guides the rest. Any local bootstrap-token option requires short TTL, binding to local console access and fail-closed validation; it is not a blanket public setup endpoint.

## Feature work packages

### S0 — study actual deployment before changes

After current E2E and phase 1/2 acceptance, record Pi OS + Compose versions, trusted domain/IPs, encrypted backup and existing manual scripts, test iPhone VPN access and private DNS resolution, and inventory existing user data. Take a private inventory of certificates (issuer, SAN, validity, public fingerprint only) **without** copying keys/logins into source control.

### S1 — unify read-only readiness and diagnostics contracts

Refactor *logic* behind the existing `doctor` / `smoke` / `verify-*` tools into importable, structured probes with stable IDs, `severity`, `state`, `evidence kind`, `last checked`, `manual next step`, `repair reference`. Keep CLI wrappers as thin clients. Avoid having the Setup Center spawn subprocess shells for each page refresh. Apply timeouts, caching, concurrency bounds and redact sensitive output.

Proposed checks: `pi.ip.present`, `docker.compose.available`, `dns.resolver.local`, `dns.private_hostname.homeaway`, `vpn.handshake.operator_verified`, `https.caddy.leaf_valid`, `https.browser.publicly_trusted`, `https.acme.dns01.propagation`, `https.acme.renewal_healthy`, `https.cloudflare.zone_active`, `https.cloudflare.dnssec_valid`, `https.caddy.dns_module_present`, `https.legacy_root_ca_fallback`, `auth.totp_enrollment.verified`, `metrics.vm.ready`, `metrics.grafana.dashboard_ready`, `metrics.ingest.tls.valid`, `metrics.export.last_sample_age`, `metrics.embed.share_enabled`. Absence of host sensor data is not "the Windows PC crashed".

### S2 — wizard state and narrowly scoped configuration

Add protected `GET /control/api/setup/status` and `GET /control/api/setup/checks`, returning redacted facts, versioned check results, and role-specific action hints. For changes, `POST /control/api/admin/setup/drafts`, `POST .../validate`, `POST .../apply` must be separately reviewed and opt-in; reject arbitrary keys. Store safe drafts with ownership and expiry, avoid secret values in browser storage, and never copy existing active secrets into draft JSON. On "apply", require an explicit scope/impact preview and stable optimistic revision, and refuse if existing backup/rollback preconditions aren't met. Avoid another installer just to host a GUI.

### S3 — private network + publicly trusted browser HTTPS

Read the [public HTTPS migration specification](PUBLIC-HTTPS.md). Preferred service names: `home.homeawayfromhome.house`, `auth.homeawayfromhome.house`, and `metrics.homeawayfromhome.house`, with the domain remaining registered at Namecheap and Cloudflare as the preferred authoritative DNS provider. Migration of nameservers and domain-wide records (website/email/TXT/DNSSEC) is manual and requires operator approval; no registrar transfer is necessary.

Use Caddy's reviewed/pinned Cloudflare DNS module for **Let's Encrypt DNS-01**, first in ACME staging. A zone-scoped API credential permits temporary public TXT challenges, **without exposing any LAN web ports**. Pi-hole Local DNS and WireGuard DNS supply the Pi's private IP for service names. Public A/AAAA web records and Cloudflare reverse proxy are not required.

The migration is one coordinated change across Caddy routing, `.env` hostnames, Authelia session cookie and redirect domain, control origin/CSRF checks, portal links, Grafana canonical origin, iframe CSP and read-only shared-dashboard links. Keep the legacy `.home.arpa`/Caddy internal CA path as an explicit rollback option; **do not disrupt the baseline integration test**.

Show setup checks separately for registrar/zone ownership, current nameservers/DNSSEC, cloud token permissions (without disclosure), DNS-01 TXT validation, installed Caddy module, certificate issuer and SAN, expiry/renewal, LAN/VPN private DNS, and real iPhone browser trust. Wildcard `*.homeawayfromhome.house` covers first-level service names and reduces individually exposed names in public certificate-transparency logs; evaluate its shared-key tradeoff. The telemetry receiver keeps its distinct private CA and bearer token.

### S4 — telemetry and Grafana onboarding

Check the separately issued telemetry CA/server certificate and receiver `https://PI_LAN_IP:8429/ingest`, report certificate remaining life, explicit server-IP SAN, secret *presence* and token fingerprint/last verified status but never secret. Display a private **"Configure SensorDash"** guide with Windows local setting names, CA public cert, receiver URL and a trusted secure bearer handoff that doesn't persist credentials in UI state or logs. A failed handshake/cert means **not verified**, even when Grafana is running.

Show Grafana's provisioned dashboards and ingest freshness per configured Windows host label and redirect to the native Grafana UI for edits. The externally shared dashboard must remain **opt-in**: explain access by possession of the link to anyone with network reachability; support safely observing status and linking to Grafana's share/revoke control. Never silently enable anonymous Grafana access, assume the shared token is a normal login token, or auto-post a credential in a page visible on :8088.

Avoid duplicating metrics readout components already implemented in Grafana; embed one verified read-only view and link to detailed charts.

### S5 — UI, accessibility and migration

Create a compact, step-based mobile-friendly Setup Center consistent with current plain HTML/CSS/JS, with resume after refresh and clear `next action` details. Each card has observed/current, desired/proposed, blocked/unknown, impact, and link to full runbook. Link to the phase 1 Account/Permissions screens and phase 2 Computer wizard instead of copying their forms. Use one canonical settings navigation and common status/error component.

Migration from manual `.env` setup is **adopt, don't reset**: never regenerate Authelia database, Pi-hole password, WireGuard keys, legacy Caddy root CA, telemetry CA, or Grafana public share on a working Pi. The Let's Encrypt/Cloudflare browser cutover must be staged and fully reversible. Existing values must be displayed as "configured", with secrets masked. If config is edited outside UI, read and refresh the canonical state; stale draft apply must conflict, not overwrite.

### S6 — testing and E2E acceptance

CI hardware-free tests for no unauthenticated writes; denied operator/viewer access; unsafe names/values, symlink/path traversal and shell injection; true conflict detection; browser LE, legacy Caddy and telemetry CA never conflated; verified Caddy DNS provider and token secrecy, split-horizon DNS, staging TXT issuance/renewal and no added WAN ports; secret redaction; external config changes; exact verification labels. Use real pinned Caddy/Authelia config validation, Compose resolved security-contract checks, and mock Pi-hole/VM/Grafana responses (timeouts and bad JSON). Inspect current Python/Node test suites and add mobile/a11y smoke tests. Run real iOS browser certificate trust and WireGuard DNS verification only in operator test session, not CI.

**Acceptance:** A household admin can see why HomeAway isn't fully configured; get correct instructions for private DNS, local HTTPS and SensorDash telemetry; distinguish working Grafana from actually delivered Windows measurements; finish opt-in Grafana embedding with correct disclosure and revocation; and return to setup later without accidentally resetting existing infrastructure. Changes to permissions, PC inventory and Windows fan controls still use only their original owners.

## Likely files/modules

- UI: `control/api/index.html`, `app.js`, `styles.css`, new small `control/api/setup-*.js` and `control/api/setup_*.py`.
- Validators: `scripts/validate_config.py`, structured wrappers of `scripts/doctor.sh`, `smoke.sh`, `verify-telemetry.sh`, `verify-service-monitoring.sh`, `verify-pi-health.sh`.
- Optional broker: new narrowly scoped host-side configuration agent/socket; **do not** add shell execution to `control/pcctl/agent.py` or expand its WOL/SSH commands.
- Config: `compose.control.yaml` only for additional isolated, no-public-port setup service if justified; no new exposed port.
- Docs/tests: `docs/INSTALL.md`, `docs/PC-CONTROL.md`, `docs/TELEMETRY.md`, `docs/PORTAL-METRICS-EMBED.md`, `docs/SECURITY.md`, new setup tests and explicit ADR.

## Scope limits / unresolved choices

- Choose at implementation time whether phase 3 stops at guided read-only checks or permits narrow writes to .env and Pi-hole Local DNS, pending privilege threat model and rollback demonstration.
- **Automatically renew Let's Encrypt browser certificates via Caddy**, monitored in Phase 4; retain manual/assisted recovery for legacy Caddy root CA and SensorDash's **separate telemetry CA**, neither of which should rotate silently.
- No unattended router changes, automatic port forwarding, cloud/remote account provider, dynamic arbitrary Compose editor, general-purpose SSH-to-Windows provisioning, or scripts executing as root from the browser.
- Do not claim E2E mobile client trust verified from a Pi-side health probe alone.
