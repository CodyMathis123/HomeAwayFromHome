# Operations and disaster recovery

## Everyday commands

```bash
docker compose ps
docker compose logs --tail=100 pihole wg-easy portal
bash scripts/inspect-vpn.sh
bash scripts/smoke.sh
bash scripts/doctor.sh
```

Portal: `http://PI_LAN_IP:8088`. Pi-hole: `http://PI_LAN_IP:8080/admin/`. WireGuard UI through SSH: `ssh -L 51821:127.0.0.1:51821 user@PI_LAN_IP`, then open `http://127.0.0.1:51821`.

## Optional Uptime Kuma

```bash
docker compose --profile monitoring up -d uptime-kuma
```

First visit `http://PI_LAN_IP:3001` and create a strong administrator password. Good initial monitors:
- DNS Record monitor querying Pi-hole on port 53 (verify using a client on the same Docker network).
- HTTP portal `http://portal:8080/healthz` (only proves portal process).
- HTTP Pi-hole `http://pihole:80/admin/` (UI reachable, not full DNS).
- TCP `wg-easy:51821` (admin listener reachable, **not a VPN handshake**).

For actual VPN availability, also periodically verify a real peer handshake and LAN reachability from cellular. Do not add a Docker socket to Uptime Kuma by default.

## Backup and restore

Run `sudo bash scripts/backup.sh --offline`. It stops **currently running stack services** briefly for consistent SQLite and WireGuard data, then restarts them in a trap even if archive creation fails. This interrupts DNS and VPN during the backup window; schedule it deliberately. Root-owned tar archives go to `/var/backups/homeawayfromhome/` mode 0600 with SHA-256 checksums.

**The archive contains WireGuard private keys, peers, Pi-hole admin password, DNS query history and .env. Encrypt it and move it off the Pi.** A backup exclusively on the Pi will not save you from an SSD failure.

Example off-device encrypted copy (requires an independent age recipient/key management setup):
```bash
sudo age -r 'age1YOUR_BACKUP_RECIPIENT' -o /safe-location/backup.tar.gz.age /var/backups/homeawayfromhome/backup-YYYYMMDDTHHMMSSZ.tar.gz
```
Do not invent or commit private encryption keys. Test decryption/restore with a disposable machine or spare storage.

**Manual restore onto a clean host:**
1. Restore Pi OS, install Docker/Compose and clone this repository.
2. Validate the archive: `sha256sum -c backup.tar.gz.sha256`; `tar -tzf backup.tar.gz`. Only use trusted archives.
3. With the stack **stopped**, extract to the project root as root so `.env`, `secrets/`, and `data/` retain file ownership and permissions; do not overwrite a live deployment. Review settings against the new host IP, current domain, Docker bridge and WAN rules.
4. Run `bash scripts/doctor.sh`, `docker compose config -q`, start core services, and test both DNS and an existing VPN peer. Check update compatibility before changing image versions.
5. Restore router DHCP DNS last. Revoke/regenerate VPN peer keys if the archive was ever exposed.

## Updates

Pin image tags in Compose. Read official release notes and security advisories, review diff, then:
```bash
sudo bash scripts/backup.sh --offline
bash scripts/update.sh --apply
bash scripts/smoke.sh
```

The update script only updates core services; monitoring stays opt-in. To update Uptime Kuma after a backup:
```bash
docker compose --profile monitoring pull uptime-kuma
docker compose --profile monitoring up -d uptime-kuma
```

Avoid blind watchtower updates. Review `docker compose logs --tail=150` and test on a noncritical client before re-enabling household-wide DNS.

## Diagnosing failures

- **Pi-hole won't start:** `sudo ss -luntp | grep ':53'`, `docker compose logs pihole`; stop an old DNS service *only if identified*. Check Pi IP exists on host.
- **DNS works on Pi but not on clients:** router DHCP DNS settings, client IPv6/DoH override, LAN isolation, Docker published host IP, and firewall.
- **VPN handshakes absent:** DNS A record, public WAN IP vs CGNAT, router UDP forwarding to static Pi IP, `docker compose logs wg-easy`, `wg show`.
- **VPN connects but no LAN/RDP:** peer AllowedIPs, Windows Firewall, duplicate subnets (phone's visited LAN vs home LAN), routing/NAT, and RDP service.
- **Pi-hole DNS through VPN fails:** peer DNS address, tunnel route for PI_LAN_IP, UDP/TCP port 53, container-to-host reachability; confirm Pi-hole query log.
- **Portal says manager reachable:** this only indicates TCP 51821 listening, *not* a WireGuard tunnel. Inspect `wg show`.

## Extensibility

This repo is an infrastructure contract, not a monolithic platform. Add service profiles and tests, keep attack surface small, require separate least-privilege authentication for any web-admin automation, and use existing native APIs carefully. The portal can grow read-only metrics without access to Docker or VPN private keys.
