# Next phase: HomeAway administration UI — gated implementation plan

> **Implementation now begins after validated `Production` consolidation (November 1, 2026).** Phase 1 is a development branch only. It can add and test authorization code, but it does not deploy account changes to the Pi, enable account writes, rotate secrets, or alter Windows fan control.

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
- Private HTTPS with a trusted Caddy root CA, Authelia password + TOTP, no access to /control without a verified session and correct group; test blocked forged proxy headers.
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

## Project delivery rules

Implement in focused commits on these branches **after** the baseline's live acceptance. Before merging, make PRs against the then-current validated base, run Python/JS tests and Docker/Caddy/Authelia checks, review exact gateway/agent privileges, then do a supervised Pi/Windows rollout. Keep this roadmap and the two detailed plans current with design decisions. Merely creating planning branches is not consent to install or enable any of these capabilities.
