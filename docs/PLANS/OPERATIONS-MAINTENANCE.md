# Phase 4 — Operations, health, backups and controlled updates

**Planning only.** This branch builds on Phase 3 (feature/homeaway-setup-monitoring), inheriting the shared identity, roles, diagnostics, configuration and privileged-broker contracts. Implementation must not begin until the current full stack and Phases 1–3 are accepted on real hardware. No code, credentials, deployments or services are changed here.

## User experience and boundaries

HomeAway administrators get a protected Operations area with System Health, Backups, Updates and Recovery. It should make everyday service visibility easy while preserving an offline recovery path if the browser/Authelia/Pi is unavailable.

Read-only service health should distinguish responding, correctly configured, fresh, client-verified, failed and unknown. Browser requests never gain shell/root privileges, Docker socket access, SSH keys or unrestricted file access. Any privileged operation uses an isolated allowlisted maintenance broker and a reviewed plan; the existing read-only :8088 portal remains unchanged. Operators/viewers get at most appropriately redacted health, never backup keys, unredacted logs or update actions.

**Do not rebuild Grafana.** Reuse Phase 3 health checks and links to existing HomeAway System Health, Storage, Events and telemetry dashboards. Also preserve native Pi-hole/wg-easy administration rather than adding a second peer-management UI.

## Audit of existing code: important gaps

| Existing file/data | Today | Follow-up |
| --- | --- | --- |
| scripts/doctor.sh, smoke.sh, verify-telemetry.sh, verify-pi-health.sh, verify-service-monitoring.sh | Console-only checks with different output styles | Phase 3 should provide stable structured IDs, caching, timestamps, source/evidence and failure reason; Phase 4 consumes those checks |
| scripts/backup.sh | Stops services via core Compose + metrics profile, then tars .env, secrets and data | **Does not use the optional compose.control.yaml overlay when enumerating/stopping services.** Audit for a potentially live Authelia SQLite database during backup |
| scripts/backup.sh contents | Only project root .env, secrets and data | **Excludes active /etc/homeaway-pcctl/hosts.json, /home/pcctl/.ssh key and known_hosts, pcctl service/unit definitions**. A valid restore may otherwise lose registered PC functionality |
| scripts/update.sh | Updates pinned core Pi-hole/wg-easy and portal only | Explicitly lacks the optional Grafana/VM and Authelia/Caddy/control overlay; create a manifest-driven, dependency-aware update process |
| docs/OPERATIONS.md | Manual tar/age advice and disaster-recovery instructions | Require encrypted verified off-device copies, full restore rehearsal and clear downtime/secret risk |
| data/control/authelia and data/control/caddy | Account file, SQLite, factors, existing Caddy internal CA, and **future Caddy Let's Encrypt ACME account/cert/key state** | Treat as critical state and coordinate consistent snapshot; public HTTPS certs do not remove recovery/backup requirements |
| Caddy Cloudflare DNS-01 credential (future) | Zone-restricted Cloudflare token used for Let's Encrypt renewal of browser certificates | Include secure re-provision/backup procedure and expiry/revocation monitoring; never print it or reuse it for DNS management UI |
| secrets/telemetry | Separate CA, server cert and bearer token | Expiry/rotation with verified Windows exporter trust; no silent CA/key rotation |
| data/grafana and data/victoria-metrics | Persistent settings and metrics history | Differentiate essential configuration backups from large retained time series |
| SensorDash ProgramData on Windows | Local collector settings, approved PWM profiles, SQLite spool | **Not covered by Pi backups**; refer to Windows independent backup/recovery and supervised MSI servicing |

The present SHA-256 checksum demonstrates accidental integrity only; it does not encrypt, authenticate or confirm that a restore will work.

## Phase M0 — inspect recovery requirements before building a button

Inventory all persistent state: .env, pinned sources/Compose files, secrets, Pi-hole, wg-easy peers/keys, Grafana/VM, Authelia file + SQLite + encryption keys, **Caddy Let's Encrypt ACME accounts/leaf private keys, Cloudflare DNS-01 token custody/rotation instructions and the legacy Caddy internal CA**, telemetry certificates, PC control logs, /etc/homeaway-pcctl inventory, Linux pcctl SSH and host keys, agent units and host configuration. Record owning service, permissions, required consistency method, output size, encryption requirements and whether data is essential for cold restore.

