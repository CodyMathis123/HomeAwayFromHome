# Decisions (2026-10-31)

1. **Single Raspberry Pi 4 / 8 GB; no new always-on VPS or server.** Pi-hole and wg-easy provide existing hardened functionality rather than reimplementing DNS/VPN.
2. **Docker Compose + a local .env and secrets.** Declarative, reviewable changes; sensitive runtime state stays outside Git. GitHub CI validates structure but never deploys home hardware.
3. **Pin release versions:** Pi-hole `2026.10.0`, wg-easy `15.5.0`; manual image updates only. Grafana 13.3.0 and VictoriaMetrics v1.155.0 run only in the opt-in metrics profile. Validate upgrades against release notes.
4. **Network separation:** only WireGuard UDP port intended for router forwarding; Pi-hole binds private host IP, wg-easy admin binds 127.0.0.1 and uses SSH tunnel. No admin login over public internet.
5. **Keep router DHCP.** Pi-hole handles DNS, not DHCP. Cutover one client at a time. DNS resilience on a single Pi is limited.
6. **A tiny custom read-only status portal.** Python stdlib and static assets, no Docker socket, no admin tokens. Its checks explicitly disclose when they only prove an HTTP or DNS listener.
7. **Grafana + VictoriaMetrics optional.** Grafana owns historical visualization, while SensorDash retains collection, cooling topology and physical control. A small TLS-token intake publishes only a restricted metric schema; no direct VM port is exposed.
8. **Offline, consistent backups.** Data includes keys and must be encrypted off-device. Prefer a controlled brief outage over silently copying live databases in an inconsistent state.
9. **Review before applying.** GitHub PRs and tests, separate operator-controlled install/update, and a clear router DNS rollback plan.
