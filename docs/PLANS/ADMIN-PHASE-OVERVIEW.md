# Next phase: HomeAway administration UI — gated implementation plan

> **Planning only (October 31, 2026).** No runtime/admin endpoints, user accounts, credentials, Windows settings, or Pi deployment are changed by these planning branches. These specs deliberately start after successful live validation of the existing complete-stack checkout. Rebase the feature work onto the subsequently validated and merged baseline before implementation; do not deploy from this branch.

## Baseline and branches

The operator's end-to-end validation uses:
- HomeAwayFromHome: `chore/docs-and-legacy-cleanup` ([integration test](../INTEGRATION-TEST.md)), baseline at planning start `c2e037a0765a0a1c1a28960b49103b531e84642d`.
- SensorDash: `chore/sensordash-telemetry-docs`, matching exporter and controls-only UI.

This planning work uses two **stacked HomeAway-only feature branches**:
1. `feature/homeaway-account-management` — account storage/identity boundary, RBAC, password/factor recovery, UI plan; includes this roadmap and [account spec](ACCOUNT-MANAGEMENT.md).
2. `feature/homeaway-computer-management` — built on the account planning branch; adds [computer management spec](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-computer-management/docs/PLANS/COMPUTER-MANAGEMENT.md), with a dependency on identity/role enforcement.

These branches do not need SensorDash changes. SensorDash remains the sole authority for cooling topology, sensor assignments, PWM control and local telemetry export. HomeAway manages **computer power commands** via restricted Pi/Windows SSH and WOL; it must not become a second fan controller.

## Baseline exit gate: before implementing the new UI

Do not start introducing new privileged management endpoints until the existing stack is proved on actual devices:
- Local DNS and WireGuard from iPhone cellular; no surprise WAN-forwarded ports.
- **Baseline only:** private HTTPS with a trusted Caddy root CA on existing `.home.arpa` names, Authelia password + TOTP, and no `/control` access without a verified session and correct group. Test blocked forged proxy headers. Public Let's Encrypt DNS-01 is a planned Phase 3 migration, not a baseline test requirement.
- Read-only Grafana iframe + separate Grafana admin login, real time-series ingestion from the reviewed SensorDash MSI.
- Two existing `pcctl` hosts and iOS Shortcut preserved; restricted status, known SSH fingerprints, and supervised WOL/Windows power operations; command audit entries.
- PWM profile persistence, radiator RPM and NVMe fan, tach-only DDC pump, firmware/default behavior when Windows service restarts or sleeps; Pi outage must never alter fan control.
- Off-device backups of Pi data, Authelia state/secrets and Windows `%ProgramData%\\SensorDash`; rollback tested.
- Green CI for current **heads**; record deployed git SHAs, Caddy/Authelia version and observed test results.

If any baseline gate fails, fix it on the existing validation branches. Do not bury a working-stack repair inside future administration features.

## Architecture and dependencies

```text
Browser (admin/operator/viewer; authenticated by Authelia)
    -> Caddy :8443 strips inbound identity and copies verified user + group claims
        -> control UI and narrow role-checked API (no root, no SSH key, no Docker socket)
              |                   |
              |                   +-> Account provisioning service (ONLY user directory)
              |                           -> Authelia users/groups backend
              +-> Computer inventory manager (ONLY active hosts.json and drafts)
                           -> existing pcctl agent (ONLY status, WOL, fixed SSH verbs)
                                     -> Windows forced-command SSH account

SensorDash on Windows -> outbound-only telemetry -> VictoriaMetrics / Grafana
Pi-hole, wg-easy and read-only :8088 portal remain independent.
```

**Privilege model:** browser must not gain filesystem write access, raw shell, `sudo`, Docker control or SSH key access. Strong authentication is not a substitute for independent server-side authorization. Distinct dedicated writers for identities and computer registry are preferred, with explicit file/socket ownership. Do not use the existing power-command agent as a generic system-administration agent.

## Planned feature order

| Gate | Work | Dependency | Measurable result |
| --- | --- | --- | --- |
| A0 | Baseline handoff and identity backend decision | Live validation | File-backend versus directory-service POC and rollback approved |
| A1 | Verified user/group claims + deny-by-default roles | A0 | Operator/viewer cannot call admin routes or forbidden power actions |
| A2 | Account store provisioning and recovery | A1 | Add/disable/change role safely with verified Authelia reload and no stale access |
| A3 | Account administration interface | A2 | Admin can manage household users without SSH; bootstrap/recovery remain local-safe |
| C1 | Inventory management broker and draft states | A1 | Admin can add/edit/deactivate hosts without root shell or restarting control API |
| C2 | Guided Windows SSH/WOL pairing | C1 | New PC reaches verified authenticated status with existing host keys preserved |
| C3 | Computer list, permissions, diagnostics | A3/C2 | Operator actions gated by role; clear requested-versus-confirmed status |
| V | Integration, Pi/Windows smoke, rollback | All | Full adversarial security matrix and real-world supervised test pass |

