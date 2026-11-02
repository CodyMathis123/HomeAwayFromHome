# Consolidate private web access behind Caddy

## Phase A: provide and test private HTTPS routes

Use the existing **home.homeawayfromhome.house** HTTPS gateway for
Pi-hole administration at **/dns/admin/**, portal at **/**, and
PC control at **/control/**. Use **metrics.homeawayfromhome.house** for
Grafana and telemetry. No new DNS record, TLS certificate, or WAN port.

Pi-hole v6 explicitly supports the reverse-proxy prefix /dns. Caddy strips
that prefix before forwarding, so Pi-hole /dns/api/* does not collide with
HomeAway's own /api/* endpoints. Authelia authenticates the original /dns/* URL before Caddy strips the
prefix for Pi-hole. A dedicated admins + two_factor policy authorizes this
route; Pi-hole retains its independent administrator password.

From LAN-side SSH to the Pi:

    cd ~/HomeAwayFromHome
    git fetch origin
    git switch Production
    git merge --ff-only origin/Production
    bash scripts/deploy-gateway-web.sh --plan

If the plan succeeds and confirms the live gateway configuration:

    bash scripts/deploy-gateway-web.sh --apply &&
    bash scripts/deploy-gateway-web.sh --status

Only Caddy, the portal, and finally Pi-hole are recreated. Pi-hole DNS on
53/udp+tcp may pause briefly during its container restart; its persistent
configuration and existing LAN admin port 8080 remain unchanged.

Before removing any ports, manually verify in a browser:

1. HomeAway portal at https://home.homeawayfromhome.house/ (status API works).
2. Pi-hole at https://home.homeawayfromhome.house/dns/admin/
   (Authelia/TOTP, independent Pi-hole login, assets and /dns/api calls).
3. Grafana at https://metrics.homeawayfromhome.house/ including embeds.
4. Authenticated PC-control at https://home.homeawayfromhome.house/control/.
5. Account recovery at /directory/ if your Caddy uses that overlay.
6. A known LAN DNS lookup and VPN peer connectivity.

If the Pi-hole web prefix breaks its UI, leave direct ports published and
restore its prior root prefix, recreating only Pi-hole:

    bash scripts/deploy-gateway-web.sh --rollback-dns

The old Pi-hole address http://PI_LAN_IP:8080/admin/ is deliberately retained
during the phase A trial.

## Repair a 403 on /dns/admin/ (November 2026)

If Authelia logs a forbidden /admin/ request instead of /dns/admin/,
the gateway is stripping the prefix before forward-auth. The canonical
gateway and account-recovery variants now authenticate before rewriting.

After merging the fix to Production, from the Pi run:

    cd ~/HomeAwayFromHome
    git fetch origin
    git switch Production
    git merge --ff-only origin/Production
    sudo python3 scripts/fix-dns-admin-auth.py --plan
    sudo python3 scripts/fix-dns-admin-auth.py --apply
    sudo docker restart homeawayfromhome-gateway-1
    bash scripts/deploy-gateway-web.sh --status

This repair does **not** recreate Pi-hole, open LAN-facing listeners, or change
port 53. The Authelia restart temporarily interrupts sign-in, and restarting
Caddy briefly interrupts HTTPS. The migration validates a private candidate
configuration before applying it and retains a private backup at
`data/control/authelia/configuration.before-dns-admin.yml`.
Never paste that file into tickets or chat: it contains secrets.

The status check deliberately requires an anonymous /dns/admin/ request to
redirect (302) to the canonical Authelia login. A 403 is not considered
healthy. After the status check, sign in with an admins-group account, complete
TOTP, and verify the Pi-hole login, assets, and /dns/api/* calls in a browser.
Anonymous or non-admin users must not be granted Pi-hole access. A
successful anonymous redirect is only a gateway smoke test, not proof of
complete authenticated browser behavior.

## Phase B: retire direct web listeners only after acceptance

A separately reviewed rollout can remove:

| Current host port | Private gateway replacement |
| --- | --- |
| Pi-hole web 8080/tcp | home.homeawayfromhome.house/dns/admin/ |
| Portal 8088/tcp | home.homeawayfromhome.house/ |
| Grafana 3000/tcp | metrics.homeawayfromhome.house/ |
| Legacy gateway 8443/tcp | Standard HTTPS port 443 |

Do not remove the following: Pi-hole DNS 53/tcp and 53/udp, WireGuard
51820/udp, Caddy HTTPS 443/tcp. LLDAP 17170/tcp and wg-easy 51821/tcp
are bound only to 127.0.0.1, not LAN-facing; retain these SSH-tunnel
maintenance access points unless their workflows receive a separate review.

No router-facing web ports are added. The hardened routing does not change
SensorDash fan control, its telemetry TLS and bearer authentication, or the
host-side PC management agents. This is access reduction, not high availability.
