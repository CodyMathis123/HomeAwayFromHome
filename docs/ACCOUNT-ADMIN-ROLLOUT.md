# Phase 1 Account Management — implementation and cutover gate

**Status: developed on PR #5; not deployed to HomeAway production.**

## Greenfield developer walkthrough (operator-run, not deployed automatically)

The early full-system restore/SMTP checklist below documents a stricter hardening
path, but it is **not a blocker to this greenfield, supervised dev rollout**.
The operator already made a consistent local backup; checksum and restart
of all 11 HomeAway containers were verified on November 1, 2026.
Use a direct LAN SSH session so an auth restart cannot cut off the operator.
The commands below do **not restart Pi-hole or WireGuard**.

**1. Check out the review branch and initialize new secrets (no service changes):**

```bash
cd ~/HomeAwayFromHome
git fetch origin feature/homeaway-account-management
git switch -c test-pr5 --track origin/feature/homeaway-account-management
sudo python3 scripts/init-accounts.py
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml \
  --profile control --profile accounts --profile metrics config -q
```

**Pi first-run note:** The standard Debian LLDAP container was reported unhealthy on a hardened Pi deployment (all capabilities dropped). The Compose overlay now uses the official `2026-09-22-debian-rootless` image, which runs as UID/GID 1000 and does not need root-to-user switching. After fetching the corrected branch, recreate **only** `lldap` and leave Authelia/Pi-hole/WireGuard alone. If unhealthy again, inspect `docker inspect ... --format '{{json .State.Health}}'` and review redacted LLDAP logs before changing file permissions or secrets.

**Docker networking note:** A container connected *only* to Docker `internal: true` networks has no default gateway and may silently lack the requested localhost published port. The account overlay therefore attaches **only LLDAP** to a second, otherwise unused `account-ui` bridge. LLDAP remains on `account-directory` for Authelia/broker communication; the UI is still bound to `127.0.0.1:17170` on the Pi. This dedicated bridge grants LLDAP outbound connectivity, but does not expose its UI to the LAN or connect it to DNS/VPN/metrics. After updating from an earlier PR #5 revision, recreate **only** `lldap` and verify that `docker port homeawayfromhome-lldap-1` prints the localhost binding.

**2. Start LLDAP and broker only; keep real Authelia file backend running:**

```bash
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml \
  --profile control --profile accounts --profile metrics up -d --build lldap account-broker
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml \
  --profile control --profile accounts --profile metrics exec -T account-broker \
  python3 -B /app/bootstrap.py
```

A loopback-only LLDAP web interface is available on the Pi at
127.0.0.1:17170 (not exposed to LAN/WAN). On your **own computer**, open
an SSH tunnel with `ssh -N -L 17170:127.0.0.1:17170 codymathis@PI_LAN_IP`
(replace PI_LAN_IP), then browse `http://127.0.0.1:17170`.
Sign in as LLDAP `admin` using the password stored in
`secrets/control/lldap_admin_password` (read it **on the Pi** with
`sudo cat secrets/control/lldap_admin_password`; never paste it into chat).
Create a matching `homeowner` user with a **new** password; add that
user to the `admins` group. Use the identical username to retain
the existing Authelia SQLite TOTP enrollment, assuming the existing Authelia
user is named `homeowner`. Existing password hashes cannot be imported.

Creating new users through the HomeAway Users UI is supported, but in this dev
version they start as *pending*. Use LLDAP's SSH-tunneled UI to provision
their initial passwords; then assign a HomeAway role from the Users UI.
No SMTP/invitation setup is required for testing.

**3. After confirming the LLDAP account and groups, switch only authentication:**

```bash
cd ~/HomeAwayFromHome
sudo python3 scripts/switch-control-auth.py --apply
# Make the optional account management endpoints visible to admins:
if grep -q '^HOMEAWAY_ACCOUNT_ADMIN_ENABLED=' .env; then
  sed -i 's/^HOMEAWAY_ACCOUNT_ADMIN_ENABLED=.*/HOMEAWAY_ACCOUNT_ADMIN_ENABLED=true/' .env
else
  printf '\nHOMEAWAY_ACCOUNT_ADMIN_ENABLED=true\n' >> .env
fi
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml \
  --profile control --profile accounts --profile metrics up -d --build \
  --no-deps --force-recreate authelia pc-control
```

Authelia validates the candidate first, retains its existing
encryption/session secrets and SQLite/TOTP records, and snapshots the original
file backend config. Verify `authelia` becomes healthy and test a fresh
HomeAway login with the **new LLDAP password** and existing TOTP, or re-enroll
the factor if necessary. Verify Users UI, role changes, and an existing
viewer/operator session after revocation. Pi-hole and WireGuard are unaffected.

**Simple rollback (if login fails):**

```bash
sudo python3 scripts/switch-control-auth.py --rollback
sed -i 's/^HOMEAWAY_ACCOUNT_ADMIN_ENABLED=.*/HOMEAWAY_ACCOUNT_ADMIN_ENABLED=false/' .env
sudo docker compose -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml \
  --profile control --profile accounts --profile metrics up -d --no-deps \
  --force-recreate authelia pc-control
```

The original file-backed password and existing TOTP data should work again.
Do not delete the original backup, rotate Authelia encryption keys, expose LDAP
port 3890, or publish LLDAP 17170 to the LAN/WAN.

## What is implemented

- The existing secured HomeAway control UI now includes an **admin-only Users** section when the server's account-admin feature switch is explicitly enabled.
- The isolated `account-broker` talks to LLDAP's fixed GraphQL API. `pc-control` never receives the directory administrator password; it receives only a narrow broker token. The broker has no host ports. LLDAP's optional admin web UI binds **only to 127.0.0.1:17170**, for an SSH tunnel; LDAP TCP 3890 is never host published.
- Roles: `admins` (all power + audit + account operations), `operators` (wake/sleep/status), `viewers` (status only). A missing or unrecognized group denies access.
- On **every protected request**, Caddy/Authelia verifies the login and two-factor policy and forwards a trusted username. The control API independently resolves that user's **current** HomeAway role from LLDAP, rather than requiring a duplicate group header. No directory role, a missing verified username, or a broker outage **fails closed**; stale group claims never grant extra permissions, including for previously authenticated sessions.
- Account creation makes a **pending identity with no HomeAway role and no password issued**. This is not a usable invitation; credentials must be enrolled by a separately reviewed flow. Explicit account role/revoke operations use an expected revision, serialized broker writes, restricted group operations and minimal audit metadata. A revoked account retains its LDAP password but loses HomeAway access; UI must not claim total LDAP-login disablement.
- Never update the existing `homeowner` Authelia files or expose Docker, SSH, root or a general GraphQL endpoint to the browser.

## Verified in disposable CI

1. LLDAP user/group creation, duplicate username rejection, membership grants/removals.
2. Real Authelia 4.39.28 configured with the pinned LLDAP Docker image and `authentication_backend.refresh_interval: always`, password authentication plus **real TOTP**, independently for two separate existing browser-equivalent cookie sessions.
3. Removing a role from LLDAP causes both **already authenticated** sessions to lose Authelia forward-auth authorization without logout. This proves the safe role-removal behavior for the tested configuration.
4. Independent controller endpoint tests: CSRF, deny-by-default, role restrictions, fresh directory role overriding stale Authelia group claims, group-header-independent directory lookup, offline broker and unauthenticated requests.
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
- For a stricter rollout, initialize `secrets/control/*` only once rollback requirements are satisfied. The new dev setup scripts bootstrap those secrets, role groups and the explicit Authelia configuration switch separately; there is still **no automatic deployment**.
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
