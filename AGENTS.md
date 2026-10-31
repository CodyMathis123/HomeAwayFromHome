# Instructions for AI and human contributors

HomeAwayFromHome configures a live household DNS/VPN appliance. Follow this safety policy.

- **Never** assume access to the Raspberry Pi, router, real domain/provider, existing secrets or active peer profiles. No production deployment, DNS changes, router forwards, peer revocation or credential rotation without explicit operator authorization and a rollback plan.
- Make code changes on a focused branch with CI; no automatic merges or deployments. Document observable behavior and remaining live-hardware testing.
- Don't commit .env, secrets, keys, QR codes, client configs, backups, logs or client query data. Do not print their contents. Keep backup handling offline and note that it contains authentication material.
- Only UDP 51820 is intended to be port-forwarded; keep wg-easy admin 51821 loopback-only, do not publicly expose Pi-hole DNS/admin or dashboard endpoints. Do not add a Docker socket mount to any HTTP service.
- No arbitrary command execution or admin API actions from the read-only portal. Respect that TCP listener checks are NOT equivalent to WireGuard handshakes.
- Prefer upstream maintained images, version pinning, explicit privilege boundaries and minimal dependencies. Don't add unattended autoupdaters to DNS/VPN. Review current release notes when changing image versions.
- Run Python unit tests, compile checks, `docker compose config -q`, resolved Compose security checks, and review docs after changes. Report explicitly when Docker, hardware or actual VPN verification is unavailable.
- Keep router DHCP and staged DNS cutover/rollback docs in sync with any network changes. Use IP ranges discovered by the operator; example IPs in this repository are placeholders.
