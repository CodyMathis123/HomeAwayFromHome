# Production housekeeping and follow-up — November 2, 2026

This is a **repository and operator-evidence** inventory, not a claim that
ChatGPT directly accessed or modified the running Raspberry Pi.

## Confirmed by operator on the live Pi

- Private HTTPS under `homeawayfromhome.house:8443` with Caddy DNS-01 works.
- LLDAP invitation/setup email received via Resend and recipient signed in.
- Account deletion is in the HomeAway admin UI; protected bootstrap/current
  administrator accounts cannot be deleted there.
- Authelia configuration no longer has the filesystem notifier; SMTP address
  and password-file environment settings were confirmed in Docker inspect.
- The Authelia container was healthy and a **real factor-removal notification
  was received by email**. The factor was not required to be deleted to test.
- Existing HomeAway control, Grafana/SensorDash telemetry and Pi metrics had
  already been reported working.
- An encrypted backup was generated; isolated complete restore remains untested.

## Repository fixes and why they matter

| Concern | Protection now committed | Operator note |
| --- | --- | --- |
| Caddyfile permissions | Read-only audit detects mounted standard or recovery Caddyfile; repair helper takes explicit `--config account-recovery` | **Only** the non-secret Caddyfile may be 0644; keep Cloudflare token root-owned and 0600 |
| Non-root Python images | `COPY --chmod=0644` covers portal, ingest, Pi-hole exporter, diagnostics, account broker, pc-control | Host git checkout umask no longer forces unreadable Python files into images |
| Gateway egress | Recovery overlay pins `infra` default gateway; Compose integration contract checks it | Caddy needs public DNS-01 DNS/API traffic; never set private account-directory as default |
| LLDAP egress | Separate account-ui bridge, LDAP on private directory bridge, raw UI bound to loopback only | Password-reset UI is published only through the private gateway route |
| Authelia egress | Dedicated mail bridge, explicit route priority, no published service port | SMTP uses implicit TLS and a file secret; no broad infra membership |
| Auth configuration | Removed unsupported `AUTHELIA_CONFIG`; kept recognized SMTP settings | Warning persists in an already-running container until its next **intentional** recreation |
| UI deployments | `deploy-ui.sh` detects live Caddy mount and active SMTP notifier overlays | Validates before targeted changes; doesn't restart gateway, VPN or DNS |
| CI validation | Tests non-root source permissions, gateway variants, and **resolved full production overlay stack** with dummy credentials | No live secrets, email, ACME, DNS or VPN changed in CI |
| Resend setup | Separate LLDAP password reset + Authelia factor notifications; operator confirmed delivery from each | Retain legacy `notification.txt`/notifier YAML as historical rollback files until next retention review |

## Safe on-Pi post-pull checks

Commands below do **not** recreate containers, rotate credentials, or restart
any service. Run from the current Pi checkout after fast-forwarding Production.

```bash
cd ~/HomeAwayFromHome
git fetch origin && git switch Production && git merge --ff-only origin/Production
sudo python3 scripts/audit-production.py
bash scripts/deploy-ui.sh --plan
```

The audit intentionally reports host-state backup gaps. If the live gateway
is using account recovery but its file mode is restricted, run the explicit
Caddyfile-only permission helper as the checkout owner:

```bash
python3 scripts/prepare-gateway-permissions.py --plan --config account-recovery
python3 scripts/prepare-gateway-permissions.py --apply --config account-recovery
```

**Do not** run the repair helper on Cloudflare, Resend, LDAP/JWT, SSH, or
Windows pairing secrets. Container source images must be **rebuilt individually**
to adopt Dockerfile changes; a Git pull alone leaves running containers intact.
Avoid doing this unless a relevant image is being upgraded.

## Remaining operational risks and acceptance gates

1. **Isolated restore** still requires real validation. Existing full backup
   coverage and host-side pcctl/inventory/SSH pairing keys must be reconciled,
   including service user/group IDs and systemd socket state.
2. **Account lifecycle**: LLDAP password-reset redemption, second-user factor
   re-enrollment, and provider-error/retry testing have not all been completed.
   See [issue #9](https://github.com/CodyMathis123/HomeAwayFromHome/issues/9).
3. **Second Windows computer**: physical pairing, pinned host key, role checks,
   activate, disable/archive/restore and power commands are not yet exercised.
4. **Operational reliability**: alerting for failed certificates, low disk
   space, stopped services and stale telemetry should be built on existing
   diagnostics rather than creating another monitoring store.
5. **Docker egress**: account-ui and Authelia mail bridge permit outbound
   connections by design; Docker bridge isolation does not make these
   containers network-inaccessible. Keep no public port forwards besides
   the approved WireGuard UDP port.
6. **Secrets**: LLDAP's SMTP environment file is a deliberate compatibility
   exception. Docker administrators may inspect env values; do not publish
   `docker inspect` output, `docker compose config` rendered environment,
   full backup logs or local `secrets/` to tickets or chats.
7. **Pi outage**: Pi-hosted alerts cannot warn you if the Pi is completely
   powered off or has lost connectivity. An external heartbeat is optional
   only if that failure mode merits an outside dependency.
8. **Runtime releases**: non-root Dockerfile fixes and Authelia warning cleanup
   are verified in CI, but need normal targeted rollout in a future service
   update. Running state is not changed by merging a PR.

## Historical branches and stale instructions

`Production` is the sole deployment branch. Some `feature/*`, `fix/*`
and `chore/*` refs are retained after merge and older Phase 3/4 planning
branches contain descriptions that predate the shipped HTTPS/readiness/backup
features. Do not use their onboarding directions on the live Pi.

Old Pi metrics [PR #8](https://github.com/CodyMathis123/HomeAwayFromHome/pull/8)
was closed as superseded rather than merged. Account-recovery issue #9 remains
open only for its explicit remaining acceptance tests, not because mail is
still unimplemented.

A safe *read-only* inventory of branches merged into Production:

```bash
git fetch origin --prune
git branch -r --merged origin/Production
```

Review names before deleting any remote branch; leave unmerged planning
branches and active PR branches alone. This document **does not authorize
an automatic bulk branch deletion**.

## Next implementation order (excluding the restore test)

1. **Finish second-PC management validation.** Complete one real safe
   enrollment and deactivation with pinned SSH trust and admin/operator/viewer
   authorization. This exercises the highest-risk remaining production API.
2. **Actionable operations alerts.** Add admin-only status + opt-in email
   notifications on certificate renewal failure, storage thresholds,
   lost Pi-hole/VM scrape freshness, failed backup jobs, or absent Windows
   collector telemetry. Reuse existing diagnostics and VictoriaMetrics.
3. **Harden deploy/maintenance workflows.** One explicit production preflight
   against the active overlay set, targeted versioned service updates,
   rollback snapshots and a documented monthly maintenance checklist. No
   auto-updating core DNS/VPN containers without approval.
4. **Unify account/computer/settings UX.** Make access state, pending
   invitations, protected users and operations clearer; keep admin
   mutations in the authenticated control API and roles in LLDAP.
5. **Improve long-term SensorDash reporting.** Expand Grafana's
   trends/retention and per-device storage/energy observations while keeping
   PWM/fan curves, hardware binding and sensor aliases on Windows.
6. **Durability work.** UPS/clean shutdown monitoring (if power-loss
   exposure warrants it), filesystem/SSD health alerts, and planned hardware
   migration only if metrics show the Pi's capacity is limiting.
