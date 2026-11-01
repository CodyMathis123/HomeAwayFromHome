# HomeAway read-only embedded Grafana metrics

This implementation adds one **optional** Grafana iframe to HomeAway's existing read-only portal. It never enables Grafana anonymous organization access, shares credentials, or exposes an admin API.

## Trust boundary and caveat

Grafana's **Share externally → Anyone with the link** feature does not require login. It can run **only the saved dashboard queries**; however, *anyone who has the link and network reachability* can view the displayed metric values. It is **not** user authentication and does not restrict who on the household LAN can see data. The high-entropy token is sent to portal visitors and must not be mistaken for an admin password. Keep Grafana TCP 3000 and portal TCP 8088 available only on the Pi private LAN/VPN, never router-forwarded, including IPv6. Do not share dashboards with sensitive annotations or identifying labels. Disable the share immediately if the URL leaks beyond your intended network.

Grafana's `GF_SECURITY_ALLOW_EMBEDDING=true` allows iframe rendering for the whole Grafana instance (clickjacking protection is relaxed). Grafana admin login **remains required**, anonymous auth stays disabled. This is a tradeoff for LAN-only access, not appropriate for public WAN exposure.

## Enable after reviewing on your Pi

1. Deploy this reviewed branch using your existing manual maintenance process; **do not** change router/firewall settings or restart DNS/VPN for this feature. Only the portal and Grafana need recreation.
2. Log into Grafana at `http://PI_LAN_IP:3000`. Verify **HomeAway · Portal Metrics** (dashboard UID `homeaway-portal`) is provisioned. Its panels reuse queries from Overview, DNS/VPN and System Health. Shared Grafana dashboards do not support template variables, so it has none; Windows summary stats represent the highest available value across exported Windows hosts.
3. Open **HomeAway · Portal Metrics → Share → Share externally → Anyone with the link**. Confirm you are sharing only this specific dashboard. Leave annotations **off**; optionally allow time selection. Copy the link. This manual step deliberately prevents enabling no-login access simply by deploying code.
4. Extract the 32-character hexadecimal token after `/public-dashboards/` from the copied URL. On the Pi, edit your existing local `.env`:
   ```dotenv
   GRAFANA_PUBLIC_EMBED_TOKEN=YOUR_GENERATED_32_CHAR_HEX_TOKEN
   ```
   Keep `.env` out of Git. The value is not an admin credential but is a viewer access capability. Do not use the internal dashboard UID `homeaway-portal` here.
5. Recreate **only** the two relevant containers:
   ```bash
   docker compose --profile metrics up -d --no-deps --force-recreate grafana
   docker compose up -d --no-deps --build portal
   ```
6. Open `http://PI_LAN_IP:8088` in an ordinary browser (test a private window with no Grafana login). Scroll to **Live metrics**. There should be one lazily created read-only iframe, and the link beneath it should open the same shared view. Closing the section or hiding the page unloads the iframe. Confirm the Grafana administration UI at `:3000` still requires a login.
7. Check browser console/network for errors, then review Pi CPU/RAM and Grafana query load during idle and with the portal open. The dedicated dashboard refreshes every 30 seconds. Missing stats can mean the relevant exporters are offline; the iframe cannot remedy absent metrics. Grafana iframe loading requires the browser to reach `http://PI_LAN_IP:3000` directly. A future HTTPS-only portal will need an HTTPS Grafana endpoint too to avoid mixed-content blocking.

### Turn off / revoke

- To hide embedding quickly, clear `GRAFANA_PUBLIC_EMBED_TOKEN` in `.env` and recreate **portal only**. This does **not** invalidate an already-issued public link.
- To revoke the no-login link, open Grafana **Dashboards → Shared dashboards → HomeAway · Portal Metrics → Pause/Revoke access**. This is the actual security revocation; do it even if the portal has been disabled.
- No changes to SensorDash fan control or Windows service operation are involved.

### Design and limits

- The portal retains its own static health checks even if the Grafana metrics profile is stopped.
- One responsive, single iframe is loaded only when the section is visible/open, avoiding multiple complete Grafana app contexts.
- `/api/embed-config` is read-only and returns no secrets beyond the already shareable dashboard URL. The URL is constructed server-side from the validated private `PI_LAN_IP` and an explicitly configured hexadecimal token, not arbitrary user-supplied origins.
- Portal CSP allows frames only from this Pi's `:3000` Grafana origin and denies all frames when unconfigured.
- There are no cooling/fan controls, arbitrary data queries, proxy credentials, or Grafana admin access in the portal.
- Provisioned dashboard definitions are Git-reviewed. The shared-link creation and revocation themselves are intentionally performed from authenticated Grafana on your own Pi.

References:
- https://grafana.com/docs/grafana/latest/visualizations/dashboards/share-dashboards-panels/shared-dashboards/
- https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/

## With the optional authenticated HTTPS gateway

If you also deploy [PC control and the Caddy/Authelia gateway](PC-CONTROL.md), use the gateway hostname `https://homeaway.home.arpa:8443` for the combined portal. Grafana's proxied shared-dashboard origin becomes `https://metrics.homeaway.home.arpa:8443`, avoiding mixed-content iframes. Use **both** Compose files for recreation (see the PC-control runbook). The original HTTP :8088 procedure above is only for the standalone, read-only portal and must not be mixed with the HTTPS overlay; no second Grafana user login is required for the shared view.
