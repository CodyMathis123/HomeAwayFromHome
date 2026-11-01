# Production hardening / verified recovery scope

The first production hardening step is a **read-only audit**, not a permission
repair or automatic backup migration. Run `sudo python3
scripts/audit-production.py` on the Pi after checkout. This checks the exact
Caddyfile and token modes that previously caused restart loops, the control
API source readability, the Caddy mount selected by the live container, and
which host-side computer management resources are present.

## File permissions policy

| Resource | Ownership and permission requirement |
| --- | --- |
| `control/gateway/Caddyfile.production` | Readable by non-root Caddy, normally mode 0644 |
| `control/api/app.py` | Readable in checkout; Dockerfile uses `COPY --chmod=0644` so installed runtime doesn't depend on checkout modes |
| `secrets/control/cloudflare_dns01_token` | Root-owned, mode 0400 or 0600, never made world-readable |
| Authelia, pcctl credentials, Windows pairing key | Keep restricted to intended service identity, never fix via broad chmod |
| Writable inventory and audit state | Preserve ownership assigned by the host agent installers, not Caddy/Compose |

The first live audit found the canonical Caddyfile at mode 0600 (all other
permission checks passed). This is consistent with a restrictive checkout umask,
but the precise source of the mode has not been proven. The live gateway was
mounted from the correct file; this does not prove a new Caddy process could
read it. The fix is narrow and safe because this Caddyfile has no embedded
Cloudflare credentials:

```bash
python3 scripts/prepare-gateway-permissions.py --plan
python3 scripts/prepare-gateway-permissions.py --apply
sudo python3 scripts/audit-production.py
```

Run the preparation script as the checkout owner, without sudo. It only sets
`control/gateway/Caddyfile.production` mode 0644 after rejecting symlinks
and hardlinks. On future `scripts/deploy-ui.sh --apply` runs, the same narrow
normalization is automatic after Git sync, before any UI container rebuild.
`--plan` and the audit remain read-only. A dedicated gateway cutover must
still use the production cutover's permission and offline Caddy checks.

A failing audit blocks a manual deployment decision; it does not mutate the
live system. Keep the gateway and Windows fan-control service running while
investigating a failed check.

## Full recovery is NOT done by the current backup

`scripts/backup.sh --offline` quiesces Compose containers and archives only
`.env`, `secrets/`, and `data/`. It cannot recreate the host-side computer
management system on a replacement Pi.

The following need a designed, **encrypted off-device**, permission-preserving
backup and a restore test, without exposing their content in CI or Git:

- Active PC inventory `/etc/homeaway-pcctl/hosts.json` plus the intended
  associated configuration.
- `pcctl` SSH identity and pinned Windows host keys; a replacement key
  would require explicit re-pairing.
- Inventory draft/retired/audit state under `/var/lib/homeaway-pcinventory`.
- Installed host-side agent code, systemd unit/socket overrides, and
  account/group ownership mapping necessary to recreate privileges.
- Docker app state, Cloudflare token, Authelia state, VictoriaMetrics history
  and other existing files covered by the current backup.
- Verified list of service sockets/write locks so a backup does not silently
  capture inconsistent live inventory/retirement data.

## Acceptance gates for a follow-up backup PR

1. `--plan` lists required and optional paths, absent paths, writable
   services, and outage boundaries **without displaying secrets**.
2. One combined archive manifest records checksums, file modes, owners,
   services/version details, and the schema version; no private keys in logs.
3. Snapshot is coordinated with host agent writes, not just Docker stopping.
4. Archive is encrypted with an operator-managed key and copied **off** the Pi;
   do not create a second unencrypted backup in Git or a cloud workspace.
5. A restore onto disposable storage proves paths, service identities,
   certificate/trust continuity, socket access and one read-only inventory
   query before claiming disaster recovery completed.
6. Test failed/partial archive cleanup and restart-on-error behavior without
   disrupting the only active PC or household DNS in CI.

Until those gates are met, keep the existing backup working and do **not**
claim it is a complete Pi/Phase 2 recovery archive.
