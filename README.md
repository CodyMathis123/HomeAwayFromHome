# HomeAwayFromHome

**Self-hosted home infrastructure and monitoring on a Raspberry Pi 4 (8 GB).** Core services are Pi-hole and WireGuard; optional profiles add VictoriaMetrics/Grafana and a private HTTPS HomeAway control gateway. Windows SensorDash owns hardware sampling and fan control. HomeAway displays history and can separately manage registered PCs through its restricted `pcctl` agent; it never controls radiator PWM.

> **Current status (November 2, 2026):** `Production` contains the Pi infrastructure, Grafana/VictoriaMetrics, authenticated HomeAway control, LLDAP-backed household account administration and Phase 2 Computer Management (merged in [PR #14](https://github.com/CodyMathis123/HomeAwayFromHome/pull/14)). The operator completed live Pi socket checks, role/UI checks and an inactive computer draft exercise; the Production container cutover was subsequently reported complete. **Real enrollment and disable/archive/restore on a second Windows PC remain unverified.** Future setup/monitoring and operations/maintenance phases are **planning**, not shipped. See the [current roadmap](docs/ROADMAP.md) for branch ownership and outstanding acceptance.
 
## Modular Linux installation (prototype)

An isolated, component-aware Linux monitoring prototype demonstrates
how Windows SensorDash historical telemetry could run on an ordinary Linux
host without Pi-hole, WireGuard, accounts or remote PC management.
It does **not** modify the existing HomeAway Pi or replace its deployment.

- [Component catalog and packaging architecture](docs/MODULAR-ARCHITECTURE.md)
- [Feature selection, dependency resolution and safety boundaries](docs/COMPONENT-SELECTION.md)
- [Linux host monitoring and generated Compose](docs/LINUX-HOST-AND-COMPOSE.md)
- [Optional read-only diagnostics and authenticated component-aware Portal](docs/MODULAR-DIAGNOSTICS-PORTAL.md)
- [Encrypted backups and reversible optional component changes](docs/MODULAR-LIFECYCLE.md)
- [Developer architecture and module boundaries](docs/DEVELOPER-ARCHITECTURE.md)
- [Cooling-only proof of concept and acceptance checklist](docs/COOLING-STANDALONE.md)
- [Portable/.deb release packaging and real Linux CI](docs/COOLING-RELEASES.md)

This is experimental; do not deploy it on the live Pi until reviewed and
independently tested. The existing full-stack `Production` installation
remains the supported baseline.

## Start with Production

| Capability | Current state | Guide |
| --- | --- | --- |
| Core DNS/VPN/portal and metrics | Implemented in `Production`; opt-in Compose profiles still apply | [Install](docs/INSTALL.md) · [Telemetry](docs/TELEMETRY.md) |
| Read-only Grafana embed | Implemented; sharing is opt-in and separate from Grafana administration | [Embed](docs/PORTAL-METRICS-EMBED.md) |
| Private HTTPS, Authelia/LLDAP, roles and PC power | Implemented; preserve existing user and host data | [PC controls](docs/PC-CONTROL.md) · [Operations](docs/OPERATIONS.md) |
| Household account administration | **Live and verified:** LLDAP user invitations, password setup/reset via Resend, role administration and deletion; Authelia factor security notifications use Resend rather than `notification.txt` | [Account recovery](docs/ACCOUNT-EMAIL-RECOVERY.md) · [Authelia email](docs/AUTHELIA-RESEND-NOTIFICATIONS.md) |
| Phase 2 Computer Management | Implemented; new-Windows-PC physical lifecycle acceptance deferred | [Rollout and recovery](docs/COMPUTER-ADMIN-ROLLOUT.md) |
| Phase 3 Setup/Monitoring; Phase 4 Operations/Maintenance | Design-only branches; **not** deployment branches | [Roadmap](docs/ROADMAP.md) |

Legacy feature branches such as `feature/pi4-home-infrastructure`, `feature/homeaway-grafana-embed` and `feature/homeaway-computer-management-impl` are historical ancestors, **not** alternate installation targets. For new installations use `Production`; for an existing Pi preserve `.env`, `secrets/`, `data/`, its host-side configuration and each opt-in Compose overlay. Do not replace a working DNS/VPN deployment with the quick-start commands below.

## Service boundaries

| Responsibility | Source of truth |
| --- | --- |
| DNS filtering and VPN peers | Pi-hole and wg-easy on the Pi |
| Windows sensor collection, fan profiles, PWM output, cooling systems/associations, aliases | SensorDash on each Windows computer |
| Long-term history, plots, electrical estimates, telemetry freshness | VictoriaMetrics and Grafana on the Pi |
| Read-only home status | HomeAway HTTP portal on private LAN/VPN :8088 |
| PC wake / sleep / reboot / shutdown | Opt-in HomeAway HTTPS :8443 UI → authenticated `pc-control` → restricted Pi `pcctl` → Windows SSH or WOL |

**WAN exposure:** Forward only WireGuard UDP 51820. Keep HTTP :8080, :8088, :3000, :8429 and HTTPS :8443 on the LAN/VPN only. wg-easy admin TCP :51821 remains bound to loopback and accessible through SSH forwarding. The controller has no published port, SSH private-key mounts, Docker socket, or direct fan-control endpoint.

For the [current housekeeping audit and next priorities](docs/PRODUCTION-HOUSEKEEPING-2026-11-02.md), see the November 2 production reconciliation. For the simplified architecture and intended trust boundaries, read [security](docs/SECURITY.md) and [decisions](docs/DECISIONS.md). Historical implementation milestones are archived in [Project history](docs/PROJECT-HISTORY.md).

## Quick start (new Pi or after imaging)

**Do not run this blindly on a Pi currently serving household DNS or VPN.** You will interrupt those services during replacement. Work from a local machine with a fallback DNS connection, and export anything you may want to keep first.

1. Install **Raspberry Pi OS Lite 64-bit**, connect Ethernet, enable SSH, configure SSH keys, OS updates, and a reliable static/reserved Pi LAN IP. Install Docker Engine + the Compose plugin using [Docker's Debian instructions](https://docs.docker.com/engine/install/debian/). Recommended: USB SSD, recent Raspberry Pi kernel, good power supply.
2. For a **new installation only**, clone the reviewed `Production` branch, then create local configuration and secrets. For an existing Pi, **do not** rerun initialization or replace saved state; use the relevant operator runbooks instead:

   ```bash
   git clone -b Production https://github.com/CodyMathis123/HomeAwayFromHome.git
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
| Grafana | `http://PI_IP:3000` | Optional metrics profile, authenticated; shared read-only dashboard is separately opt-in |
| HomeAway HTTPS gateway | `https://homeaway.home.arpa:8443` | Optional Caddy + Authelia; computer controls require 2FA |
| Authelia login | `https://auth.homeaway.home.arpa:8443` | Optional local account and enrolled second factor |

Start optional Grafana/VictoriaMetrics using the [telemetry guide](docs/TELEMETRY.md), which includes local HTTPS certificate and bearer token bootstrap. Monitoring charts never authorize physical fan control. Do not add a Docker socket to any web service.

## Current private HTTPS deployment

The live Pi uses `home.homeawayfromhome.house`,
`auth.homeawayfromhome.house` and
`metrics.homeawayfromhome.house` on **LAN/VPN-only port 8443**.
The historical `.home.arpa` links above are retained as initial-install
examples, **not the canonical live endpoints**.

The live production Compose project uses these overlays, in order, for
account/HTTPS work:

```bash
files=(
  -f compose.yaml
  -f compose.control.yaml
  -f compose.accounts.yaml
  -f compose.computers.yaml
  -f compose.production.yaml
  -f compose.account-recovery.yaml
  -f compose.authelia-smtp.yaml
)
profiles=(--profile control --profile accounts --profile metrics)
```

The last two overlays are **opt-in on a new Pi** and already enabled
on the current deployment. They give LLDAP password-setup mail and
Authelia second-factor security mail a working Resend SMTP path.
After changing Compose files, recreate **only the affected services**,
never issue unscoped `up -d` on a functioning DNS/VPN installation.
The non-secret Caddyfile bound by `compose.account-recovery.yaml` is
`control/gateway/Caddyfile.account-recovery`, which must be readable
by non-root Caddy. Keep tokens and private credentials restricted.
See [production operations](docs/OPERATIONS.md) for preflight commands.

## Operator commands

```bash
bash scripts/doctor.sh              # preflight and port collisions
docker compose ps                  # running containers
docker compose logs -f --tail=100  # logs
bash scripts/inspect-vpn.sh        # wg show: peers and handshakes
bash scripts/smoke.sh              # LAN DNS and portal tests
sudo bash scripts/backup.sh --offline  # consistent backup; temporarily stops DNS/VPN
bash scripts/update.sh --apply     # manual reviewed update; core services only
bash scripts/deploy-ui.sh --plan  # safe preflight for HomeAway UI releases
bash scripts/deploy-ui.sh --apply # fast-forward Production; deploy ONLY portal + pc-control
```

The Compose backup contains **private keys and passwords**, is chmod 0600, and must be copied to encrypted storage away from the Pi. **It does not include host-side PC inventory, host-side pairing keys, or the separate inventory-service state**; see [backup coverage and recovery gaps](docs/OPERATIONS.md). No unattended image updates are configured; pin images in `compose.yaml` and review upstream security releases regularly. See [operations](docs/OPERATIONS.md).

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



## Optional features and deployment order

The DNS/VPN quick start above does **not** start the optional control service. First enable and check Grafana/VictoriaMetrics via [telemetry setup](docs/TELEMETRY.md), then optionally share the compact metrics view via [Grafana embed setup](docs/PORTAL-METRICS-EMBED.md). Next, when ready to enable computer power commands, follow [PC control / Authelia onboarding](docs/PC-CONTROL.md) to configure the Pi host's inventory and socket, provision each Windows client's restricted SSH account, enable trusted local HTTPS, enroll two-factor auth, and verify access. The existing iOS Shortcut may remain operational.

**Do not let remote PC management become a second hardware-control system:** SensorDash keeps all PWM authorization and cooling mappings, while HomeAway's PC power commands use a separate allowlisted, audited host agent. For visual and historical features, prefer Grafana panels to reimplementing graph code inside the portal. The [operating guide](docs/OPERATIONS.md) covers backups and manual updates, and [live validation](docs/LIVE-VALIDATION.md) covers the staged Pi/Windows checks.
