# Optional account email setup and password recovery

HomeAway's live account directory already supports users/groups and working
username + password + Face ID. This optional enhancement uses **LLDAP's built-in
password-reset emails**, rather than building a custom password or token system.

## Status / scope

**Prepared, not enabled on the Pi.** The recovery overlay does not run unless
explicitly appended to the existing Compose stack. No existing users or roles
are modified, and password resets alone do not grant HomeAway access. Existing
password + TOTP / security-key authentication remains unchanged.

The reset site is `https://home.homeawayfromhome.house:8443/directory/`.
It is served on the **existing private 8443 listener**, reachable only via
LAN/VPN and internal DNS. The full LLDAP native UI is exposed *to those LAN/VPN
clients* and relies on **LLDAP's own login** for administration; this is not a
public registration portal. It does not expose LDAP port 3890 or grant 2FA
bypass. The original LLDAP loopback/SSH-tunnel UI remains available.

## Before enablement: one-time operator setup

Obtain an SMTP account and verified sender address at your preferred provider
(for example Brevo or Resend). Domain verification and SMTP credentials must
be completed at the provider; no API keys belong in Git or chat.

On the Pi, from the repository root, create a private env file:

```bash
sudo install -d -m 0700 secrets/control
sudo install -m 0600 /dev/null secrets/control/lldap-smtp.env
sudo nano secrets/control/lldap-smtp.env
```

For Resend, the verified sending domain is `homeawayfromhome.house`.
The SMTP settings are:

```dotenv
LLDAP_SMTP_OPTIONS__SERVER=smtp.resend.com
LLDAP_SMTP_OPTIONS__PORT=465
LLDAP_SMTP_OPTIONS__SMTP_ENCRYPTION=TLS
LLDAP_SMTP_OPTIONS__USER=resend
LLDAP_SMTP_OPTIONS__PASSWORD=<your private Resend sending API key>
LLDAP_SMTP_OPTIONS__FROM=HomeAway <accounts@homeawayfromhome.house>
```

These are **illustrative values**. The operator has already created the
private file on the Pi with the real key; do not re-create, print, or paste it.

Docker Compose's `env_file.format: raw` preserves literal password
characters; requires Compose v2.30.0+ (check `docker compose version`). LLDAP
does **not** provide a documented `LLDAP_SMTP_OPTIONS__PASSWORD_FILE` feature
in its pinned version, so the password ends up in the LLDAP container
environment and can be read by Docker administrators. The env file is
root-readable only, but treat access to Docker as privileged and do not run
`docker inspect` or `docker compose config` into public logs. The encrypted
HomeAway backups include this secrets directory.

### Validate without service changes

```bash
files=(-f compose.yaml -f compose.control.yaml -f compose.accounts.yaml
       -f compose.computers.yaml -f compose.production.yaml
       -f compose.account-recovery.yaml)
sudo docker compose "${files[@]}" --profile control --profile accounts --profile metrics config -q
```

Double-check that the existing home hostname resolves to your Pi on LAN and
that the second user can access this same URL over LAN/VPN. Do not enable
public NAT/port-forwarding, and don't change the functioning Pi-hole/VPN.

### Enable in a reviewed maintenance window

```bash
sudo docker compose "${files[@]}" --profile control --profile accounts --profile metrics \
  up -d --no-deps --force-recreate lldap gateway
```

Only `lldap` and `gateway` are recreated; Caddy will briefly interrupt HTTPS
login/portal during its restart. Pi-hole, WireGuard and other services remain
running. This is an **opt-in change**; do not run it unless prepared to test
account setup and the existing HomeAway login in the same session.

Then open the directory UI, choose **Forgot password**, and enter the email of
an existing pending/test user. Use the resulting email's link (from LAN/VPN) to
set the user's password. Confirm the user has **no HomeAway access** until an
admin explicitly assigns an allowed HomeAway role. Confirm password + Face ID
still works for an existing account. Avoid attempting a live SMTP test with
production admin identities first.

**Factor recovery:** resetting an LDAP password does **not** reset an Authelia
TOTP/WebAuthn enrollment. Use your existing backup/fallback factors for now;
safe factor replacement remains a separate, operator-assisted acceptance test.

## Roll back the optional overlay

```bash
base=(-f compose.yaml -f compose.control.yaml -f compose.accounts.yaml
      -f compose.computers.yaml -f compose.production.yaml)
sudo docker compose "${base[@]}" --profile control --profile accounts --profile metrics \
  up -d --no-deps --force-recreate lldap gateway
```

This returns LLDAP UI to SSH-loopback access only and restores the original
gateway routes. It does not remove accounts, passwords, roles or the SMTP file.

## Known acceptance limits

- Provider sending, HTML/path prefix handling, and real reset-token redemption
  are **not validated** on the actual Pi until a sender and a second test
  account exist.
- No HomeAway-admin "Send invite" API yet; administrators create a pending
  user, then the user requests a password reset email through LLDAP.
- Password reset does not establish identity ownership; provision accounts
  only with trusted email addresses.
- Full account/factor recovery, including lost TOTP + passkey, is not enabled.
