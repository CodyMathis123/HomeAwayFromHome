# Authelia notification migration: filesystem -> Resend

The active Pi authenticates through Authelia, with an LLDAP directory and
the private Caddy gateway. This **opt-in** change replaces only Authelia's
notification provider. It does not migrate passwords, TOTP, WebAuthn/passkeys,
sessions, LDAP, roles, or user accounts.

Authelia permits exactly one notifier provider. The old `filesystem:` entry
**must be removed** before enabling SMTP; simply supplying SMTP environment
variables alongside `notifier.filesystem` is not valid.

## Operator steps

Execute on the Pi in `~/HomeAwayFromHome`. Keep an existing administrator
session and a way to SSH into the Pi while applying this change.

1. Pull reviewed Production and run the read-only preflight:

```bash
cd ~/HomeAwayFromHome
git fetch origin && git switch Production && git merge --ff-only origin/Production
sudo python3 scripts/prepare-authelia-smtp.py --plan
```

**STOP if the preflight fails.** The script accepts only a single simple
filesystem notifier in the actual `data/control/authelia/configuration.yml`.
Do not change a complex or already-SMTP configuration automatically.

2. Stage the local protected files without restarting services:

```bash
sudo python3 scripts/prepare-authelia-smtp.py --apply
```

This backs up the **entire original config** to
`secrets/control/authelia-notifier-original.yml` (root-only), removes just
the filesystem notifier stanza, and writes
`secrets/control/authelia_smtp_password` (root:nogroup, mode 0640)
using the already-tested Resend key from the private LLDAP env file.
Neither key nor configuration content is printed. No SMTP key is committed.
The notifier replacement leaves the original `notification.txt` file in
place as historical data; do **not** delete it during rollout.

3. Validate configuration while the working Authelia process is unchanged:

```bash
files=(
  -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml
  -f compose.computers.yaml -f compose.production.yaml
  -f compose.account-recovery.yaml -f compose.authelia-smtp.yaml
)
profiles=(--profile control --profile accounts --profile metrics)

sudo docker compose "${files[@]}" "${profiles[@]}" config -q

sudo docker compose "${files[@]}" "${profiles[@]}" \
  run --rm --no-deps --entrypoint authelia authelia \
  config validate --config /config/configuration.yml
```

Validation is the gate: **do not restart** Authelia if it fails. Correct
the reported schema or secrets/permission problem, or roll back as below.

4. Recreate **only Authelia** (no gateway/DNS/VPN restart):

```bash
sudo docker compose "${files[@]}" "${profiles[@]}" \
  up -d --no-deps --force-recreate authelia

sudo docker compose "${files[@]}" "${profiles[@]}" ps authelia
```

Check that Authelia stays `Up (healthy)` and a new HomeAway login still
works with password + existing second factor. Do not change stored
credentials or re-enroll factors. The additional non-internal network is
dedicated to Authelia SMTP outbound access, and carries no published host
ports; the existing account-directory and pc-control bridges remain.

5. Verify the deployed **Authelia 4.39.28** notifier without making changes:

```bash
sudo docker compose "${files[@]}" "${profiles[@]}" ps authelia
sudo docker logs --since 10m homeawayfromhome-authelia-1 2>&1 | \
  grep -Ei 'smtp|notifier|notification|error|warn' | tail -40
```

A healthy container and an SMTP startup check without errors provide
evidence of connectivity and SMTP authentication, **not proof of actual
delivery**. Confirm you can still authenticate with the existing password
and existing second factor.

**Important version note:** Authelia 4.39.28 does **not** have an
`authelia debug notification` command or `--recipient` option. Do not use
the command documented for newer Authelia releases. To verify real delivery,
request an **actual user-initiated** verification notification through
Authelia's existing enrollment UI, when appropriate. An OTP device
registration request is one possible trigger for an account without an
enrolled OTP device. Do **not** delete or replace any existing TOTP/passkeys
just to test email. Use a trusted test account, and do not share email links
or verification tokens.

## Immediate rollback

If configuration validation or Authelia startup fails, restore the backed-up
notifier YAML and restart only Authelia under the **old** overlays:

```bash
sudo python3 scripts/prepare-authelia-smtp.py --rollback

base=(
  -f compose.yaml -f compose.control.yaml -f compose.accounts.yaml
  -f compose.computers.yaml -f compose.production.yaml
  -f compose.account-recovery.yaml
)
sudo docker compose "${base[@]}" \
  --profile control --profile accounts --profile metrics \
  up -d --no-deps --force-recreate authelia
sudo docker compose "${base[@]}" \
  --profile control --profile accounts --profile metrics ps authelia
```

The protected SMTP key may stay on disk; it is not referenced without the
optional overlay. Restore `notification.txt` *only* if independently required;
the migration does not delete or truncate it.

Do not re-run `--apply` over an existing backup; review or roll back first.
This process uses the same Resend API key as LLDAP for now. A separate
domain-scoped sending key for Authelia is a later security improvement.
