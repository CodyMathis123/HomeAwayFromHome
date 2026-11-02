# HomeAwayFromHome — current roadmap and branch inventory

**Updated November 2, 2026.** This is the current status index for the live project, rather than a claim that every feature has undergone full physical-device acceptance. The [root README](../README.md) is the entry point for new installs; the [operations guide](OPERATIONS.md) covers the deployed system.

> **Implementation status correction (November 1, 2026):** The historical
> Phase 3 and Phase 4 statuses below were written before the HTTPS and
> encrypted-backup milestones. Production now includes Cloudflare DNS-01
> private HTTPS on `homeawayfromhome.house`, read-only diagnostics and
> certificate observations, and a full-state encrypted backup with a
> sanitized admin-only checksum status view. The off-device decrypt/listing
> check succeeded; **isolated restoration remains untested**. See
> [current phase status and component ownership](PHASE-STATUS-AND-OWNERSHIP.md)
> for the reconciled view and remaining gates. This historical inventory is
> retained for branch traceability, not as the current feature checklist.
>

## Active deployment cleanup (November 2)

The active Pi uses the canonical `compose.production.yaml` plus opt-in
`compose.account-recovery.yaml` and `compose.authelia-smtp.yaml`.
Both LLDAP invitations and Authelia factor notifications were received via
Resend. LLDAP web paths and DNS-01 access are scoped to private LAN/VPN.
The active Caddy mount uses `Caddyfile.account-recovery`, not
`Caddyfile.production`; readiness/perms audits must inspect the live mount.
Docker accounts and broker sources must be readable by non-root container
users (`COPY --chmod`), but SMTP/ACME credentials must remain private.
A normal login and an Authelia security-notification email were confirmed.
An isolated full restore is still outstanding.

## Current baseline and gates

