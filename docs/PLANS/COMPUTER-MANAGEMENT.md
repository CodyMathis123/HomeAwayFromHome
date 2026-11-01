# Computer inventory, pairing and management UI — technical implementation plan

> **Implementation status (November 1, 2026):** Phase C1 (shared locked, revisioned, atomic active-inventory updates) has been started on `feature/homeaway-computer-management-impl` from merged `Production`. Account-management authorization and its live Pi/iOS tests are complete. C2–C5 (dedicated inventory service, authenticated UI, guided Windows onboarding and deployment) are planned but **not yet implemented**. Existing active `pcctl`, iOS Shortcut, Windows SSH keys and fan controls remain unchanged.

## 1. Verified existing infrastructure

The current `/control/` page displays all active computers and exposes `Wake`, `Sleep`, `Restart`, `Shutdown`, current SSH TCP-reachability status and a bounded activity log. It has **no inventory editing or onboarding UI**.

- Canonical Pi inventory: `/etc/homeaway-pcctl/hosts.json` with `{"hosts":[...]}`; `control/pcctl/core.py:load_hosts` validates ID, display name, private IPv4, MAC, broadcast, SSH username and port; max **64** hosts. `control/api/app.py:inventory` exports only `id` and `name`, intentionally hiding network configuration from the ordinary UI.
- CLI `scripts/add-pc.py` only **adds** a host, but now calls a shared `control/pcctl/inventory_store.py` transaction layer with Linux advisory locking, canonical live validation, optimistic revision, atomic file replacement, previous-valid snapshot, symlink checks and owner/mode preservation. Duplicate IP/MAC additions are denied. No user-facing edit/deactivate UI or host pairing yet. Runtime `core.perform` re-reads the JSON on every action, so active inventory edits need no restart.
- Power RPC: `pc-control` runs unprivileged in isolated Compose network, sees inventory read-only and group-restricted `/run/homeaway-pcctl/agent.sock`. The non-root Pi host agent performs allowlisted WOL or SSH with pinned known_hosts (strict host key checking). It **does not** accept arbitrary shell commands; `pcctl.py` and the `SSH_ORIGINAL_COMMAND` dispatcher preserve `pcctl HOST ACTION` for the existing iOS Shortcut.
- Actual Windows integration: `control/windows/Install-PcctlWindows.ps1` (admin PowerShell) installs/uses a restricted local `pcctl` account, a Windows SSH forced-command dispatcher, Pi-only firewall restriction, key-only SSH and `PsShutdown.exe` for sleep. It is intentionally cautious if an existing Match block is present. Windows shutdown/restart rights, power-state differences, sleep semantics and NIC WOL settings remain hardware-specific and must be verified on each PC.
- `status` means TCP/22 (or configured SSH port) reachable, **not** an authenticated login, Windows service health or definitive power state. Sending a WOL packet or SSH power command means **requested**, not confirmed completion. The UI must preserve this distinction.
- Authelia now authenticates via LLDAP. The existing control API resolves fresh `admins/operators/viewers` roles through its broker; live Viewer → Operator promotion and immediate revocation were verified by the operator before the Phase 2 branch.

## 2. Desired experience

HomeAway `/control/` gets a **Computers** list with an admin-only `Add computer` button. On desktop and iPhone, an administrator can see current online/unknown state, edit a friendly name, review health and enrollment progress, disable or retire entries, and follow a guided setup for new Windows computers. Operators see allowed action buttons only; viewers see statuses. UI capability flags are conveniences, not security controls.

### Wizard: enroll a Windows PC

