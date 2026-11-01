# Phase 1 Account Management — implementation and cutover gate

**Status: developed on PR #5; not deployed to HomeAway production.**

## What is implemented

- The existing secured HomeAway control UI now includes an **admin-only Users** section when the server's account-admin feature switch is explicitly enabled.
- The isolated `account-broker` talks to LLDAP's fixed GraphQL API. `pc-control` never receives the directory administrator password; it receives only a narrow broker token. The broker has no host ports.
- Roles: `admins` (all power + audit + account operations), `operators` (wake/sleep/status), `viewers` (status only). A missing or unrecognized group denies access.
- On **every protected request**, the control API compares Caddy/Authelia's verified role with a fresh role from LLDAP. Mismatch, disabled role, unreachable broker, missing verified header, or invalid identity **fail closed**, including previously authenticated sessions.
- Account creation makes a **pending identity with no HomeAway role and no password issued**. This is not a usable invitation; credentials must be enrolled by a separately reviewed flow. Explicit account role/revoke operations use an expected revision, serialized broker writes, restricted group operations and minimal audit metadata. A revoked account retains its LDAP password but loses HomeAway access; UI must not claim total LDAP-login disablement.
- Never update the existing `homeowner` Authelia files or expose Docker, SSH, root or a general GraphQL endpoint to the browser.

## Verified in disposable CI

1. LLDAP user/group creation, duplicate username rejection, membership grants/removals.
2. Real Authelia 4.39.28 configured with the pinned LLDAP Docker image and `authentication_backend.refresh_interval: always`, password authentication plus **real TOTP**, independently for two separate existing browser-equivalent cookie sessions.
3. Removing a role from LLDAP causes both **already authenticated** sessions to lose Authelia forward-auth authorization without logout. This proves the safe role-removal behavior for the tested configuration.
4. Independent controller endpoint tests: CSRF, deny-by-default, user/admin role restrictions, stale role versus current directory, offline broker and unauthenticated requests.
5. Broker tests: last-admin/current-admin protection, revision mismatch, invalid arguments, unavailable directory, minimal audit fields.
6. The optional Compose overlay is opt-in, has no public or LAN-facing ports for LLDAP or broker, and does not mount the existing Authelia secrets into the broker.
7. An **entirely synthetic** file-backed `admin` account authenticates with its original password and a real encrypted TOTP enrollment. Switching the first-factor backend to LLDAP while retaining the same username, Authelia SQLite volume, session/encryption secrets and TOTP record permits a fresh login with the **new LLDAP password and original TOTP**. Switching back to the original file backend also permits login with the original file password and TOTP. This proves the test migration/rollback mechanism, **not** compatibility with the Pi's actual encrypted storage snapshot.

## What must be proven before real cutover

The synthetic tests **do not** migrate a working existing file-backend user and TOTP into LLDAP. LLDAP does not accept the Authelia Argon2 password hash as a drop-in plaintext password. A real migration must preserve the existing **username** and Authelia **storage encryption key + SQLite data**. Synthetic TOTP continuity and rollback are now validated in disposable CI, but **a restored snapshot of the real Pi state has not been tested**. No assumption of password portability or session persistence is allowed.

Mandatory human-supervised release checklist:

- Confirm that the opt-in Compose overlay gives **Authelia and the account broker access to the private `account-directory` network** while `lldap` itself has no host-published ports, and `pc-control` cannot directly reach LDAP. The resolved-Compose CI assertion guards this. Do not attempt a live backend switch merely because the overlay validates.
- Build and validate the **LLDAP-backed Authelia authorization policy** in an isolated copy. The original `init-control-auth.py` file-backend configuration only admits `admins` to `/control`. With the new account feature, Authelia must admit `admins`, `operators`, and `viewers` at **two-factor** level; the authenticated `pc-control` API then enforces the distinct command permissions on every request. Preserve `default_policy: deny` and scope the rule to `/control` only. Do not weaken the entire site's access policy or modify the existing Pi file-backend config. The synthetic CI lab now tests all three group grants and existing-session role revocations.
- Make and verify an **off-device encrypted backup** of the Pi's existing Authelia `configuration.yml`, `users_database.yml`, SQLite data, notifier and encryption/session secrets, plus Caddy CA files; test restore in an isolated environment. Protect backup encryption key independently.
- Establish a new LLDAP administrator and protected directory secrets, create groups, and provision the matching `homeowner` username with a secure **new password**. Independently test that LLDAP administrator credentials work. Do not remove the existing file backend or original working user.
- Restore **only a test copy** of existing Authelia encrypted state into an isolated Authelia+LLDAP environment. Verify existing TOTP and passkey device associations and test real password + TOTP; if any 2FA record is unusable, stop and restore file-backend operation.
- Configure SMTP or a documented supported credential-invitation mechanism, with a test message and safe failure handling. Pending users cannot be onboarded from the current UI until this is reviewed.
- Verify fresh Authelia session, previously authenticated session after role downgrade/revoke, and reauthorization after role upgrade using two browsers; prove admin access stays recoverable and the iOS Shortcut continues to function. Do not conflate computer power permissions with Pi-hole/wg-easy administrative credentials.
- Fail the cutover if old sessions, LDAP outages, last-admin enforcement, backup integrity or TOTP association are unsafe. Roll back the **Authelia backend config and its matching encrypted storage** as a coherent set, without rotating encryption keys.
- Only after all tests pass, initialize `secrets/control/*` with unique random secrets, adjust permissions, and opt into `compose.accounts.yaml`. The example overlay is **not a one-click install**: it does not perform an Authelia migration and deliberately leaves the existing Authelia file authentication configuration unchanged.
- Validate the real deployment as the final gate before merging the feature branch into `Production`.

## Explicitly not supported yet

Automatic password invitations, SMTP configuration, factor restoration, password resets, LDAP-login disablement for unrelated applications, and concurrent edits by external LLDAP administrators are **not supported by the current HomeAway UI**. GraphQL role updates are not transactional or compare-and-swap across all external writers. Avoid multiple broker instances or other management writers during mutations; do not claim the optimistic revision is a distributed transaction.

## Files and tests

- `control/accounts/broker.py`, `control/accounts/Dockerfile`
- `control/api/app.py`, `control/api/accounts.js`, `control/api/index.html`
- `compose.accounts.yaml` (disabled by default)
- `scripts/integration-identity-lab.py`
- `scripts/integration-authelia-lldap.py`
- `scripts/tests/test_accounts.py`, `scripts/tests/test_accounts_http.py`
- `scripts/check_accounts_contracts.py`
- `docs/ADR/0001-identity-backend.md`

Do not switch the Pi checkout from its verified `Production` installation just to try this branch.

## Development validation cadence

The heavyweight Compose, Grafana, VictoriaMetrics and identity-lab CI runs are
for **code, configuration, test, and workflow changes**. Markdown-only edits
are documentation updates and must not start or require the full workflow.
Branch push filters now compare the newly pushed files; existing PRs no
longer rerun CI for each documentation-only synchronization. Run the workflow
manually if documentation changes materially affect installation commands.
