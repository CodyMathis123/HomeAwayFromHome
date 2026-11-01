# User accounts and access management — technical implementation plan

> **Phase 1 implementation branch.** Rebased onto the verified merged `Production` integration. The first change is a role-enforcement foundation; account provisioning remains gated on the independent Authelia user-store/session-lifecycle proof of concept. This feature branch is not deployed to the Pi, and does not alter existing Authelia users or live sessions. Read [phase overview](ADMIN-PHASE-OVERVIEW.md) first.

## 1. Existing behavior confirmed in the repository

- `scripts/init-control-auth.py` builds `data/control/authelia/configuration.yml` and `users_database.yml`, generates a one-time 28-character password with pinned `authelia/authelia:4.39.28` CLI and Argon2id, sets one `admins` group user, refuses to replace an existing directory, and prints the password once.
- Authelia's current file backend is `/config/users_database.yml`; access policy defaults to `deny` and explicitly allows **only** `group:admins` at `/control` after `two_factor`. All `/control` visitors are therefore currently admins; no separate `operators` or `viewers` can enter.
- Caddy's `control/gateway/Caddyfile` strips incoming identity headers, uses Authelia forward_auth and sends only the verified username via `X-HomeAway-User`. The control API checks username syntax but does not authorize roles/actions itself.
- Authelia's `notification.txt` filesystem notifier requires operator file access to retrieve registration/password-reset links; the current setup does not offer normal remote self-service delivery. `compose.control.yaml` mounts the entire Authelia directory writable to Authelia, not to the browser controller.
- The standalone read-only portal at `:8088` and shared Grafana link are separate **unauthenticated/URL-capability** views. Do not accidentally convert them into user-admin routes or imply their visibility is governed by HomeAway roles.

## 2. Recommended household permissions — server-enforced

| Capability | admins | operators | viewers | signed out |
| --- | --- | --- | --- | --- |
| View authenticated computer statuses | Yes | Yes | Yes | No |
| Read activity (redacted as appropriate) | Yes | Own/limited | Own/limited | No |
| Wake / sleep permitted PCs | Yes | Yes | No | No |
| Restart / shut down | Yes | No initially | No | No |
| Manage computers, registry, SSH onboarding | Yes | No | No | No |
| Create/disable users, change roles/password handoff | Yes | No | No | No |
| Register/manage own Authelia factors | Authelia's own authenticated settings | Same | Same | No |

This is a **proposed default**; per-computer operator grants and additional finely grained actions are phase two. All `/control` roles require a valid Authelia **two-factor** session. A logged-in account that isn't in any allowlisted role is denied. Preserve the separate read-only :8088 behavior.

Do not trust `X-HomeAway-User`, `Remote-Groups`, a role label rendered in HTML, a JS hiding mechanism, or a self-declared actor in requests. Caddy must clear every inbound identity header and copy verified **username and groups** from Authelia, with the API authorizing route/action against independently resolved roles. Confirm whether group changes/disablement are visible to existing sessions immediately; where not, the backend must additionally check the current account state for **each privileged action** or safely revoke/expire sessions. “Disabled” must never mean “can still send restart requests for eight hours”.

## 3. Identity backend decision: implementation spike before coding UI

### Option A — keep current Authelia file backend (recommended small-household prototype, conditional)

Authelia documents `authentication_backend.file.watch` for watching changes to the file; it is **off by default**. It also **writes the file itself** during password resets/changes. Our bootstrap writes a JSON object inside a `.yml` file (valid YAML), so treat extension/serialization rules carefully: Authelia writes files in the format implied by their extension. Do not assume two independent writers can safely serialize concurrently.

Prototype with the actual pinned 4.39.28 container:
1. Add `watch: true` to config **only in a separate test environment**. Create a secondary test account; validate successful reload after external atomic rewrite without Authelia restart.
2. Exercise Authelia self-service password change and admin creation simultaneously. Test for lost writes and mixed serialization, including process crash and restart. A lock used only by our writer does **not** coordinate Authelia's own writes: do not claim this resolves races.
3. Verify case normalization, duplicate usernames/emails, group reload, account disable, secret ownership and predictable session invalidation. Verify validated rollback from a coherent backup of users file + Authelia SQLite/secrets.
4. If safe concurrency and immediate privilege revocation cannot be shown, **do not ship** externally editable file accounts with self-service password changes. Prefer directory option B or a constrained staged administrative workflow with manual maintenance, explicitly disclosed.