**Non-goals:** no automatic router changes or WAN exposure; no Fan Control/SensorDash PWM mutation; no full user directory/SSO provider rewrite unless a proof of concept demonstrates it is necessary; no bulk subnet scanning; no unattended Windows shutdown tests; no global Docker administration interface; no duplicate Grafana/SQLite history views.

## Design questions to resolve after live validation

1. **Identity backend:** preserve Authelia file backend (small household, simplest) only if concurrent file writes, hot reload and session invalidation have a safe verified design; otherwise choose a small LDAP directory such as LLDAP and document migration/backup costs. See [account plan](ACCOUNT-MANAGEMENT.md).
2. **Admin account bootstrap:** preserve one-time local bootstrap/recovery; do not expose an unauthenticated remote “create first admin” endpoint.
3. **Roles:** recommend `admins` (all management and power), `operators` (status/wake/sleep for permitted hosts), `viewers` (status only); restart/shutdown admin-only initially. Optional per-host grants are later functionality, not assumed available.
4. **Notifications:** filesystem notifier is development-only; SMTP or an approved explicit local recovery procedure is needed for routine household password/factor recovery.
5. **Passkeys:** Authelia v4.39 supports WebAuthn/passkey enrollment; the current implementation has password + TOTP only. Treat Face ID as independently testable UX enhancement, not required for the core admin launch. Do not enable experimental “passkey counts as 2FA” without explicit security review.
6. **Host identity and WOL:** verified Windows SSH host keys, existing public keys and iOS Shortcut compatibility override “zero-click” convenience. Initial pairing may still need one elevated Windows step.

## Source-of-truth map and review scope

- `scripts/init-control-auth.py` generates initial Authelia YAML/JSON and password; today it creates only an `admins` user, uses filesystem notifier, and **does not set** `authentication_backend.file.watch: true`.
- `control/gateway/Caddyfile` currently gates all `/control` to Authelia, propagates only `Remote-User`; new roles need verified `Remote-Groups` forwarding and server-side enforcement.
- `control/api/app.py` has status, history and power-action routes only; identity is currently a trusted gateway username header, not a role-checked admin API.
- `compose.control.yaml` isolates the controller, which mounts the host registry read-only and the group-protected command socket; preserve these boundaries.
- `control/pcctl/core.py` is the canonical strict host validator/performer; `scripts/add-pc.py` today is an atomic CLI-only append with no concurrent edit contract.
- `control/windows/Install-PcctlWindows.ps1` provisions the non-admin Windows SSH user via an elevated operator step; it intentionally refuses overwriting a pre-existing forced-command block.
- `portal/app.py`, `ingest/app.py`, Grafana and the WireGuard/Pi-hole stack do not require new write permissions for either feature.

## Four-phase product roadmap (future implementation; stacked planning branches)

Keep the baseline integration branch independent from this plan. Each phase is a **Git descendant of the preceding planning branch**, not a separate alternative implementation, and each uses the same Caddy/Authelia identity, permissions and configuration contracts.

| Phase | Planning branch | Owned screens / features | Prerequisites |
| --- | --- | --- | --- |
| **1 — Accounts and access** | `feature/homeaway-account-management` | Users, RBAC, safe provisioning, factor/recovery experience | Baseline full-stack live validation |
| **2 — Computers** | `feature/homeaway-computer-management` | Inventory editing, Windows enrollment, SSH trust and WOL diagnostics | Phase 1 identity and verified server-side roles |
| **3 — Setup and monitoring** | `feature/homeaway-setup-monitoring` | First-run/private network wizard, HTTPS trust, telemetry/Grafana guidance | Phase 1 admin model, Phase 2 inventory; preserves working core |
| **4 — Health and maintenance** | `feature/homeaway-operations-maintenance` | Service readiness, backup/restore, safe updates and recovery | Earlier three phases, tested restricted maintenance broker |

The phase 3 [setup & monitoring plan](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-setup-monitoring/docs/PLANS/SETUP-MONITORING.md) and phase 4 [operations & maintenance plan](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-operations-maintenance/docs/PLANS/OPERATIONS-MAINTENANCE.md) reside on their later stacked branches so the early branches remain independently readable.