1. **Identity:** choose immutable safe ID (e.g. `living-room`) and a human-friendly display name; indicate which physical machine will be enrolled and whether existing `pcctl` service is installed. Stable ID is used in iPhone Shortcuts, history and links.
2. **Network settings:** enter or suggest private IPv4, MAC, broadcast and SSH port with clear validation; no arbitrary subnet scans. Show exactly how to find these on Windows. Confirm wired NIC/WOL assumptions and account for Wi-Fi limitations.
3. **Windows setup:** download a pinned/hash-verified installer package containing the reviewed PowerShell script and dispatcher (or show command from trusted checkout). One-time elevated execution with `-WhatIf`, explicit administrator consent, backup and restore instructions. Reuse an existing hardened `pcctl` account rather than overwriting it.
4. **Trust and connection:** show the Pi's **public** SSH key to install on Windows; verify the Windows SSH host-key fingerprint over a trusted local channel before adding to the Pi's known_hosts. Never transmit or render the Pi private key; never use `StrictHostKeyChecking=no`. Confirm forced-command status via authenticated SSH, not merely TCP reachability.
5. **Power capabilities:** separate indicators for `status`, `wake`, `sleep`, `restart` and `shutdown`; capabilities must be explicitly tested rather than inferred. PsShutdown may be missing, Windows may not have account shutdown rights, BIOS/NIC WOL may be disabled. Destructive tests require opt-in and supervision, not performed automatically by the wizard.
6. **Activation:** show the draft, validate canonical schema and active host conflicts; only then promote to active inventory. Log the initiating admin and inventory revision. Confirm the new host appears in both browser and `pcctl HOST status`, preserving the other machines and existing Shortcut commands.

A valid host without completed Windows setup remains **Not configured** (draft), never a fake “online” device. Host deletion is an explicit archive/deactivate workflow with typed confirmation and a warning that old iPhone Shortcuts referring to the host ID will stop working.

## 3. State and data model (single active inventory source)

Keep **`/etc/homeaway-pcctl/hosts.json` as the sole authority for active routing/commands** and reuse `load_hosts` validation; do not introduce a competing computer database that the agent might ignore. The current strict schema exposes:
```json
{"hosts":[
  {"id":"living-room","name":"Living Room PC",
   "address":"192.168.1.120","mac":"aa:bb:cc:dd:ee:ff",
   "broadcast":"192.168.1.255","ssh_user":"pcctl","ssh_port":22}
]}
```

Add separate lightweight administration state (e.g. a small SQLite database on the Pi) for **drafts and diagnostics**, keyed by the same immutable `id`: draft steps, last verified SSH fingerprint/time, last tested capability, onboarding actor, failure codes, and archived records. Draft data is not acted upon by pcctl and must not silently change live routing. Never store password/SSH private key in this DB.

Suggested states: `draft` → `windows-setup-needed` → `host-key-verification-needed` → `ready-to-activate` → `active`, with `degraded` (active but status/capability failed), `disabled` (excluded from live inventory), `archived` (history retained). The display should distinguish **inventory status**, **SSH connectivity**, and **actual power state (unknown unless measured)**. An active but offline PC is not automatically disabled.

**Edits:** display-name-only changes may be immediate; changes to address/SSH port/Windows public key/host fingerprint require a staging + test + approve transaction with rollback. Do not allow renaming an active host `id` in place initially (Shortcuts depend on it). Do not infer broadcast address solely from “private IP”; require network mask or explicit verification of the NIC/subnet.

**C1 implemented:** A shared `inventory_store.update_inventory` performs cooperative Linux lock, optimistic revision, canonical validation, temp-file fsync, owner/mode preservation, atomic replace and a one-deep previous-valid snapshot. `scripts/add-pc.py` now calls it, preventing races between cooperating CLI updates. **Remaining for C2:** Ensure a future dedicated writer uses the same helper and lock; implement safe UI-mediated read-back, broker audit, path protections for its fixed privileged destination, and the activation/rollback workflow. Direct external editors that bypass the lock remain unsupported.

## 4. Least-privilege management path

```text
Authelia 2FA / Caddy verified identity and groups
     |
     v
/control/api/admin/computers (HomeAway control UI, strict admins role)
     |
     v
Narrow inventory writer endpoint / dedicated Unix socket with own ACL
     |
     +-> validate active inventory and stage transaction
     +-> root-managed or separately owned hosts.json only (no SSH key access)
     +-> audit revision, backup, reload-less activation
     |
existing pcctl power socket (unchanged) -> action allowlist / pinned SSH
```

