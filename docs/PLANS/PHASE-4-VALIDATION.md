# Phase 4 — Test matrix and release gates

**Design checklist only. Nothing here has been run on the operator's Pi or Windows PC.**

## Required four-phase sequence

| Phase | Software gate | Hardware/operator gate |
| --- | --- | --- |
| Current baseline | Current full-stack PR chain CI passes | Pi/iPhone private HTTPS+Authelia, Grafana embed, SensorDash telemetry, WOL/SSH and PWM verified |
| 1 Accounts | Admin, operator and viewer server-side roles; disable/recovery and stale-session denial | Real Authelia sign-in, second-factor enrollment and local break-glass |
| 2 Computers | Durable atomic inventory/host-key transactions; no unsafe RPC or header spoof | Existing iOS Shortcut survives; new Windows SSH host key and supervised power actions |
| 3 Setup/Monitoring | Redacted health/drafts, Let's Encrypt DNS-01 Cloudflare token/Caddy module checks, separate telemetry CA and correct TLS/metrics probes | Publicly trusted iPhone Safari over VPN, Pi-hole split DNS, private-only service ports, real Windows-to-Pi sensor series |
| 4 Operations | Complete backup manifest, encryption/restore and reviewed update jobs | Restore from an off-Pi archive on spare host, supervised maintenance and real rollback |

## Backup restore coverage

The full-state manifest includes project .env, pinned Compose and source revision, secrets, Pi-hole, WireGuard, Grafana, VictoriaMetrics (if chosen history tier), **Authelia users plus SQLite and encryption state**, Caddy **Let's Encrypt ACME account/cert/private-key cache and legacy internal CA**, Cloudflare DNS-01 zone token recovery/rotation instructions, control audit, telemetry CA and token; **/etc/homeaway-pcctl/hosts.json plus /home/pcctl/.ssh and agent/units**. Store encryption recipient and recovery instructions separately, and ensure Windows SensorDash ProgramData/approved profiles have their own backup plan.

Check both core-only and Compose-control-overlay deployments. A running Authelia database must either be quiesced or consistently snapshotted. Test ENOSPC, power loss, cancelled backup, failed restart, incomplete archive, checksum/tar path tampering, expired or missing recovery credentials, and restore on a differently addressed replacement Pi without silently altering active keys.

## Update and diagnostics tests

Exercise status false positives (TCP reachability without VPN handshake; stale Windows data; absent filesystem thermal zone), **Caddy ACME renewal with revoked/wrong-scope Cloudflare API token, failed DNS-01 TXT propagation/DNSSEC/CAA, temporary offline operation, public cert expiry and stale legacy/internal CA**, service restarts with UI unavailable, admin session revocation, malicious requests, improper scope escalation, concurrent admins, duplicate POST/jobs, unpinned image changes, crash between apply/verification, incompatible DB migrations, and restore vs image-tag-only rollback. Verify server-side allowlist and that no new host ports, generic root UI, Docker socket mount or Windows fan commands appear.

## Delivery requirements

Before any actual management endpoints: review threat model and data owner map; implement read-only probes and backup manifests first; add narrowly scoped privileged writer only when needed. CI must pass at the current commit, then operator must explicitly approve actual Pi/Windows deployment in a maintenance window with a rollback plan. A green test suite cannot prove actual WOL, Windows sleep rights, Apple CA trust or fan safety. Record exact source commits, selected Compose profiles, host OS and observed results.