Define recovery objectives for a single household Pi, including how much monitoring history is optional, how the admin re-enters after loss of all factors, and how DNS/VPN clients recover. Preserve known good configuration and keys. No automatic regeneration of peers, passwords or certificate authorities.

## Phase M1 — extend the read-only System Health screen

Use Phase 3's shared check IDs and evidence timestamps. Add service and disk health, **public Let's Encrypt certificate expiration and last Caddy DNS-01 renewal outcome for `homeawayfromhome.house`**, DNS zone/NS/DNSSEC/CAA/TXT readiness (without token disclosure), legacy internal CA fallback state, independent private telemetry certificate expiry, data retention, VictoriaMetrics freshness, Pi temperature/load, DNS probe, actual VPN handshake verification where supported, authenticator availability, restricted pcctl agent status, encrypted-backup freshness, version drift and health of optional stacks.

A TCP listener does not establish a WireGuard handshake; stale Windows telemetry does not prove that PC is off; a successful SSH power command means requested, not proven completed. Never put query logs, bearer strings, VPN endpoints or full credentials into displayed diagnostics.

## Phase M2 — backup engine, consistency and encrypted off-device custody

Before UI backup actions, refactor the offline backup path into a manifest-driven engine that handles **both** Compose files, optional services, relevant host-side files, and their real storage lifecycle. Decide per-volume whether full coordinated downtime or native SQLite/VM snapshots are required. If using an offline approach, explicitly warn that DNS/VPN will be interrupted, preserve the exact set of running services, and restore them in a trap even after exceptions; report any failed restart.

Allow only known backup sources and an allowlisted output location. Stage, fsync and verify archive contents/manifest before finalizing. Ensure strict root-only file permissions, bounded storage usage and safe handling of interrupted jobs; never delete the last known-good archive on failure.

Encrypt complete backups before distribution using an independently controlled, off-device recipient/key (age is an example); an unencrypted tar containing WireGuard keys, Authelia session encryption secrets, or pcctl SSH keys must **not** become an ordinary downloadable web artifact. Report off-device copy state and last **restore verified** date, not merely that a local tar exists. A backup validly listed/decrypted but never booted/restored is not a proven disaster recovery plan.

A restore must remain available as an offline operator script with explicit decryption and trust verification, safe path checks, staging, version compatibility checks, owner/mode preservation, controlled activation and rollback. Online restore of arbitrary uploaded archives from an HTTP endpoint is **out of scope** for initial release.

## Phase M3 — versions, update planning and explicit manual apply

