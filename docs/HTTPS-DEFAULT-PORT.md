# Private HTTPS default port transition

**Scope:** In stage 1, add the normal HTTPS port **443** on the Pi's existing
`PI_LAN_IP` while retaining `8443`. Caddy keeps listening on container port
8443; Docker maps both `PI_LAN_IP:443` and `PI_LAN_IP:8443` to that listener.
No new certificates, public DNS changes, WAN forwarding, security exceptions,
or additional proxy containers are needed.

Stage 1 permits `https://home.homeawayfromhome.house` as a second strict
control API origin, while keeping the authenticated primary origin,
portal links, Grafana public URL, and Authelia's actual login redirection on
`:8443`. **Portless login redirects might still send the browser to 8443**.
Do not treat stage 1 as the final cutover.

## Apply stage 1 (Pi only)

Use normal Pi login; these commands do not touch your Windows fan controller.

```bash
cd ~/HomeAwayFromHome
git fetch origin
git switch Production
git merge --ff-only origin/Production
bash scripts/deploy-https443-preview.sh --plan
bash scripts/deploy-https443-preview.sh --apply
```

The targeted command auto-preserves the live `Caddyfile.account-recovery`
mount and root-protected Authelia SMTP overlay when enabled. It validates the
resolved private IP bindings, does not alter Authelia configuration/secrets,
and reconciles **only** the `gateway` and `pc-control` services.
There can be a short interruption to HTTPS while the gateway is reconciled.
The old `:8443` address remains published as the fallback.

Verify the new ports locally (this check validates the public certificate):

```bash
curl -I --max-time 10 --resolve home.homeawayfromhome.house:443:192.168.1.43 https://home.homeawayfromhome.house/
curl -I --max-time 10 --resolve auth.homeawayfromhome.house:443:192.168.1.43 https://auth.homeawayfromhome.house/
curl -I --max-time 10 --resolve metrics.homeawayfromhome.house:443:192.168.1.43 https://metrics.homeawayfromhome.house/
```

Also verify `https://home.homeawayfromhome.house:8443/control/` still
authenticates and that the controls API can authorize an innocuous action
from the new origin when signed in. Do not test shutdown/restart just to
prove a URL migration.

## Before final cutover

The real running Authelia configuration is in the root-protected
`data/control/authelia/configuration.yml`, not in Compose. Inspect only
the `session.cookies[].authelia_url` and
`session.cookies[].default_redirection_url` values, without printing
`session.secret`, LDAP credentials, SMTP credentials, or other secrets.
Authelia needs validated portless values for the final login redirect.

Stage 2 should then, as a separate, explicitly validated change:

1. Update the stored Authelia URLs from `:8443` to portless HTTPS with
   secure backup and validation; restart only Authelia.
2. Switch portal-generated links, Grafana `GF_SERVER_ROOT_URL`, account
   recovery `LLDAP_HTTP_URL`, and control primary CSRF origin to portless
   URLs; keep the old `:8443` origin as a safe alternate during migration.
3. Verify password + TOTP, users, PC power control, Grafana, invite/reset
   email links, LAN and WireGuard access, before optionally removing
   the legacy `:8443` binding.

Don't change router port forwarding or expose :443 to the WAN.

## Stage 2: use portless HTTPS URLs as canonical

After confirming all three hostnames answer on private port 443, stage 2
updates only the canonical URL **references**. The gateway still maps both
`PI_LAN_IP:443` and `PI_LAN_IP:8443` to its unchanged internal listener.
No Caddyfile edit or certificate issuance is necessary.

The script reads the existing *root-protected* Authelia config and requires
exactly the two known `:8443` session URLs. It prepares a candidate with
the same file ownership/mode, validates it inside the existing Authelia
container, backs up the complete original config root-only, and atomically
replaces it. It does **not** regenerate any secrets, TOTP registrations,
LDAP roles or SMTP settings. On unusual config layouts, it stops rather
than guessing. If the root-only backup exists, it will not overwrite it.

```bash
cd ~/HomeAwayFromHome
git fetch origin
git switch Production
git merge --ff-only origin/Production
bash scripts/deploy-https443-final.sh --plan
bash scripts/deploy-https443-final.sh --apply
```

The apply operation only reconciles `pc-control`, `portal`, `grafana`,
and (when account-recovery routing is live) `lldap`, then restarts
`authelia` to pick up the already validated redirect configuration.
There may be a brief authentication interruption. Caddy, VPN, Pi-hole,
VictoriaMetrics and Windows fan control are untouched.

Test an actual **fresh browser sign-in with TOTP**, not just loading the
portal, at the portless URL. Check admin and operator actions with a safe
Wake/status operation, Grafana navigation, and account-invite/reset URLs
if used. Confirm old `:8443` links still work. The app origin remains
strictly whitelisted for **both** explicit HTTPS origins.

**Rollback**, if login or control actions fail:

```bash
cd ~/HomeAwayFromHome
bash scripts/deploy-https443-final.sh --rollback
```

Rollback restores only the original root-protected Authelia configuration
and reapplies the former generated URLs from the explicit rollback Compose
overlay, then restarts the same select containers. It does not close
port 443 or manipulate DNS/VPN. Avoid running a normal `deploy-ui.sh`
after rollback until the cutover has been re-evaluated: its canonical
production Compose inputs would otherwise reapply portless settings.

Once verified across LAN and WireGuard, retire `:8443` separately, not
as part of this cutover.
