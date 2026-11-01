# Security model

This is a **home-network appliance**, not a hardened public multi-tenant service. The optional VictoriaMetrics API is internal-only; the opt-in ingest gateway is HTTPS-only with a private CA, a strong bearer token and strict schema validation.

## Internet surface

Only **UDP 51820** should be forwarded from the router to the Pi. DNS (TCP/UDP 53), SSH, Pi-hole (8080), HomeAway portal (8088), Grafana (3000), TLS telemetry ingest (8429), optional authenticated PC gateway HTTPS (8443), and wg-easy admin (51821) must NOT be forwarded. Published LAN web ports are bound to PI_LAN_IP, not wildcard. Docker-published ports can bypass some host firewall rules; verify router and IPv6 exposure separately. Keep Docker and host packages updated.

## Administration

wg-easy 15 requires `INSECURE=true` for HTTP without a reverse proxy. This is intentionally bound to `127.0.0.1:51821` on the Pi and should be accessed **only through SSH local port forwarding**. SSH encrypts the HTTP traffic between the administrator and the Pi; the wg-easy login and optional 2FA still protect its UI. Do not rebind this port to `0.0.0.0` or PI_LAN_IP. A local process/root user on the Pi can access it.

Pi-hole's authenticated admin UI is HTTP on a **trusted private LAN/VPN**. Credentials cross that LAN without application-level encryption. If your LAN is untrusted or you want remote admin outside an encrypted VPN, enable trusted HTTPS first. Do not treat this as public-ready.

## Portal

The portal serves static assets and read-only DNS/TCP checks. It holds no passwords, private keys, and no Docker socket; it has no mutation routes, runs as non-root with dropped capabilities, a read-only filesystem and no-new-privileges. It shows service responsiveness, **not peer handshake proof** or complete Pi health. Treat it as a convenience, not a security monitor or controller.

## Docker and secrets

The wg-easy container needs NET_ADMIN, SYS_MODULE and IP-forwarding sysctls to manage its tunnel. These are privileged capabilities; do not place unknown workloads in the same network or mount the Docker socket to a dashboard. The Pi-hole password is a Docker Compose secret mounted into Pi-hole. `.env` contains network topology; peer keys, passwords and backups should stay offline from Git.

Do not enable Watchtower or unattended container upgrades for DNS/VPN. Review advisories, back up and update in a maintenance window.

## Threats and limits

- Stolen iPhone/configured peer: revoke peer in wg-easy; regenerate any exposed keys. WireGuard encryption does not prevent authorized peers from reaching allowed LAN hosts.
- Compromised Pi or Docker daemon: VPN keys and DNS activity can be disclosed. Docker group membership effectively grants root-like capabilities.
- Open resolver risk: Pi-hole v6 uses `FTLCONF_dns_listeningMode=ALL` for bridge networking; **never** forward public port 53. Bind the host port to the Pi's private address, verify WAN isolation.
- Home Wi-Fi trust: people on the same private LAN can see portal status and, unless TLS is added, HTTP admin traffic. Use SSH/VPN for administrative tasks.
- Router/ISP/CGNAT: WireGuard needs reachable UDP unless an alternate relay architecture is added. Domain names do not substitute for inbound connectivity.
- DNS bypass: DoH, iCloud Private Relay, IPv6 DNS RA and secondary resolvers can reduce Pi-hole coverage.
- Only one Pi: hardware, power, SSD, OS or DNS outages are possible. Router DNS rollback should be practiced.

No unattended AI operation is authorized. Any agent modifying configuration should propose a PR, review diff/CI, and require operator approval for actual deployment, router changes, credential use and peer revocation.

## Pi host exporter boundary

`pi-node-exporter` reads `/proc` and `/sys` **read-only** and exposes
Prometheus metrics only on the private Docker `infra` bridge. It does not
mount the host root, join the host network/PID namespace, read secrets, inspect
Docker's Unix socket, accept remote commands or publish port 9100.
As a tradeoff, container-network statistics are intentionally excluded,
and Pi OS root filesystem free space is not inferred from container overlays.
VictoriaMetrics provides reliable free bytes for the filesystem holding its
data directory; a real thermal zone may not exist on every kernel.

## Optional authenticated computer-control gateway

The separate [PC control runbook](PC-CONTROL.md) covers the private Caddy HTTPS endpoint on TCP 8443, the local Authelia password + second-factor challenge for an `admins`-group user, and the isolated `pc-control` container. Caddy is the only LAN-exposed ingress for this feature: neither Authelia nor the computer controller publishes a port. Caddy's internally issued root CA must be explicitly trusted on every client. **Never forward port 8443 to WAN** or use untrusted TLS exceptions.

The browser controller has no Pi/Windows SSH private keys and no Docker socket. Its restricted RPC reaches a group-protected Unix socket; only the non-root host agent owns the allowlisted WOL/Windows SSH actions. Requests require POST, same-origin/CSRF checks and confirmation for destructive actions; returns acknowledge requested actions rather than proving shutdown. The original :8088 portal continues to be read-only and may be viewed by any LAN/VPN client. Authelia session authentication does not protect that standalone status page.

Grafana's separate externally shared metrics URL is also a bearer-style read-only link. It is accessible without the Authelia login to anyone who has the URL **and** LAN/VPN reachability. Grafana administrative credentials remain required for dashboard edits and sharing.
