# Consolidate private web access behind Caddy

## Phase A: provide and test private HTTPS routes

Use the existing **home.homeawayfromhome.house** HTTPS gateway for
Pi-hole administration at **/dns/admin/**, portal at **/**, and
PC control at **/control/**. Use **metrics.homeawayfromhome.house** for
Grafana and telemetry. No new DNS record, TLS certificate, or WAN port.

Pi-hole v6 explicitly supports the reverse-proxy prefix /dns. Caddy strips
that prefix before forwarding, so Pi-hole /dns/api/* does not collide with
HomeAway's own /api/* endpoints. Authelia authenticates the Pi-hole route;
Pi-hole retains its independent administrator password.

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

## Phase B: retire direct web listeners only after acceptance

The separately reviewed **Stage B** rollout removes only those four listeners
after the real-browser checks above pass. Verify that SensorDash's Last delivery
is still updating and SSH to the Pi works over the LAN; do not deploy through
a session dependent on Pi-hosted DNS or WireGuard.

    cd ~/HomeAwayFromHome
    git fetch origin
    git switch Production
    git merge --ff-only origin/Production
    bash scripts/deploy-private-web-ports.sh --plan

The plan validates the live Pi-hole /dns prefix, canonical HTTPS responses,
private DNS/VPN/SSH-loopback port requirements, and both private and rollback
Compose configurations. It makes no changes. **Only after confirming the
actual Pi-hole UI/API, HomeAway, Grafana embeds and role-gated PC controls
work in a browser**, apply:

    bash scripts/deploy-private-web-ports.sh --apply &&
    bash scripts/deploy-private-web-ports.sh --status

Only portal, Grafana, Pi-hole and Caddy are recreated. Pi-hole DNS pauses
briefly during its restart but its existing port 53 binding and persistent
data remain unchanged. The port cutover checks Caddy on HTTPS 443 and the
absence of old host web bindings, plus unauthenticated ingest rejection.
On failure it attempts to restore the old ports automatically. For manual
rollback (no other services touched):

    bash scripts/deploy-private-web-ports.sh --rollback

After a successful cutover, confirm fresh SensorDash delivery, new Grafana
samples, Pi-hole query resolution, VPN connectivity, login/TOTP and PC
status. The old direct admin addresses should no longer accept connections.

It removes:

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
