# Face ID sign-in (iPhone)

## Working setup (November 2026)

HomeAway authentication uses **username + password + Face ID**. Authelia
4.39 accepts an Apple/iPhone WebAuthn security key as the second factor.
The user has successfully exercised this flow; no passwordless sign-in is
required. TOTP remains available as a fallback.

The registered Apple credential reports **Discoverable: No**. It works as a
second factor but fails the standalone **Sign in with a passkey** flow;
Authelia logs `Unable to find the credential for the returned credential ID`.
Enabling a discoverability requirement for *new* credentials did not resolve
the user's passwordless login attempt. Do not alter stored credential IDs or
remove the functioning security-key credential to pursue passwordless login.

## Pi configuration

The optional `compose.passkeys.yaml` overlay remains available. It enables
WebAuthn/passkey login without changing the existing two-factor policy.
The local attempt added
`AUTHELIA_WEBAUTHN_SELECTION_CRITERIA_DISCOVERABILITY: "required"`.
That override is not necessary for the working password + security-key
flow and can be removed locally; the tracked overlay does not include it.

To remove **only** that unsuccessful local override, run from the Pi checkout:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path('compose.passkeys.yaml')
lines = p.read_text().splitlines(keepends=True)
key = 'AUTHELIA_WEBAUTHN_SELECTION_CRITERIA_DISCOVERABILITY:'
out = [line for line in lines if key not in line]
if len(out) != len(lines):
    p.write_text(''.join(out))
    print('Removed optional discoverability override.')
else:
    print('No discoverability override present.')
PY
```

No container recreation is required merely to edit this file. To apply
the change to the currently running Authelia environment, recreate **only**
`authelia` using the normal five production Compose overlays plus
`compose.passkeys.yaml`, after validating `docker compose config -q`.
Do not restart Pi-hole, VPN, Caddy or the entire Compose project.

## Registering an additional security key

Sign in to `https://auth.homeawayfromhome.house:8443`, open **Settings →
Two-Factor Authentication → Security Keys**, and register with the iPhone
Face ID prompt. Keep TOTP enrolled. For the existing two-factor access
policy, choose **Security Key** after entering username and password;
a discoverable passwordless passkey is not required.
