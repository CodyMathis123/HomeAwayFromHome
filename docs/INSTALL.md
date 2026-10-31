# Installation and controlled DNS/VPN cutover

## Stage 0: decisions and backups

This installation assumes **one Pi 4, 8 GB RAM**, Raspberry Pi OS Lite 64-bit (ARM64), Ethernet, and the router retaining DHCP. Treat the old DNS/VPN setup as disposable, but export current Pi-hole Teleporter settings and WireGuard peer profiles if you might need rollback. Write down the router's DHCP DNS, static reservation, WAN forwarding and dynamic-DNS configuration *before* changing them.

**Important:** One Pi means one DNS single point of failure. Changing the router's DNS too early can interrupt browsing for the household. During initial testing, set DNS manually on one test laptop or phone, not the entire LAN.

## Stage 1: OS and prerequisites

1. Flash Pi OS Lite 64-bit (prefer an SSD if available), configure a nondefault user, SSH key login, Ethernet and reliable IP assignment.
2. Update OS packages and reboot. Confirm `uname -m` reports `aarch64`.
3. Install Docker Engine and Docker Compose v2 from the official Docker repository. Validate with `docker info` and `docker compose version`.
4. Verify port 53 is not already held by old Pi-hole, dnsmasq, systemd-resolved or a container. Check with `sudo ss -luntp` and `docker ps`. Stop/remove **only the old service you recognize** after collecting any desired backup.
5. Ensure Pi's host interface actually owns the `PI_LAN_IP` used for Docker binds. With DHCP reservations, Docker might start before the IP is acquired; configure boot ordering or static addressing if necessary.

## Stage 2: safe deployment

Run `bash scripts/init.sh`, edit `.env` to match your *real* home subnet and domain, then run `bash scripts/doctor.sh`. This validates overlapping networks; the default Docker bridge subnet is `10.42.42.0/24`.

Start `docker compose up -d --build pihole wg-easy portal`. The initial admin secret is at `secrets/pihole_password` (do not paste it into logs or support tickets). Pi-hole admin is `http://PI_LAN_IP:8080/admin/`; the portal is `http://PI_LAN_IP:8088`.

Ensure Pi-hole resolves `example.com`:

```bash
dig @PI_LAN_IP example.com A
```

Before changing the router DHCP DNS, configure one test device to use the Pi and inspect the Pi-hole query log. If the client has IPv6 DNS/DoH/Private Relay configured, it may bypass your Pi. Resolve that deliberately, without disabling unrelated internet security features by accident.

## Stage 3: VPN peer creation

From an admin laptop/desktop:

```bash
ssh -L 51821:127.0.0.1:51821 your-pi-user@PI_LAN_IP
```

Browse to `http://127.0.0.1:51821`. Complete wg-easy's first-run wizard. Use:
- A **unique, strong** admin password (and enable 2FA in the UI if available).
- Endpoint hostname matching `VPN_ENDPOINT`, with UDP port **51820**.
- An unused VPN IPv4 subnet (example `10.13.13.0/24`); confirm the app's actual server/client allocations.
- DNS server **PI_LAN_IP**.
- For a **split tunnel**, peer AllowedIPs include **LAN_CIDR** and the configured VPN tunnel subnet. Some interfaces default to full tunnel; inspect or edit the generated client before import. Check iPhone routes on cellular.
- If the wizard requires IPv6 settings, choose a unique ULA IPv6 prefix; don't advertise IPv6 full-tunnel routing until you've verified it end to end.

Create one iPhone peer. Scan its QR **privately**; it contains a VPN private key. Use a mobile network (not home Wi-Fi) to verify external connectivity. If blocked, check domain A record, router UDP forward, the Pi container logs and your ISP public-IP/CGNAT situation.

A ready admin UI does NOT imply an active VPN. `docker compose exec -T wg-easy wg show` displays actual peer handshakes. Pi-hole should show remote queries when the VPN client uses DNS as intended.

## Stage 4: prove LAN reachability

On cellular with WireGuard enabled:
1. Use Safari to reach `http://PI_LAN_IP:8088` (portal) and `http://PI_LAN_IP:8080/admin/` (Pi-hole); login only over a trusted VPN/LAN, not a public Wi-Fi network.
2. Test RDP to your Windows host's LAN IPv4 address. Windows Firewall and RDP must independently permit appropriate private-network clients.
3. Test `http://WINDOWS_LAN_IP:8765` only if the separate SensorDash LAN feature is installed/enabled and Windows Firewall explicitly permits the VPN traffic; otherwise leave it local.
4. Inspect Pi-hole query logs to confirm DNS filtering actually traverses the tunnel. Some devices/browser apps override system DNS.

## Stage 5: DNS cutover and recovery

- On the router, set DHCP DNS to PI_LAN_IP; router continues supplying DHCP leases.
- Renew one LAN client's DHCP lease and test web browsing. Then verify other clients.
- Decide whether to specify a backup DNS server: a public secondary DNS keeps some clients online when the Pi fails but **bypasses filtering** whenever selected. There is no perfect failover with only one Pi.
- Maintain a written router login/rollback route. If DNS fails, set DHCP DNS back to a known resolver or configure an individual client to use it directly.

## Not in scope

The configuration never flashes the Pi, installs packages, edits the router, changes household DHCP, opens host firewall rules, or imports VPN peer keys automatically. Those are operator-controlled steps. For public web services and custom-domain HTTPS, design an authenticated reverse-proxy boundary separately.