Do **not** mount `/etc/homeaway-pcctl` writable in the main web controller, grant `pc-control` root privileges, expose Docker's socket, or reuse the existing power-command socket as arbitrary inventory/config editor. The inventory writer should have exactly one path to modify, no generalized file path argument, no shell API, no SSH private key/known_hosts write access unless a separately reviewed **host-key enrollment service** is deliberately introduced with its own validation and least privilege.

Implement server-side role and action checks independently of buttons. Suggested command permissions:

| Operation | admins | operators | viewers |
| --- | --- | --- | --- |
| Read computer status | Yes | Yes | Yes |
| Wake and sleep eligible active host | Yes | Yes | No |
| Restart/shutdown | Yes | No until specifically delegated | No |
| Add/edit/deactivate/archive computer | Yes | No | No |
| Enroll/rotate SSH trust, manage pairing | Yes | No | No |
| Read network internals, MAC, SSH details | Yes | No (unless justified) | No |
| Review administrative audit | Yes | No | No |

The existing `Caddyfile` must verify an authenticated username from Authelia while clearing inbound spoofed headers. With account administration enabled, the control API retrieves the user's **current** role from LLDAP through the broker for every protected request; it must not trust browser-supplied role/group headers. Missing directory role or broker outage fails closed. Denied actions should never reach the host RPC. For truly immediate disable/role change, recheck active account state on privileged requests or revoke existing sessions (see account plan).

Host permissions are **not Windows OS user permissions**: Windows accepts requests only from the Pi's key-only, forced-command account, while HomeAway gates *which household users* may ask for each action.

## 5. Proposed API endpoints and precise behavior

These route names are a design sketch, **not yet implemented**:

| Method | Endpoint | Who | Behavior |
| --- | --- | --- | --- |
| `GET` | `/control/api/hosts` (existing) | viewer/operator/admin | Redacted list + status, no MAC or private keys |
| `POST` | `/control/api/actions` (existing) | operator/admin depending verb | Existing schema/cooldown, role-check action before RPC |
| `GET` | `/control/api/admin/computers` | admin | Active and drafts with validated fields, ETag/revision |
| `POST` | `/control/api/admin/computers` | admin | Create draft; never implicitly activate / send WOL |
| `PATCH` | `/control/api/admin/computers/{id}` | admin | Allowed edits with revision precondition; stage risky edits |
| `POST` | `/control/api/admin/computers/{id}/verify` | admin | Controlled auth-status/host-key diagnostic; no destructive commands |
| `POST` | `/control/api/admin/computers/{id}/activate` | admin | Promote verified draft to live inventory transaction |
| `POST` | `/control/api/admin/computers/{id}/disable` | admin | Remove from active routing, keep audit/restore metadata |
| `POST` | `/control/api/admin/computers/{id}/archive` | admin | Confirm, retain bounded history; no key cleanup without review |
| `GET` | `/control/api/admin/computers/{id}/diagnostics` | admin | Structured validation and failure categories, redact secrets |

Require JSON only for changes, origin/CSRF token checks, bounded request size, strict allowlisted JSON fields, safe IDs, explicit confirmations on deactivate/key changes, per-user and per-host rate limits, idempotency keys for retry-prone requests, and error handling that does not leak key paths/credentials. Display recent activity by actor and host but restrict access/redact network details. Never place raw shell commands or user-supplied paths in an RPC payload.

### Windows bootstrap package (not a general remote install)

Keep the current `control/windows/Install-PcctlWindows.ps1` and `dispatch.ps1` as auditable implementation source, with packaging and an explicit version/manifest/hash, not a new privileged resident Windows service. Windows installer prerequisites:
- OpenSSH Server installed and working; `sshd_config` backup and `sshd -t` validation before restart.
- Non-admin `pcctl` local account and key-only restricted `Match User` ForceCommand; detect existing block and provide review/reuse path.
- Harden file ACLs and Pi-only inbound firewall access without blindly breaking other administrator SSH rules.
- Account rights for sleep/restart/shutdown tested separately. PsShutdown obtained from its legitimate source and verified; do not bundle or silently download unknown binaries.
- Preserve `authorized_keys`, `known_hosts`, Windows profile, user files and iOS Shortcut paths when upgrading or removing HomeAway.
- `-WhatIf` and post-install human-readable report. No automatic write to Windows until a local administrator explicitly starts the installer.