If proven adequate, use a dedicated narrowly privileged **identity provisioner**, not the web controller or generic root shell. It alone can update `users_database.yml` via strict operations, preserve the last enabled admin, validate schema, use safe file ownership/permissions, backup/recovery and optimistic revision checks; never mount all Authelia encryption/config secrets writable. Argon2id hash generation must happen within a reviewed pinned dependency/CLI isolated from browser input. Do not expose hashes or generated passwords in GET responses/logs. Keep backups off the Pi and encrypted.

### Option B — a small LDAP directory, e.g. LLDAP (fallback for proper multi-user lifecycle)

Authelia supports LDAP as a first-factor backend. A dedicated lightweight user directory can own users/groups and password changes, reducing competing writers to Authelia's local file. Evaluate LLDAP compatibility with Authelia's exact pinned version, password reset, TOTP/session identities, migration of the existing admin account and backup/disaster recovery. This introduces one more always-on service and its database, secrets, ACLs and lifecycle; only choose it if the POC proves it materially safer/simpler than file administration. Never promise seamless migration of password hashes, 2FA devices or active sessions without testing.

**Decision deliverable:** one ADR recording chosen identity source of truth, account migration procedure, privilege/secret boundaries, concurrent update design, session revoke behavior and rollback. Keep account identifiers stable across changes.

## 4. First-run and recovery design

- **Keep one-time local trust bootstrap:** a Pi-side installer or console-paired, short-lived setup token can create the first admin. Never open an unprotected `/setup` endpoint on LAN/VPN with “first visitor wins”. CLI/script remains an offline break-glass path if UI/auth is unavailable.
- Admin account must exist before authenticated browser provisioning can be used. Never allow a user (including the last admin) to delete/disable/demote the last enabled administrator.
- New user: admin selects username, display name, email, role; backend generates strong unique temporary password (display once) or an Authelia-native recovery/enrollment flow after secure SMTP delivery. No unhashed password sent via regular email and no unrestricted invitation endpoints. Require password change and 2FA enrollment before any control access.
- Routine password changes, TOTP and passkey registration should remain in **Authelia's own supported user portal**, not duplicate MFA secret handling in HomeAway.
- Filesystem notifier is development-only for household usability; design the optional SMTP configuration flow (private credential handling, test send, failure state) or documented offline break-glass. A filesystem link appearing on the Pi is **not** an acceptable self-service experience.
- Account recovery: tested encrypted backup, local administrator break-glass with audited actions, lost all factors scenario, password-reset delivery failure and CA/trusted-domain outage. Admin UX must never display authenticator shared secrets or claim it can recover unexportable passkeys.

### Planned browser-domain migration (Phase 3 dependency, not Phase 1 scope)

The current unvalidated gateway uses private `*.home.arpa` hostnames and `tls internal`. **Do not change that during Phase 1** or make account management dependent on a public domain. The preferred follow-up after baseline acceptance is publicly trusted Let's Encrypt DNS-01 for `home.homeawayfromhome.house`, `auth.homeawayfromhome.house` and `metrics.homeawayfromhome.house` (registered domain expected to remain at Namecheap; Cloudflare DNS is proposed), with no inbound website port forwarding. Phase 3 owns the switch.

The migration must treat Authelia cookie domain, session/redirect URLs, first-factor recovery and notifier links, browser origin/CSRF policy, Caddy forwarded identity headers and Grafana embed origins as **one coherent hostname migration**. Test existing login accounts and 2FA enrollment after migration; keep an independently usable local recovery/rollback path. Public TLS only authenticates the server: Authelia username/password/TOTP or passkey remains necessary and distinct from SSH, WireGuard or ingest bearer tokens. A private CA fallback is retained for offline/recovery use; don't leak its private key. See the later [public HTTPS migration plan](https://github.com/CodyMathis123/HomeAwayFromHome/blob/feature/homeaway-setup-monitoring/docs/PLANS/PUBLIC-HTTPS.md).

The tested Caddy control gateway must keep its explicit `route` order (untrusted identity header cleanup **before** `forward_auth`), copy `Remote-User>X-HomeAway-User` on a successful Authelia response, and reject a missing verified identity. Do not silently restore default Caddy directive sorting or trust user-provided identity headers. The baseline is pinned to patched `caddy:2.11.7-alpine`.

## 5. API and UI design (indicative, not implemented)

Keep the existing authenticated control UX; add an **admin-only** Users area and a compact “My account” link into Authelia's own settings.

