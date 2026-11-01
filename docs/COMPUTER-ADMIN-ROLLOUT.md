# Phase 2 Computer Management — supervised preview (not deployed)

**State, November 1, 2026:** PR #10 contains the C1 inventory transaction layer and
the first C2/C4 admin-only **active inventory / inactive draft** UI. It is on a
review branch, not running on the Pi. Real Windows enrollment and activation
are deliberately **not implemented yet**.

## What exists

The production `/etc/homeaway-pcctl/hosts.json` remains the **single authoritative
list of computers the Pi can control**. Existing power actions, Pi-side
`pcctl` forced commands, SSH keys, known_hosts and the iOS Shortcut are unchanged.

The new HomeAway admin interface displays the active registry (including private
IP, MAC and SSH settings **to admins only**), allows friendly-name edits
without renaming IDs, and creates/discards unconfigured computer drafts.
A draft is stored separately in `/var/lib/homeaway-pcinventory/drafts.json`,
**not in the active `hosts.json`**, and cannot receive power commands.

The `pc-control` container still mounts `/etc/homeaway-pcctl` **read-only**.
With the *explicit* `compose.computers.yaml` overlay, it accesses a fixed Unix
socket through a read-only mount with the dedicated `PCINV_GID` group.
The host-side `homeaway-inventory` service is a separate non-root identity,
not part of the `pcctl` power-command socket group. It does not access keys
or run remote commands. The browser requires Authelia 2FA and a **current
LLDAP `admins` role**, enforced server-side for every inventory request.

### Supported admin operations

| Action | Behavior |
| --- | --- |
| Read registry | Active entries, separate drafts, inventory revision; admin only |
| Rename | Changes only the display name, preserving immutable ID/IP/MAC/SSH |
| Create draft | Validated private IPv4, MAC, broadcast, SSH user/port; no activation |
| Discard draft | Requires matching draft revision; no active changes |
| Activate, disable, archive, pair Windows | **Not yet implemented**; requires supervised SSH host-key trust |

Viewer and Operator permissions remain unchanged. The original power-control
socket is independent. Duplicate IDs, MACs and IPs are rejected for drafts.

## Future operator review (not a request to run this yet)

1. Confirm an off-device copy of `/etc/homeaway-pcctl/hosts.json` and
   the existing `pcctl` keys and that the iOS Shortcut still works.
2. In the Phase 2 review checkout, **preview** without changes:

   ```bash
   bash scripts/install-pcinventory-host.sh --plan
   ```

3. Only in a supervised activation session, after reviewing the script,
   use `sudo bash scripts/install-pcinventory-host.sh --apply`. It validates
   the existing inventory, snapshots it to the root-only
   `/var/backups/homeaway-pcinventory/` directory, adds the **separate**
   `homeaway-inventory` Linux user, and installs only the new socket service.
   It does **not** restart the existing `pcctl` socket or change inventory rows.
   The fixed host inventory directory becomes owned by the dedicated writer,
   and `hosts.json` remains 0644 read-only to other users (the existing
   original read model). The inventory is network metadata only; it must
   never include passwords, SSH keys, or tokens. The Unix socket and draft
   state remain private (0660 and 0700).
4. Record `PCINV_GID` reported by the installer in local `.env`, then
   explicitly enable `HOMEAWAY_COMPUTER_ADMIN_ENABLED=true`. Add
   `-f compose.computers.yaml` to the already working
   `compose.yaml` + `compose.control.yaml` + `compose.accounts.yaml`
   invocation, **recreating only `pc-control`**, never Pi-hole/WireGuard.
5. Verify admin-only list, draft creation/discard, unchanged live power controls
   and Shortcut. Do not attempt Windows activation from this preview.

**No commands in this section have been run by the assistant on your Pi.**
Do not copy this overlay into a live deployment until the relevant review
and operational acceptance gate is explicitly agreed.

## Error cases and recovery

- Missing service or unset flag: the new UI stays hidden; existing power
  controls continue to use the unchanged `pcctl` agent.
- Invalid/corrupt inventory or draft registry: writes fail closed.
- Conflicting global/draft revisions: refresh and retry; no silent overwrite.
- Every inventory transaction takes a cooperative file lock and stages an
  atomic replacement, with the last valid active file retained as
  `hosts.json.previous`. External editors **must use the same lock**.
- On an interrupted rollout, disable the new feature flag and remove
  `compose.computers.yaml`, recreate **only `pc-control`**, and stop the
  new `homeaway-inventory.socket`. Preserve existing host IDs and never
  overwrite `hosts.json` from an old snapshot without first inspecting
  intervening changes. Restore original owner/mode only in a deliberate
  manual rollback.

## Next development packages

- C3: guided Windows SSH and WOL onboarding with explicit host-key
  fingerprint confirmation. No auto-accept, no arbitrary LAN scanning,
  no SSH private keys transmitted to a browser.
- C4: verification and activation of reviewed drafts, safe disable/archive
  lifecycle, clear network/trust diagnostics and ergonomic mobile workflow.
- C5: integration tests and supervised Pi/Windows validation. Never infer
  actual power state from a TCP/22 reachability probe.
