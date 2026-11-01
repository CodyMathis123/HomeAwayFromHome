# Phase 2 Computer Management — supervised preview (not deployed)

**State, November 1, 2026:** PR #10 contains the C1 inventory transaction layer and
the C2/C4 admin inventory lifecycle UI, verified SSH status checker and
out-of-band key-pinning helper. It is on a **review branch**, not running on
the Pi. No Windows pairing, activation, deactivation or archival has been
physically tested yet.

## What exists

The production `/etc/homeaway-pcctl/hosts.json` remains the **single authoritative
list of computers the Pi can control**. Existing power actions, Pi-side
`pcctl` forced commands, SSH keys, known_hosts and the iOS Shortcut are unchanged.

The new HomeAway admin interface displays the active registry (including private
IP, MAC and SSH settings **to admins only**), allows friendly-name edits
without renaming IDs, and creates/discards unconfigured computer drafts; with explicit confirmation it can
activate a **freshly SSH-verified** draft, disable an active host, archive its
retained connection metadata or reverify and restore a retired computer.
A draft is stored separately in `/var/lib/homeaway-pcinventory/drafts.json`,
**not in the active `hosts.json`**, and cannot receive power commands.

A separate `homeaway-pcverify` systemd socket executes only pinned-host-key
SSH `status` as the existing unprivileged `pcctl` Linux user. Only a matching
ED25519 host-key pin **and** a successful restricted Windows `status` response
can activate or restore a computer. The inventory writer cannot read SSH
private keys and the browser never receives them. The `pin-pc-hostkey.py`
script requires a full fingerprint read **locally on Windows**, compares the
Pi network scan to that independent fingerprint and refuses to overwrite
existing trust without manual review.

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
| Verify draft | Pi runs pinned SSH host-key and authenticated forced-command `status` check, never WOL or power mutation |
| Activate draft | Typed ID, current inventory and draft revisions, **fresh** SSH verification, then atomic promotion to active commands |
| Disable | Typed ID, archive snapshot written **first**, then removes host from active command registry; in-flight commands cannot be cancelled |
| Archive retired host | Keeps private recovery snapshot and audit; host remains disabled |
| Restore retired host | Typed ID, current revisions, pinned host-key and authenticated SSH **reverified**, then atomic active promotion |
| Prepare Windows | Manual local elevated setup and independently verified SSH fingerprint; no remote arbitrary installer |

Viewer and Operator permissions remain unchanged. The original power-control socket is independent. Duplicate IDs, MACs and IPs
are rejected across active, drafts, and retired records. Retirement metadata is
stored in `/var/lib/homeaway-pcinventory/retired.json` (private 0600). A disabled
computer immediately becomes an unknown target for new HomeAway and Shortcut
power requests. On failure to remove the host after saving its archive, the
active inventory remains authoritative and the overlapping record is flagged;
retry Disable after reviewing. If a Restore succeeds but archive cleanup fails,
HomeAway flags the overlap for manual inspection instead of deleting ambiguous
recovery data. The system does **not** revoke already executing SSH/power actions.

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
   `homeaway-inventory` Linux user, and installs both new sockets: the restricted inventory writer and the
    `pcctl`-user non-destructive SSH verifier.
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
5. Verify admin-only inventory listing, draft creation/discard, and existing
   PC/Shortcut functionality first. **Do not activate or disable an existing
   computer in the initial smoke test**. For a truly new test PC, first use
   the reviewed local Windows installer, independently note its ED25519 host
   fingerprint on the Windows console, then run the Pi trust helper:

   ```bash
   python3 scripts/pin-pc-hostkey.py --ip 192.168.1.120 --port 22 --expected 'SHA256:REPLACE_WITH_WINDOWS_CONSOLE_VALUE' --plan
   sudo python3 scripts/pin-pc-hostkey.py --ip 192.168.1.120 --port 22 --expected 'SHA256:REPLACE_WITH_WINDOWS_CONSOLE_VALUE' --apply
   ```

   These are **examples only**, not a suggestion to pin the shown IP or run
   anything without a matching Windows fingerprint. Confirm that `pcctl`
   can SSH to the Windows host with the existing forced `status` command,
   then use the two-step **Verify SSH → Activate verified computer** UI.
   Actual Wake/Sleep must be tested separately, under supervision.

**No commands in this section have been run by the assistant on your Pi.**
Do not copy this overlay into a live deployment until the relevant review
and operational acceptance gate is explicitly agreed.

## Error cases and recovery

- Missing service or unset flag: the new UI stays hidden; existing power
  controls continue to use the unchanged `pcctl` agent.
- Invalid/corrupt inventory, draft or retired registry: writes fail closed.
- Conflicting active/draft/retired revisions: refresh and retry; no silent overwrite.
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

- C3: polish the reviewed Windows installation handoff and make manual
  fingerprint comparison/SSH trust diagnostics easier; no auto-accept, no
  arbitrary LAN scanning or SSH keys transmitted to a browser.
- C4: refine mobile/keyboard usability, explain unknown/offline SSH status,
  and expose bounded administrative audit history.
- C5: isolated end-to-end integration, actual Pi/Windows supervised validation,
  rollback and break-glass review before merging/deployment. Never infer
  physical power state from a TCP/22 probe or unconfirmed WOL packet.
