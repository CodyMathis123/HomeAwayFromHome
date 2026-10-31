# HomeAwayFromHome

**A small, predictable home-infrastructure stack for one Raspberry Pi 4 (8 GB).**

Pi-hole v6 · WireGuard via wg-easy v15 · built-in responsive read-only status portal · optional Grafana + VictoriaMetrics. Everything is self-hosted on the Pi using Docker Compose. No VPS, external dashboard, mandatory account, or ongoing control-plane dependency.

> **Status:** feature-branch implementation with a first real Pi 4 / Raspberry Pi OS 64-bit deployment. Pi-hole reached healthy DNS service, wg-easy started successfully with the documented nftables-compatible image, and the read-only portal/smoke checks passed on October 31, 2026. Real iPhone cellular VPN routing, DNS coverage through the VPN, eero household DNS cutover and Grafana/SensorDash telemetry are **not yet fully validated**. Read the [installation runbook](docs/INSTALL.md) and [existing-Pi migration notes](docs/EXISTING-PI-MIGRATION.md) before replacing live DNS or VPN.

## Architecture

```text
                Internet / cellular
                         |
       Your public vpn.example.com -> home WAN IP
                         |
          Router: forward UDP 51820 ONLY
                         |
                    Raspberry Pi 4
                  Raspberry Pi OS 64-bit
                  Ethernet / fixed LAN IP
                         |
                  Docker Compose
       +-----------------+------------------------+
       |                 |                        |
  Pi-hole v6         wg-easy v15           HomeAway portal
  DNS :53            VPN UDP :51820       HTTP :8088 (LAN)
  Web :8080 (LAN)    Admin :51821          Read-only checks
                     localhost ONLY              |
       |                 |                 Grafana :3000
       +------- Pi LAN DNS / RDP / SensorDash ----+ (optional metrics)
```

- **Public router exposure:** UDP 51820 to the Pi only. No published administration UI, SSH, DNS or HTTP WAN forwarding.
- **WireGuard manager:** bound to the Pi's IPv4 loopback, reachable from an administrator's PC using an SSH port-forward. Admin HTML is HTTP only *inside the SSH tunnel*.
- **Pi-hole DNS:** bound to the Pi's specified private IPv4 address, TCP and UDP 53; administration on port 8080 is **LAN/VPN-only HTTP with password**. Keep the LAN trustworthy; add HTTPS before extending web-admin access beyond it.
- **Portal:** `http://PI_IP:8088` on LAN/VPN, zero dependencies beyond Python stdlib, no secrets, no Docker socket, no write API. Checks Pi-hole DNS and wg-easy's admin listener (not an actual VPN handshake).
- **Optional Grafana:** `http://PI_IP:3000` with its own login; leave off until needed.

## Quick start (new Pi or after imaging)

**Do not run this blindly on a Pi currently serving household DNS or VPN.** You will interrupt those services during replacement. Work from a local machine with a fallback DNS connection, and export anything you may want to keep first.

