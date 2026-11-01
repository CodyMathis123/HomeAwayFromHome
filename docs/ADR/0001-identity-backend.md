# ADR 0001 — HomeAway identity ownership (provisional)

Status: **Development implementation validated in disposable CI; production migration NOT approved**  
Date: 2026-11-01

## Context

HomeAway currently uses one Authelia 4.39.28 filesystem user database and a
filesystem notification channel. Its existing `homeowner` account, password,
TOTP, SQLite storage and secrets are working and MUST survive Phase 1.

We need to add/edit/suspend household accounts from a browser without giving
the control API access to root, Docker, SSH keys or raw Authelia secrets.
Authelia's file provider can watch the database, but **Authelia itself writes
that same file** for password change/reset. A separate administration writer
cannot coordinate those writes using only its own locks, nor can an atomic
rename by itself prevent lost updates. The user database is currently JSON
inside a `.yml` file; Authelia rewrites it as YAML according to the suffix.

## Provisional decision

**Use LLDAP as the canonical future account and group directory; retain
Authelia as authentication/authorization and TOTP provider.** Do not add an
external writer to Authelia's live filesystem user database.

Pin the LLDAP version for each test/deployment. Use its documented GraphQL
administration API through a *dedicated minimally privileged account broker*,
NOT browser-controlled arbitrary GraphQL or a mounted Docker socket. Authelia
uses its first-party `authentication_backend.ldap.implementation: lldap`
integration with a restricted bind account.

The account broker must have tightly scoped fixed operations, authenticated
admin-only requests, strict origin/CSRF defenses, optimistic concurrency,
unique username/email validation, last-admin safeguards, transaction-aware
group role changes, audit, and no secret-bearing read endpoints. Its own
admin/service credentials must never enter the browser. Use short-lived
service tokens in memory only, rotate/recover deliberately. Do not assume
LLDAP GraphQL provides a native atomic multi-mutation transaction.

LLDAP's HTTP GraphQL API is **not** a public web administration endpoint.
Keep its listener accessible only to the account broker on a private Docker
network. Authelia's LDAP traffic stays on a private isolated network; use
TLS for any hop outside that protected trust boundary. Never expose LDAP
3890 or the directory web UI to the WAN.

## Verified since the provisional decision

The isolated CI job `scripts/integration-authelia-lldap.py` now exercises Authelia 4.39.28 with an actual LLDAP directory and two independent **password + TOTP** sessions. With `authentication_backend.refresh_interval: always`, revoking the membership used by a `two_factor` resource causes both old cookies to lose forward-auth access immediately in the synthetic test. The control API also independently checks current directory membership for **every request** and fails closed on mismatch/outage, so session refresh is not its only revocation boundary.

The separate LLDAP CI job tests directory mutations and the actual HomeAway account broker. The synthetic Authelia test also covers the `admins`, `operators` and `viewers` two-factor policies, including revocation of existing cookies for each role. An additional file-backend-to-LLDAP-to-file-backend lab now proves that **synthetic** TOTP enrollment and encrypted SQLite state can survive a backend migration and rollback when the username and secrets are preserved. The draft Users interface includes pending identities, group changes and **HomeAway-access** revocation, not full LDAP login disablement. No **real Pi account data restoration**, production credential migration, or invitation flow has been tested. The [supervised cutover runbook](../ACCOUNT-ADMIN-ROLLOUT.md) records the remaining gates.

## Critical unfinished security requirements

1. **Immediate suspend/revoke — synthetic cookie and role removal PASS; real migration still pending**. Authelia's LLDAP integration does not
   currently document a suitable standard account-disabled attribute.
   Removing a user's `admins`, `operators`, and `viewers` memberships must
   deny future actions, including with a previously issued 2FA session.
   Confirm both Authelia `refresh_interval` behavior and independent
   backend group/current-state checks. Fail closed when the authoritative
   account/revocation service is unavailable; don't cache permission grants
   through outages. A stale cookie must not suffice.
2. **Last enabled administrator**. Never remove/suspend/demote the last
   working administrator. Preserve local Pi-side break-glass recovery.
3. **Migration**. Preserve username identity exactly; verify Authelia's
   existing TOTP registrations remain associated with migrated accounts
   and restore correctly. Do NOT assume Argon2 hashes can be imported into
   LLDAP or that live sessions survive. Provide explicit password reset/
   reenrollment contingency and reversible backup/restore.
4. **Recovery**. Back up LLDAP data, Authelia SQLite and encryption secrets
   coherently and encrypted off-device. Restore and test in a separate
   environment before any production cutover. Do not use the live
   `homeowner` account for experiments.
5. **Notifications**. Filesystem notifications are temporary; configure
   secure SMTP and verify password-reset/TOTP setup delivery for actual
   self-service before exposing household account provisioning.
6. **Other apps**. Changing Authelia's first-factor backend must not silently
   repurpose unrelated Pi-hole, wg-easy, Windows SSH or SensorDash accounts.

## Implementation sequence and gates

- **A0a (this branch):** disposable LLDAP GraphQL role-mutation laboratory
  using synthetic test identities. No real account files or ports exposed.
- **A0b:** disposable *Authelia + LLDAP* login and 2FA experiment, including
  two existing-session browsers and role demotion/suspension.
- **A0c:** design/prove restricted broker and independent immediate
  revocation check, with fail-closed outages and last-admin protection.
- **A0d:** exercise migration, original TOTP/user preservation, encrypted
  backup and rollback using copies of private data in a controlled lab.
- **A1/A2:** only after gates pass, add provisioner UI and onboarding.
  Release behind an off-by-default feature switch, with supervised cutover.

**No production cutover, dynamic account changes, or alternate backend
deployment is authorized by this ADR.**

## References

- https://www.authelia.com/configuration/first-factor/file/
- https://www.authelia.com/reference/guides/passwords/
- https://www.authelia.com/configuration/first-factor/introduction/
- https://www.authelia.com/integration/ldap/lldap/
- https://github.com/lldap/lldap/blob/main/docs/scripting.md
- https://github.com/lldap/lldap/blob/main/schema.graphql
