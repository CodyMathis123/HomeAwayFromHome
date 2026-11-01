# Face ID sign-in (iPhone)

Authelia 4.39 supports WebAuthn passkeys. No new authentication service is
needed. The optional `compose.passkeys.yaml` overlay enables passkey login
without changing the existing access control rules or disabling TOTP.

### Enable

On the Pi, with this branch checked out:

```bash
cd ~/HomeAwayFromHome
sudo docker compose \
  -f compose.yaml -f compose.control.yaml \
  -f compose.accounts.yaml -f compose.computers.yaml \
  -f compose.production.yaml -f compose.passkeys.yaml \
  --profile control --profile accounts --profile metrics config -q

sudo docker compose \
  -f compose.yaml -f compose.control.yaml \
  -f compose.accounts.yaml -f compose.computers.yaml \
  -f compose.production.yaml -f compose.passkeys.yaml \
  --profile control --profile accounts --profile metrics \
  up -d --no-deps --force-recreate authelia
```

Then open `https://auth.homeawayfromhome.house:8443` on the iPhone.
Sign in and register a **Security Key / Passkey** in Authelia's account
settings using the iPhone Face ID prompt. Keep the existing TOTP method.

Authelia may request confirmation through its configured notifier during
registration. If its notifier writes to a local file rather than sending
email, the confirmation will not arrive in the user's inbox. This is
unrelated to Face ID support; inspect the notifier locally if prompted.

**Login policy:** For the current two-factor resources, a passkey can be a
second factor after password authentication. Passwordless passkey login
still requires an additional factor under Authelia's normal two-factor
policy. We intentionally do **not** enable the unsupported experimental
setting that treats one passkey verification as two factors.

### Disable

Recreate **only** Authelia using the original five production Compose
files, leaving out `compose.passkeys.yaml`. This reverts the opt-in setting;
it doesn't delete registered authenticators, TOTP, or directory accounts.

This does not change Caddy, port mappings, Pi-hole, VPN, Grafana, or PC
power-control behavior.
