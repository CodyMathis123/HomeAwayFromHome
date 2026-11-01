# Shared administration contracts for phases 1–4

> Architectural guardrails to prevent each feature from inventing its own privileged helper, identity scheme, settings serializer or diagnostics model. **Plan only; none of these contracts are implemented.**

## Source-of-truth matrix

| Domain | Authoritative owner / existing location | How UI accesses it | Mutations allowed? |
| --- | --- | --- | --- |
| Sign-in, factors, groups | Authelia (currently `data/control/authelia` files + SQLite) | Authelia native factor UI; verified gateway claims | Phase 1 narrow identity writer after backend decision |
| Household role permissions | Server policy derived from verified Authelia identity/groups | Shared `/control/api/me` capability matrix | Only phase 1 role admin |
| Computers and active SSH/WOL target | `/etc/homeaway-pcctl/hosts.json` | Redacted API and narrow inventory writer | Phase 2 admin, not generic setup API |
| PC power | Pi non-root `pcctl` Unix socket + Windows forced-command SSH | Existing `/control/api/actions` with server role checks | Only allowlisted status/wake/sleep/restart/shutdown |
| Core network config | `.env`, Pi-hole Local DNS, wg-easy native peer store | Setup check/read and operator guide | Phase 3 limited approved changes; router/manual steps external |
| Browser HTTPS | Current: Caddy internal CA on `.home.arpa`; Phase 3 preferred: Caddy Let's Encrypt DNS-01 on `homeawayfromhome.house` | Redacted issuer, trust, SAN, expiry and renewal checks; no private-key export | Phase 3 reviewed hostname/certificate cutover, not generic CA key access |
| Public DNS / ACME | Namecheap registration (retained), Cloudflare authoritative zone (proposed), Pi-hole split DNS | NS/DNSSEC/TXT propagation checks; zone-scoped token never sent to browser | Manual nameserver change and Caddy's DNS-01 TXT only; no unrestricted DNS editor |
| Windows ingest HTTPS | Dedicated `secrets/telemetry` (different CA) | Redacted validity/fingerprint; public certificate handoff | Phase 3 approved issue/rotate via dedicated broker, never browser key read |
| SensorDash hardware | Windows SensorDash `%ProgramData%` and collector | Read-only exported telemetry through Grafana | Not mutated by HomeAway |
| Grafana dashboards/history | Provisioned JSON, VictoriaMetrics data volume, Grafana native UI | Read-only iframe/links/readiness | Use Grafana's authenticated features, no duplicate dashboard editor |
| Operations & updates | Existing `scripts/backup.sh`, `scripts/update.sh`, Compose, systemd | Phase 4 read-only health / narrowly scoped actions | Only separate maintenance broker under explicit approval |

## Shared role and security contract

`admins` may administer users, computers and setup; `operators` can view status and use only allowlisted Wake/Sleep on permitted active computers; `viewers` can read authenticated status only. The exact role design is finalized in phase 1 and reused unchanged in phases 2–4. On every privileged request, gateway checks verified user/groups, clears spoofable incoming headers, API validates role + action and narrowly privileged backend revalidates the operation scope. Session revocation/disable must stop existing cookies from exercising stale privilege.

Keep the explicit ordered Caddy `route`: remove any client-supplied identity headers **before** Authelia `forward_auth`, copy the successful `Remote-User` to `X-HomeAway-User`, and fail closed on absent identity. CI must validate the adapted Caddy JSON route order. Keep `/control` private-HTTPS-only behind two-factor Authelia, `:8088` read-only, Grafana admin independent, and all admin helper sockets unexposed to LAN. A publicly trusted certificate must **not** change LAN/VPN-only exposure or replace user authorization. All privileged requests are bounded JSON POST/PATCH, same-origin/CSRF validated, role checked, strongly audited and rate-limited; no GET mutation. No arbitrary command, `sudo`, general Docker/socket control, SSH key mount, OS-wide read/write API, or blanket filesystem write permission.

## Proposed diagnostic check schema

```json
{
  "id": "metrics.ingest.tls.valid",
  "state": "healthy",
  "severity": "info",
  "summary": "Ingest TLS certificate valid",
  "evidence": "local_server_cert_verified",
  "checkedAt": "ISO8601",
  "expiresAt": "ISO8601-or-null",
  "nextAction": null,
  "runbook": "docs/TELEMETRY.md"
}
```

**Conventions:** `healthy`, `warning`, `failed`, `unknown`, `not_configured`, `not_applicable`. A failed probe does **not** prove why the external service failed; evidence describes what was actually checked. Use stable IDs and a typed schema with version to allow phase 4 to reuse all phase 3 readiness probes. Include duration, cache age and timeout budget for slow probes. No bearer tokens, complete HTTP URLs containing shared credentials, email/reset links, private IPs where unnecessary, or unredacted log contents.

## Mutation transaction contract

Any optional write broker should support `preview` → `validate` → `apply` → `verify` → `rollback` for each *explicit allowlisted operation*, with:
- Separate subject, action and resource authorization independent of Caddy and browser rendering.
- Redacted before/after diffs, immutable audit ID, operator acknowledgement of service effects.
- Resource version/ETag, shared locking with CLI writer, fsync and atomic replace when files are involved.
- No unattended regeneration of existing secrets/CA/peer keys.
- Exact allowed output paths; reject symlinks, arbitrary filenames or shell arguments.
- Safe no-op on retry and explicit idempotency for uncertain response.
- Maintenance mode gate for changes that could interrupt Pi DNS/VPN, Authelia, Grafana or Windows onboarding.
- Rollback and service-health verification before declaring success, not merely process exit code zero.

**Avoid a universal host-privileged server.** If there are multiple privileged domains, prefer separate, small agents or approved fixed workflows with Linux ACLs; common client/UI conventions are fine. A low-privilege API frontend must never obtain the Docker socket or broad filesystem access.

## Test ownership and delivery

- Phase 1 tests identity, user disable/session revocation, role claims and final-admin recovery.
- Phase 2 tests inventory concurrency, stable host identity, Windows SSH trust and power-command isolation.
- Phase 3 tests setup checks, Let's Encrypt DNS-01 issuance/renewal, zone-scoped Cloudflare token, public/browser TLS versus private telemetry CA, config drafts, Pi/WireGuard split DNS/telemetry readiness and no WAN web ports.
- Phase 4 tests service diagnostics, consistent backup coverage, restore verification, pinned updates and rollback.
- Shared CI checks Compose resolved security invariants, actual Caddy/Authelia syntax, Python/JS unit tests, docs links, and fake/isolated backend integration. Hardware-dependent Pi/iOS/Windows tests are separate and supervised.

**Deployment sequence:** baseline validated → accounts/RBAC → computer management → setup wizard / monitoring → operations / maintenance. Each implementation branch may be rebased onto an accepted/merged predecessor with a fresh CI run. No automation of real devices or production-side writes is authorized merely by a planning commit.
