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
2. In the Phase 2 review checkout, **run the real read-only preflight**:

   ```bash
   sudo bash scripts/install-pcinventory-host.sh --plan
   ```

   It validates the canonical inventory, permission and symlink safety, and
   existing `pcctl` SSH prerequisites. The privileged preview can inspect
   otherwise private files **without printing their contents**. Missing
   pairing keys produce a `PENDING` warning; an unsafe active inventory
   fails closed. Ordinary-user `--plan` also works, but may not be able
   to inspect the `pcctl` private SSH directory.

3. Only in a supervised activation session, after reviewing the script,
   use `sudo bash scripts/install-pcinventory-host.sh --apply`. It repeats
   preflight and validates installation sources **before** changing
   ownership, snapshots the existing inventory to the root-only
   `/var/backups/homeaway-pcinventory/` directory, adds the **separate**
   `homeaway-inventory` Linux user, and installs both new sockets: the restricted inventory writer and the
    `pcctl`-user non-destructive SSH verifier.
   It does **not** restart the existing `pcctl` socket or change inventory rows.
   The fixed host inventory directory becomes owned by the dedicated writer,
   and `hosts.json` remains 0644 read-only to other users (the existing
   original read model). The inventory is network metadata only; it must
   never include passwords, SSH keys, or tokens. The Unix socket and draft
   state remain private (0660 and 0700).
4. Before changing any Compose configuration, run a harmless socket
   test on the Pi:

   ```bash
   sudo bash scripts/verify-pcinventory-host.sh
   ```

   This exercises the inventory writer with a **read-only list** and sends
   an invalid operation to the SSH verifier, which must reject it without
   opening a network connection. It checks socket ownership/groups and
   leaves the existing `pcctl-agent.socket` running. It prints only the
   number of active computers, never addresses, MACs or SSH credentials.

5. Record `PCINV_GID` reported by the installer in local `.env`, then
   explicitly enable `HOMEAWAY_COMPUTER_ADMIN_ENABLED=true`. Add
   `-f compose.computers.yaml` to the already working
   `compose.yaml` + `compose.control.yaml` + `compose.accounts.yaml`
   invocation, **recreating only `pc-control`**, never Pi-hole/WireGuard.
6. Verify admin-only inventory listing, draft creation/discard, and existing
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
- On an interrupted rollout, disable the new feature flag in the Pi's
  `.env` and **omit** `compose.computers.yaml` from your Compose command.
  Recreate **only `pc-control`** (use the original working overlays):

  ```bash
  sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml \
    --profile metrics --profile control --profile accounts \
    up -d --no-deps --force-recreate pc-control
  bash scripts/disable-pcinventory-host.sh --plan
  sudo env HOMEAWAY_COMPUTER_ADMIN_ENABLED=false \
    bash scripts/disable-pcinventory-host.sh --apply
  ```

  The rollback stops **only** `homeaway-inventory` and `homeaway-pcverify`
  services/sockets; it does not touch `pcctl-agent`, the Windows SSH
  configuration, VPN, DNS, WOL or any SensorDash fan-control process. It
  leaves ownership and private retirement/draft/audit data in place for
  deliberate recovery. If an inventory mutation had already succeeded,
  those changes are still authoritative. **Never** blindly overwrite
  `hosts.json` from a pre-install snapshot or erase its history. Restore
  original file ownership only as a separate, reviewed operation.

## Next development packages

- C3: polish the reviewed Windows installation handoff and make manual
  fingerprint comparison/SSH trust diagnostics easier; no auto-accept, no
  arbitrary LAN scanning or SSH keys transmitted to a browser.
- C4: refine mobile/keyboard usability, explain unknown/offline SSH status,
  and expose bounded administrative audit history.
- C5: read-only Pi preflight, opt-in host socket smoke and management-only
  rollback scripts are implemented with synthetic CI validation. Next: actual
  Pi/Windows supervised verification and break-glass recovery before merging
  or deploying. Never infer physical power state from TCP/22 or a WOL packet.