| Method / route (proposed) | Purpose | Required role | Behavior |
| --- | --- | --- | --- |
| `GET /control/api/me` | Current verified username/roles | any signed-in role | No secrets, explicit authorization capabilities |
| `GET /control/api/admin/users` | Redacted user inventory | admins | Never returns hashes, enrollment seeds or tokens |
| `POST /control/api/admin/users` | Create disabled/pending-enrollment user | admins | Validated inputs, one-time secret handoff or approved SMTP workflow |
| `PATCH /control/api/admin/users/{id}` | Display name, email, role, enable/disable | admins | Optimistic revision, no last-admin demotion |
| `POST /control/api/admin/users/{id}/reset` | Safe credential recovery trigger | admins | Explicit confirmation, one-time token flow, no raw private data |
| `GET /control/api/admin/audit` | Recent administrative events | admins | Timestamp, actor, action, outcome, no secrets |

Use strong origin/CSRF validation, small request bodies, JSON schema/field allowlists, URL path-ID normalization, no GET mutations, audit all denied privileged attempts, rate limits, and safe generic error messages. Add independent authorization in API and the privileged identity provisioning boundary (not solely the Caddy route). Consider deliberate step-up/recent-authentication for disable admins and credential recovery. Prefer server-rendered or plain JS controls consistent with current `control/api` UI; no SPA framework required.

Screens: Users list (name, role, enabled/enrollment status), Add User, Edit Permissions, Disable/Restore, Recovery, Security/Notification Status. UI must clearly distinguish login account state from the computer inventory and from Pi-hole/wg-easy admin logins.

## 6. Implementation files and tests

Likely changes: `control/gateway/Caddyfile` (verified groups), `scripts/init-control-auth.py` (new safe defaults/migration), `compose.control.yaml` (isolated identity provisioner and/or directory), `control/api/app.py` plus small `control/api/*` UI modules, a new identity provisioning module, `scripts/check_control_contracts.py`, `scripts/tests/test_control_auth.py`, new account-admin unit/integration cases, `docs/PC-CONTROL.md`, `docs/SECURITY.md`, and a new accepted ADR. Do not give `pc-control` or `portal` generic write access to Authelia secrets.

Test matrix (hardware-free first, then Pi auth/browser):
- Unauthenticated, forged identity/group, viewers/operators on admin endpoint, missing/old 2FA and cross-origin writes all denied **without any backend mutation**.
- Create disabled/pending account; duplicate case/email; invalid usernames/Unicode; insufficient password strength; exact revision races; simultaneous password reset and admin edit; partial file write/ENOSPC; interrupted process; restoration after reboot.
- Demotion/disable of current admin; **last enabled admin** protection; disabled or demoted user with an already issued cookie cannot execute forbidden actions; different-browser sessions.
- Authelia container's actual hash format, hot-reload and notifier workflows; watch active config and backup/restore under real volume permissions; no secret leakage from API/logs/CI/artifacts.
- Role matrix applied separately to UI availability, GET, POST, the action RPC and admin operations. Authelia gateway Caddy syntax/config and real browser + trusted CA tested.
- Password + TOTP launch path; WebAuthn/Face ID as a later separately enabled test (per 4.39 the passkey-only login is normally single-factor and the UV=two-factor option is experimental).
- Pi unavailable, Authelia unavailable, identity store corrupt or unreadable: **fail closed** for mutations without breaking Pi-hole/DNS/VPN or the existing offline Windows fan controller.

## 7. Acceptance and rollout

Acceptance: an admin can create a second household user and change their role/disable them from the HomeAway UI; an operator can perform only allowed power requests; a viewer can see status but no power buttons or backend power actions; an older session cannot bypass a revocation; users can manage their own factors via Authelia; the first admin and break-glass path are recoverable without destroying existing identities. All successful/denied mutations are audited with **no secrets**.

Roll out behind an off-by-default feature switch or separate optional Compose profile, preserve original users and Authelia SQLite/keys, test on cloned credentials with fake users before actual accounts, and validate rollback to pre-admin gateway configuration. Do not automatically rewrite the live user file, reset existing sessions or convert identity backends as part of ordinary `docker compose up`.

## References to verify at implementation time

- [Authelia filesystem backend/watch](https://www.authelia.com/configuration/first-factor/file/)
- [Authelia user/password file write semantics](https://www.authelia.com/reference/guides/passwords/)
- [Authelia ACL and group policies](https://www.authelia.com/configuration/security/access-control/)
- [Authelia WebAuthn and passkeys](https://www.authelia.com/configuration/second-factor/webauthn/)
- [Authelia filesystem vs SMTP notifier limitations](https://www.authelia.com/overview/authorization/statelessness/)
- [Authelia authentication backend options](https://www.authelia.com/configuration/first-factor/introduction/)