Track git SHA, pinned image tags AND running digests (including **Caddy's custom Cloudflare DNS module version/arm64 build**), config/schema version, host agent revision and selected Compose profiles. Read available updates and release notes only from trusted sources; never imply a checked-in tag tells what is currently running.

Classify updates by impact: UI-only; Grafana/VM/ingest; **Caddy HTTPS/ACME plugin and Cloudflare token renewal**; auth/control; Pi-hole DNS; WireGuard; host agent. Caddy cert renewal is expected to be automatic and is **not a service-version update**. The updater must never restart DNS/VPN as a side effect of Grafana/dashboard changes. Pi ingest schema changes deploy before new SensorDash exporter code. Windows MSI/fan controller servicing stays on Windows.

Proposed operator flow: preview exact pinned change and dependency impact -> validate resource/disk/config and firewall exposure -> require verified recent encrypted backup -> approve outage explicitly -> apply only a **fixed allowlisted plan ID** through a separate host maintenance broker -> test service health -> report success or rollback needed. Jobs are persisted by ID with idempotency, bound resource/time, per-service result and redacted audit. Browser disconnect must not re-run the same upgrade.

No unattended update mechanism for DNS/VPN, generic shell endpoint, administrator-selected command string, writeable Docker socket on HTTP services, or silent service image tag switching.

## API, UI and security sketch

Indicative protected endpoints:
- GET /control/api/admin/health — shared structured checks with evidence and last observed time
- GET /control/api/admin/backups — redacted backup manifest, time, coverage, last verified off-device transfer and restore
- POST /control/api/admin/backups/preview — coverage, size and expected downtime with no side effects
- POST /control/api/admin/backups/start — explicit recent admin auth, reviewed profile, idempotency key and audit
- GET /control/api/admin/jobs/{id} — persistent redacted job progress for authorized admin
- GET /control/api/admin/updates/plan — pinned versions, expected changes, dependencies and rollback support
- POST /control/api/admin/updates/apply — only an approved signed/hashed fixed plan identifier and verified backup
- POST /control/api/admin/updates/rollback — controlled operation with independent offline recovery available

Every mutating call must pass verified Authelia admin role, independent API and broker authorization, origin/CSRF checks, strict schema, rate limits, strong audit and destructive confirmation. A custom root admin console is not acceptable. A broker should enumerate fixed operations and resources, without arbitrary Docker/systemd service or shell arguments.

## Phase M4 — validation and staged rollout

Automated tests with synthetic secrets for: optional control profile backup coverage; Authelia SQLite consistency; **Let's Encrypt ACME cache/key restore and failure to renew under revoked Cloudflare token, expired leaf, broken DNSSEC/CAA and DNS TXT propagation**; legacy Caddy internal CA vs separate telemetry CA; host inventory+SSH key retention; invalid permissions/paths/symlinks; insufficient disk, interrupted backup, restart-trap failures; wrong encryption recipient and archive tampering; restore traversal and schema mismatch; update permission denial, CSRF and forged gateway claims; duplicated jobs, timeouts and conflicting maintenance operations; fixed service list preventing unexpected port publishing. No real credentials or peer configs in test artifacts.

Operator hardware tests: recover from encrypted off-device backup on **spare/test media**; verify old accounts and peer identities; perform scoped, non-critical monitoring update before any DNS/VPN update; check rollback and authenticated sessions; verify iPhone VPN/private DNS and unchanged SensorDash approved fan profiles even if Pi services fail.

**Acceptance:** Health UI reports what it really verified, including browser Let's Encrypt certificate renewal and separate telemetry CA health; admins can produce an encrypted, independently held and restore-tested full-state backup including Caddy's ACME state and renewal token recovery; recovery works without the web UI; controlled updates show precise impact, require approval, and leave DNS/VPN and Windows fans undisturbed unless that particular operation was explicitly approved. CI tests alone do not count as real-world acceptance.

## Public HTTPS certificate operations (depends on Phase 3)

The [Phase 3 DNS-01 plan](PUBLIC-HTTPS.md) chooses Let's Encrypt/Caddy for browser-hostname certificates, using Cloudflare DNS while the domain remains registered at Namecheap. Phase 4 must monitor **automatic renewal** rather than write a second certbot/cron script. Cert expiry alarms should trigger with adequate lead time; report last successful renewal, certificate chain/hostname correctness, errors, Cloudflare DNS-01 availability, server uptime and whether the gateway still serves a valid cert after reboot. Do not "fix" a failed renewal by permitting new WAN HTTPS forwarding or turning off certificate verification.

A valid publicly trusted Caddy leaf must not be confused with the separate **private telemetry server certificate**; the latter has a distinct CA and rotation procedure, even if a later optional public-name telemetry migration is approved.

The backup/restore manifest should include persistent Caddy ACME account/cert/private-key state, its internal CA fallback state and protected Cloudflare token handoff/rotation instructions. If the DNS API token is excluded from exported encrypted backup by policy, that exclusion must be explicit with a tested way to provision a replacement token. Do not expose any of this through read-only dashboards or ordinary GET APIs.

## Files likely to change after permission to implement

scripts/backup.sh, scripts/update.sh, docs/OPERATIONS.md, docs/SECURITY.md, config manifest and backup helpers, Phase 3 structured health library, isolated narrow maintenance agent/socket, protected control/api admin UI, additional unit/integration tests and an accepted backup/update ADR. No changes in this planning branch affect runtime.