| Stage | Code/merge status | Operator acceptance | Outstanding |
| --- | --- | --- | --- |
| Core Raspberry Pi DNS, WireGuard, portal and metrics | In `Production` | Existing household operation and dashboard ingestion reported working | Continue normal maintenance and off-device restore verification |
| Phase 1 — Accounts/access | In `Production` | Authelia TOTP, LLDAP roles, admin/operator/viewer and existing PC controls previously exercised | SMTP invitation, LLDAP password setup/reset, account deletion, and Authelia factor notification via Resend verified on live Pi; follow-up includes complete second-PC acceptance and hardening |
| Phase 2 — Computer Management | In `Production`, [integration PR #14](https://github.com/CodyMathis123/HomeAwayFromHome/pull/14) merged; original [PR #10](https://github.com/CodyMathis123/HomeAwayFromHome/pull/10) closed as superseded | Read-only Pi host socket smoke; restricted admin UI; inactive draft create, denied SSH trust verification, discard and audit; existing power controls and Shortcut reportedly preserved | Actual **second** Windows PC onboarding, authenticated host-key pinning, activation, disable/archive/restore, and real offline-recovery test remain deferred |
| Phase 3 — Setup and Monitoring | **Design only**, [planning branch](https://github.com/CodyMathis123/HomeAwayFromHome/tree/feature/homeaway-setup-monitoring) | None on Pi | Read-only unified readiness; controlled certificate and private DNS plan; reviewed privileged broker only if genuinely needed |
| Phase 4 — Operations and Maintenance | **Design only**, [planning branch](https://github.com/CodyMathis123/HomeAwayFromHome/tree/feature/homeaway-operations-maintenance) | None on Pi | Backup coverage/restore, reliable update orchestration, useful health diagnostics, scoped admin actions |

The Phase 3/4 branches are **stacked historical planning descendants** of earlier Phase 1/2 planning. Do not merge them wholesale or deploy from them: their descriptions of what was “not implemented” predate the current `Production` baseline. Review and port **specific future requirements** against the present code. Sources: [phase overview](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-operations-maintenance/docs/PLANS/ADMIN-PHASE-OVERVIEW.md), [shared admin contracts](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-operations-maintenance/docs/PLANS/ADMIN-CONTRACTS.md), [Setup/Monitoring](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-setup-monitoring/docs/PLANS/SETUP-MONITORING.md), [Public HTTPS](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-setup-monitoring/docs/PLANS/PUBLIC-HTTPS.md), and [Operations/Maintenance](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-operations-maintenance/docs/PLANS/OPERATIONS-MAINTENANCE.md).

## Before Phase 3: focused refinement, not a competing implementation

1. **Reconcile status and runbooks** with current Production, including obsolete branch-based checkout instructions and the Phase 2 acceptance caveats. Keep old integration documents clearly labeled as historical rather than silently rewriting test history.
2. **Polish the existing authenticated `/control/` UI**: simple navigation among Power Controls, Computers and Accounts; mobile layout, keyboard/focus semantics, explicit success/failure messages; indicate `Reachable` means SSH/TCP reachability, not confirmed OS power state. Preserve current authorization and endpoint separation. Build and test on a small dedicated branch.
3. **Reuse, do not reimplement, existing capabilities**: SensorDash owns Windows fan profiles, RPM/PWM, cooling topology, aliases, collection and export. Pi VictoriaMetrics/Grafana own history and dashboards. `pcctl` owns allowlisted PC power operations; the existing non-root inventory writer owns active hosts and draft/retired state; LLDAP/Authelia own account identities.
4. **Maintain deferred physical gates**: do not disable the only active Windows PC to test Phase 2 or assume a successful wake/SSH request confirms a complete power transition.

## Important known recovery gap

The existing `scripts/backup.sh --offline` creates a consistent archive of **only** the repository's `.env`, `secrets/` and `data/` while stopping Compose-project containers. It **does not** capture host-side `/etc/homeaway-pcctl/hosts.json`, Pi `pcctl` SSH keys/trust, `/var/lib/homeaway-pcinventory/` drafts/retired/audit, installed systemd units/sockets or their permission model. These need a separately approved, encrypted off-device recovery plan; a successful current Compose backup is **not** a complete Phase 2/4 disaster recovery test. See [Operations](OPERATIONS.md) and the Phase 4 planning branch before designing the scoped backup extension.

## Existing open items and how they fit

- [PR #8](https://github.com/CodyMathis123/HomeAwayFromHome/pull/8) is a still-open Pi metrics diagnostics improvement against older code. Its diagnostic script/docs changes are **not** in Production by ancestry. Review its incremental value before closing or selectively reworking it; do not blindly merge an obsolete branch.
- [Issue #9](https://github.com/CodyMathis123/HomeAwayFromHome/issues/9): account invitation/password/factor-notification mail has now been implemented and **verified with received messages** on the live Pi. Review remaining issue wording and close only after checking its specific acceptance criteria; avoid conflating this with full account recovery or second-PC validation.
- Earlier `feature/*`, `fix/*`, `chore/docs-and-legacy-cleanup` and the Phase 2 `integration/*` branches are mostly historical ancestors of Production. Presence of a branch is not evidence that it should be merged.
- Unmerged Phase 3 and 4 **planning** documents are intentionally retained as design input; decide their implementation order by risk and dependencies rather than branch age.

## Next implementation sequence

**Immediate:** reconcile current overlays, permissions/network/security preflights and eliminate obsolete docs. **Then:** focused second-PC enrollment testing; actionable readiness diagnostics and service alerts; lifecycle/runbook refinements; encrypted host-state recovery coverage and isolated restore testing. **Already delivered:** private DNS-01 HTTPS, readiness observations and encrypted full-state backup. Keep the SensorDash/HomeAway ownership split.

The browser certificate migration discussed in Phase 3 is **not** a reason to expose HomeAway admin ports to the internet. Keep all browser services LAN/VPN-only, even with publicly trusted certificates, and keep the Windows telemetry trust/CA separate.