Future optionally use a signed MSI/PowerShell package if this becomes frequent enough; *not* a prerequisite for the first UI enrollment.

### Browser HTTPS and Windows trust are intentionally separate

Phase 3 will eventually change the browser control URL from a private `.home.arpa` hostname to `home.homeawayfromhome.house:8443` with publicly trusted Let's Encrypt certificates. This is **not** part of computer enrollment and should not change the `pcctl` command socket, Windows SSH host keys, Windows account permissions, WOL MAC/IP data or existing iOS Shortcut commands. Do not try to use a Let's Encrypt X.509 HTTPS certificate as a substitute for Windows SSH host-key fingerprint verification. Before any hostname change, test Authelia group claims, CSRF allowed origins, authenticated control actions and Shortcut continuity. No LAN/VPN access control is relaxed.

## 6. Edge cases and recovery

- **Existing machines:** import `work` and `gaming` without changing IDs/IP/MAC/keys, reconstruct a read-only inventory from the host file, attach draft metadata only after admin opts in; test before modifying any live entry.
- **Changing IP:** stage the new address, verify known host key against the expected fingerprint, test authenticated status, then commit; keep old connection details for immediate rollback. Never silently trust a new SSH key.
- **Removing host:** disable commands and hide from operational list, but retain audit references and don't purge logs/credentials without explicit separate rotation; clarify that saved Shortcuts will fail with an unknown-host result.
- **Lost PC / hostname spoof / duplicate IP:** prevent fingerprint auto-acceptance and display Host identity mismatch rather than treating SSH port reachable as success. Do not automatically scan or enroll devices on the LAN.
- **Offline Windows / sleeping machine / VPN unavailable:** can still display configuration state; mark reachability unknown or offline. WOL is a packet send, not proof of boot. Avoid blocking the UI on long TCP probes; bound the existing parallel checks and cache short status windows.
- **Two admins edit concurrently:** optimistic revision conflict not silent overwrite; stage changes and serialize commit.
- **Disk full / partial JSON / restart:** retain previous valid inventory and atomic fsync, fail closed for new commands; preserve power agent read access and previous backups.
- **Untrusted forwarded identity:** refuse write even if the UI displays admin; Caddy, API, inventory-writer ACL and agent each provide distinct boundary enforcement.
- **Private network outages:** HomeAway failure must not alter SensorDashCollector, motherboard fan control, Pi-hole or WireGuard. No remote “fix fans” buttons.

## 7. Implementation work packages

### C0 — prerequisites and baseline (completed where noted)

`Production` includes the implemented account/RBAC feature. The operator confirmed working actual PC power controls and iOS Shortcut after account rollout and demonstrated roles using a second live test user. Before any later Phase 2 deployment, preserve `/etc/homeaway-pcctl/hosts.json`, Pi `pcctl` keys and the approved iOS Shortcut. This implementation branch is created from the merged `Production` commit, not from the older planning-only branch.

### C1 — transaction-safe inventory backend (initial implementation complete; UI integration pending)

Refactored `scripts/add-pc.py` and canonical validator and added `control/pcctl/inventory_store.py` for locking, revision conflicts, atomic replacement, previous-file snapshot and preservation of current mode/owner. Tests cover corruption, failed replacement, symlinked path/lock, conflict, permissions and cooperative concurrent writers. **Remaining:** dedicated inventory service, draft metadata, actual admin UI and lock sharing across CLI and service, and real-machine smoke. No change to running inventory until an operator chooses to deploy.

### C2 — authenticated admin APIs

Add read-only host diagnostics and admin-only CRUD/activate/disable routes behind verified Authelia claims, strict same-origin controls and dedicated writer boundary. Add policy matrix on existing `/control/api/actions`. Review Caddy/Compose group propagation and the Pi host socket ACLs. Tests must ensure no forged forwarded group can edit inventory, no read-only portal admin surface, and no API can read SSH private keys.

### C3 — guided Windows provisioning

Publish reviewed Windows bootstrap package with `-WhatIf`, default non-destructive behavior, signed/hash manifest and operator instructions. Implement a trust-verification handoff and authenticated status check. Detect existing forced command and do not clobber it. CI can exercise PowerShell syntax/mock OpenSSH config, but real Windows needs a supervised VM before the operator's live machines.