1. Install **Raspberry Pi OS Lite 64-bit**, connect Ethernet, enable SSH, configure SSH keys, OS updates, and a reliable static/reserved Pi LAN IP. Install Docker Engine + the Compose plugin using [Docker's Debian instructions](https://docs.docker.com/engine/install/debian/). Recommended: USB SSD, recent Raspberry Pi kernel, good power supply.
2. Clone this branch, then create the local configuration and secret:

   ```bash
   git clone -b feature/pi4-home-infrastructure https://github.com/CodyMathis123/HomeAwayFromHome.git
   cd HomeAwayFromHome
   bash scripts/init.sh
   nano .env
   bash scripts/doctor.sh
   ```

3. Set `PI_LAN_IP`, `LAN_CIDR`, `VPN_ENDPOINT` (a hostname you own with a WAN DNS record), and `VPN_CIDR`. Ensure LAN, VPN and Docker subnet `10.42.42.0/24` do not overlap. Set router firewall/DHCP **after** standalone verification. Leave old Pi-hole / WireGuard stopped before binding conflicting ports.
4. Start and test locally:

   ```bash
   docker compose up -d --build pihole wg-easy portal
   bash scripts/smoke.sh
   ```

5. Access the wg-easy **initial setup** from your computer through SSH:

   ```bash
   ssh -L 51821:127.0.0.1:51821 <pi-username>@<PI_LAN_IP>
   ```

   Open `http://127.0.0.1:51821` **on your computer**, complete the wg-easy v15 wizard with a strong unique admin password and the VPN endpoint/port. Set client DNS to your Pi's LAN address, and create a test peer. Install the [WireGuard client](https://www.wireguard.com/install/) on your iPhone. Test on cellular **before** disabling old VPN access.

6. Forward **UDP 51820** on your router to the Pi. Check for ISP CGNAT if the handshake never works; DNS records alone cannot fix CGNAT. Use the iPhone's WireGuard client to verify tunnel handshake, Pi-hole DNS filtering, RDP and SensorDash access.
7. Only after those tests, change the router's DHCP DNS to the Pi's LAN IP and renew clients' leases. Router stays the DHCP server. Never port-forward TCP/UDP 53 or any web-admin ports.

Detailed notes: [Existing-Pi headless migration and observed network fixes](docs/EXISTING-PI-MIGRATION.md) · [Install and cutover](docs/INSTALL.md) · [Security](docs/SECURITY.md) · [Operations and recovery](docs/OPERATIONS.md) · [Architecture decisions](docs/DECISIONS.md).

**Raspberry Pi nftables note:** the pinned wg-easy 15.5.0 image is built locally using `Dockerfile.wg-easy-nft`, which selects the `iptables-nft` and `ip6tables-nft` tools inside the container. This addresses a confirmed startup failure on Pi kernel 6.18.50 without altering host firewall alternatives. Also, `VPN_CIDR` and `VPN_ENDPOINT` in `.env` are not currently injected into wg-easy; set and verify the actual interface, endpoint and client defaults in wg-easy's v15 UI. See the migration notes for details.

## Client profiles and DNS

**Split tunnel** is the sensible starting point: WireGuard AllowedIPs should include your home LAN CIDR and the WireGuard peer subnet, while DNS points to the Pi's LAN IP. That allows private LAN resources + Pi-hole DNS over VPN without sending all phone web traffic through your home. Create and inspect peers in the wg-easy UI; **verify generated configurations** rather than assuming the wizard applies the desired routes.

Full tunnel is optional: use `0.0.0.0/0` for IPv4 AllowedIPs when intentionally routing all IPv4 traffic through home (and design/test IPv6 separately). iOS Private Relay and applications using encrypted DNS may bypass Pi-hole filtering; confirm using Pi-hole's query log.

## The dashboards

| Component | URL from home or VPN | Administration |
| --- | --- | --- |
| HomeAway portal | `http://PI_IP:8088` | Read only; no credentials |
| Pi-hole | `http://PI_IP:8080/admin/` | Pi-hole password |
| wg-easy | `http://127.0.0.1:51821` | Only via SSH forwarding, then wg-easy login |
| Grafana | `http://PI_IP:3000` | Optional metrics profile, authenticated |

Start optional Grafana/VictoriaMetrics using the [telemetry guide](docs/TELEMETRY.md), which includes local HTTPS certificate and bearer token bootstrap. Monitoring charts never authorize physical fan control. Do not add a Docker socket to any web service.

## Operator commands

```bash
bash scripts/doctor.sh              # preflight and port collisions
docker compose ps                  # running containers
docker compose logs -f --tail=100  # logs
bash scripts/inspect-vpn.sh        # wg show: peers and handshakes
bash scripts/smoke.sh              # LAN DNS and portal tests
sudo bash scripts/backup.sh --offline  # consistent backup; temporarily stops DNS/VPN
bash scripts/update.sh --apply     # manual reviewed update; core services only
```

The backup file includes **private keys and passwords**, is chmod 0600, and must be copied to encrypted storage away from the Pi. No unattended image updates are configured; pin images in `compose.yaml` and review upstream security releases regularly. See [operations](docs/OPERATIONS.md).

## Development

```bash
python3 -m unittest discover -s scripts/tests -v
python3 -m compileall -q portal scripts
# With Docker Compose installed:
cp .env.example .env
mkdir -p secrets; printf 'test-only' > secrets/pihole_password
docker compose config -q
docker compose --profile metrics config -q
python3 scripts/check_contracts.py
```

Never commit `.env`, `secrets/`, `data/`, `backups/`, or client profiles/QR codes. CI tests configuration without starting DNS or WireGuard or modifying a router. See [AGENTS.md](AGENTS.md) for AI-assisted change rules.

**References:** [Pi-hole Docker](https://docs.pi-hole.net/docker/) · [Pi-hole v6 config](https://docs.pi-hole.net/docker/configuration/) · [wg-easy 15 setup](https://wg-easy.github.io/wg-easy/v15.2/examples/tutorials/basic-installation/) · [wg-easy admin security warning](https://wg-easy.github.io/wg-easy/latest/examples/tutorials/reverse-proxyless/) · [Grafana](https://grafana.com/docs/grafana/latest/) · [VictoriaMetrics](https://docs.victoriametrics.com/).


## SensorDash / Grafana milestone 1

See [docs/TELEMETRY.md](docs/TELEMETRY.md) and [cooling dashboard contract](docs/COOLING-GRAFANA.md): an optional Grafana 13.3.0 + VictoriaMetrics v1.155.0 profile, write-only authenticated TLS intake, a raw-sensor dashboard, a metadata-driven cooling-system dashboard, and a provenance-labeled fan/pump electrical dashboard. SensorDash's exporter remains disabled by default; all existing local control and browser features continue. Do not expose metrics endpoints at the router.

## Real hardware validation

After CI passes, follow [First Pi/Windows rollout and side-by-side verification](docs/LIVE-VALIDATION.md). It describes the staged Pi-hole/DNS cutover, WireGuard test from iPhone cellular, private CA/token transfer to Windows, and the read-only `scripts/verify-telemetry.sh` checker. **Do not merge or delete the original SensorDash dashboard until that comparison passes.**

## Optional Pi-hole and WireGuard dashboards

The metrics profile now has opt-in internal monitoring for Pi-hole v6 aggregate DNS statistics and wg-easy v15 native Prometheus statistics. Both use the existing VictoriaMetrics instance. No additional host port is opened. Follow [DNS & VPN monitoring setup](docs/DNS-VPN-MONITORING.md) to configure app/bearer credentials and enable wg-easy Prometheus. The HomeAway DNS/VPN dashboard remains read-only; peer public endpoints and DNS queries are not collected.

## SensorDash-style unified overview

The read-only [SensorDash Overview](grafana/dashboards/overview.json) puts featured temperatures/RPM, recorded peaks, CPU/GPU power, cooling electrical estimates, temperature trends and a hardware catalog on one Grafana page. See [design and limitations](docs/GRAFANA-DESIGN.md). Windows remains the sole authority for hardware control.

## Storage and events moved to Grafana

Storage-device activity and temperature, local history-buffer footprint, and historical threshold events are now available in the new read-only [Storage](grafana/dashboards/storage.json) and [Events](grafana/dashboards/events.json) dashboards. Windows remains the sole owner of sensor sampling, alert thresholds, fan control, tray icons and cooling associations. Deploy the Pi ingestion schema before upgrading the Windows exporter.