**Cross-phase contracts to avoid duplicate work:**
- One account/role source: Authelia + a reviewed identity provisioning boundary; all admin APIs use the same validated claims and revoke semantics. Never build a new login subsystem for system settings.
- One system setup state: canonical `.env`, scoped application configs and service-owned data. The UI can hold redacted drafts and readiness results, but never keep a second writable source of truth.
- One computer inventory: `/etc/homeaway-pcctl/hosts.json`; only the constrained inventory writer (phase 2) mutates it. Phase 3's wizard reuses that onboarding workflow.
- One certificate/secrets custody design: Phase 3 prefers **Let's Encrypt DNS-01** on public-name/private-network browser gateway, using Cloudflare's scoped DNS token; Caddy's existing internal CA remains a legacy/fallback trust boundary, **distinct from SensorDash's dedicated telemetry TLS CA**. No generic root endpoint, Docker socket mounted to HTTP, or browser-readable private keys.
- One diagnostics surface: phase 3 provides setup-specific readiness states, phase 4 reuses those probes and adds time-series/backup/update health. Implement shared structured check IDs instead of duplicate shell scripts.
- One UI navigation and admin API convention: reuse the `/control` authenticated gateway, existing plain JavaScript and role gates; standalone `:8088` stays read-only.
- **SensorDash remains authoritative** for Windows telemetry configuration, fan curves, PWM, physical sensor assignments and the collector. HomeAway's monitoring wizard reports Pi readiness and links to SensorDash's own settings; it does not try to write Windows fan or exporter configuration remotely.
- Grafana owns charts and VictoriaMetrics history; prefer native Grafana provisioning, not duplicate dashboard builders or independent history storage.

During planning, write contracts and tests first; **do not introduce writable management controls before a dedicated privilege design is validated.** A first-run UI may guide manual steps (iPhone CA trust, router changes, Windows UAC) without pretending to perform them.

## Preferred domain and certificate decision (October 31, 2026)

- Target domain: **`homeawayfromhome.house`**. Intended browser names are `home.homeawayfromhome.house`, `auth.homeawayfromhome.house` and `metrics.homeawayfromhome.house`; optional future telemetry hostname `telemetry.homeawayfromhome.house`. These are proposals until ownership/authoritative DNS and current records are verified.
- **Registration:** keep the domain with Namecheap for now; registrar transfer to Cloudflare is optional, not required for DNS hosting or HTTPS issuance. Avoid assumptions about renewal pricing without checking the user's current renewal quote.
- **DNS hosting:** prefer free Cloudflare authoritative DNS. Copy/verify all existing records (website, MX, SPF/DKIM/DMARC, TXT, CAA and Namecheap forwarding, if any), review DNSSEC/DS at the registrar and change nameservers only with explicit operator approval and rollback. Public DNS publishes the ACME challenge TXT; Pi-hole/VPN provide private address resolution.
- **HTTPS:** Let's Encrypt **DNS-01** managed automatically by a reviewed pinned Caddy build with the Cloudflare DNS plugin; no public A/AAAA record and no inbound HTTP/HTTPS port forwarding are necessary. Scope DNS API credentials to this zone and protect them like secrets. Use ACME staging first and validate renewal, expiry and failure alerts.
- **Rollout:** Phase 3 updates `.env`, Caddy, Authelia cookie/redirect domains, portal/Grafana URLs, iframe/CSP and Pi-hole Local DNS as one controlled migration, with rollback to existing `.home.arpa` local CA access if needed. Do **not** change the existing integration branch or make phases 1–2 require domain migration.
- **Telemetry:** retain the separate private receiver CA and bearer-token authentication for SensorDash→Pi initially. Browser Let's Encrypt certificates do not replace SSH host/user keys, WireGuard peer keys or user MFA. Cert transparency logs reveal publicly issued certificate names; prefer one scoped wildcard covering one-label subdomains if privacy/operational tradeoffs justify it.
- See [Phase 3 HTTPS technical plan](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-setup-monitoring/docs/PLANS/PUBLIC-HTTPS.md) for precise steps, security checks and acceptance tests.

## Project delivery rules

Implement in focused commits on the appropriate phase branch **after** the baseline's live acceptance. Before merging, make PRs against the then-current validated base, run Python/JS tests and Docker/Caddy/Authelia checks, review exact gateway/agent privileges, then do a supervised Pi/Windows rollout. Keep this roadmap and the phase plans current with design decisions. Merely creating planning branches is not consent to install or enable any of these capabilities.