### C4 — mobile-friendly UI

Add Computers management screens to the existing `control/api/index.html`, `app.js`, `styles.css`, minimizing new JS frameworks. Each step explains state and next action, handles refresh/resume, works on iPhone Safari, keyboard/screen readers, disables forbidden buttons and uses backend-derived capabilities. Group status, action, configuration and audit separately to avoid an overwhelming single page.

### C5 — verification, deployment and rollback

Run `python3 -m unittest discover -s scripts/tests -v`, static code checks, Docker Compose/Authelia/Caddy validations and `scripts/check_control_contracts.py`; add integration tests for inventory IPC and live Caddy/role forwarding with mock Windows endpoints. Test feature on a cloned Pi/test container and Windows VM first. On a real machine, validate **status** and WOL before separately supervised sleep/restart/shutdown; verify Shortcut compatibility. Test failed inventory writer, failed Authelia, internet outage and unchanged SensorDash fan control. Provide one-click disable for the management UI without disabling existing PC power controls.

## 8. Acceptance criteria

**Functional:** An admin can add a third Windows PC through the UI with at most one local elevated Windows install and explicit host-key verification; saved inventory survives Pi restart and is immediately reflected in the browser, CLI and iOS Shortcut. Admin can rename a display name and retire a PC safely without changing any other machine. Existing Windows hosts and their keys are untouched.

**Security:** A viewer can't submit any control action; an operator can use only allowed verbs; an admin can perform CRUD; forged headers, invalid CSRF, path traversal, disallowed host addresses, shared credentials and concurrent edits are blocked; no Docker socket or SSH private keys are exposed; all changes are audited with real verified identity.

**Reliability:** Any failed edit returns a clear reason and leaves the last valid inventory intact; no stale identity fingerprint is silently accepted; read-only status doesn't imply authenticated power state; WOL success only means a magic packet was emitted. No fan-control dependency or network polling duplication is introduced.

## 9. Files likely to change (implementation, not in this plan commit)

- `control/api/app.py`, `app.js`, `index.html`, `styles.css` — admin-only routes/UI, server-side role checks and onboarding status.
- `control/pcctl/core.py`, new `control/pcctl/inventory_store.py`, `scripts/add-pc.py` — shared validated transaction and registry ownership.
- New narrowly privileged Pi inventory administration agent with separate systemd socket and ACL; update `scripts/install-pcctl-host.sh` carefully to preserve legacy configurations.
- `control/windows/Install-PcctlWindows.ps1`, `dispatch.ps1`, packaging/manifest scripts — Windows bootstrap and reviewable diagnostics.
- `control/gateway/Caddyfile`, `compose.control.yaml`, `scripts/check_control_contracts.py` — admin route, claims and no new host ports.
- `scripts/tests/test_pc_control.py`, new inventory-admin/Windows enrollment contract tests, `docs/PC-CONTROL.md`, `docs/SECURITY.md`, `docs/INTEGRATION-TEST.md`, new architecture ADR.

**No SensorDash source modifications are planned.** SensorDash continues to be the authoritative fan-control and sensor collector, and its MSI pipeline is not a PC power-onboarding mechanism.

## 10. Research and follow-up decisions

- Decide whether operator permissions should initially be `wake/sleep` only and whether per-host grants are needed for the household. Default documented policy above is intentionally conservative.
- Confirm whether Windows sshd forced-command PowerShell script works with non-admin account and the target Windows versions, including SeShutdownPrivilege and PsShutdown licensing; do not design a “magic one-click install” that conceals UAC or absent rights.
- Evaluate whether private-network discovery is worth privacy/security costs. **Not needed** for v1; user enters IP/MAC and verifies them.
- Ensure the existing `pcctl` host agent and Windows Shortcut don't need code changes for an added host; only the inventory and correct keys should change.
- Consider portable SSH host-key enrollment through fingerprint confirmation and a narrow host-only trust writer; never let the browser submit arbitrary `known_hosts` bytes or unverified fingerprints.
