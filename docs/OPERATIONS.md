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

## Optional Grafana and VictoriaMetrics

See [telemetry guide](TELEMETRY.md). Provisioning config and dashboards are version controlled. TLS certificates and tokens are generated locally and are not stored in Git. The metrics profile is opt-in:

```bash
sudo bash scripts/init-telemetry.sh  # only the first time; credentials never overwritten
docker compose --profile metrics up -d --build grafana telemetry-ingest
```

Grafana is LAN/VPN-only at `http://PI_LAN_IP:3000`. VictoriaMetrics never publishes its HTTP API, and SensorDash writes through the authenticated HTTPS receiver on `https://PI_LAN_IP:8429/ingest`. The private server certificate expires after 365 days and must be rotated, alongside updated Windows collector trust if the CA changes.

## Backup and restore

First run `sudo bash scripts/backup.sh --plan` to verify the Compose project and number of running containers **without any outage or archive**. Then, in a planned maintenance window, run `sudo bash scripts/backup.sh --offline`. It stops **all running containers labeled with this checkout's HomeAway Compose project**, including optional Caddy/Authelia, computer control, LLDAP and account broker services (if deployed), before archiving their data. Previously running containers are restarted in an exit trap even if the archive step fails; restart failures are reported as errors. **DNS, VPN, login and monitoring may all be temporarily unavailable.** Root-owned tar archives go to `/var/backups/homeawayfromhome/` mode 0600 with SHA-256 checksums.

The backup includes `.env`, `secrets/` and `data/` (including the account directory and Authelia SQLite if present).

**Phase 2 host-state coverage is NOT included:** This script does not archive `/etc/homeaway-pcctl/hosts.json`, `/home/pcctl/.ssh` and other host-side pcctl credentials/trust, `/var/lib/homeaway-pcinventory/` draft/retirement/audit metadata, or installed systemd unit/socket state. It stops only Compose containers; the independent host agents are not quiesced by it. A Compose backup alone cannot restore the active PC inventory and pairing guarantees to a replacement Pi. Plan an encrypted off-device host-state backup, safe snapshot coordination and a tested restore **before** relying on Phase 2 as disaster-recovery-ready. Do not copy these sensitive resources into the Git repository. See [roadmap](ROADMAP.md) and the Phase 4 planning spec. The project selection follows Docker Compose's resolved project name. **Any HomeAway service deliberately started under another Compose project or outside Compose is outside this script's stop scope**; do not claim a consistent snapshot of those services. After a new account-backend migration, verify an off-device encrypted backup and perform a supervised isolated restore; a successful archive is not by itself a recovery test.

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

The update script only updates core services; metrics stay opt-in. To update Grafana/VictoriaMetrics after a backup (intake image separately if changed):
```bash
docker compose --profile metrics pull grafana victoria-metrics
docker compose --profile metrics up -d grafana victoria-metrics
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

## Pi host and Windows telemetry freshness (optional monitoring profile)

The metrics profile includes a pinned **Node Exporter v1.12.1**, scraping the Pi's read-only
`/proc` and `/sys` from the Docker bridge. It is deliberately **not** in the host
network or PID namespace, has no LAN port, no Docker socket, no secrets and no
read-only mount of the host root. It exposes CPU activity, load, memory, kernel
thermal zones and disk activity. The actual filesystem containing VictoriaMetrics
is monitored separately using `vm_free_disk_space_bytes`. Its free bytes may
describe an attached SSD rather than the Pi OS microSD; never call it whole-Pi
free space.

Enable this exporter on an already-running Pi:

```bash
cd ~/HomeAwayFromHome
git status --short  # Review changes first; do not switch over local edits.
git fetch origin
git switch Production
git merge --ff-only origin/Production
sudo docker compose --profile metrics up -d pi-node-exporter
sudo docker compose --profile metrics up -d --no-deps --force-recreate victoria-metrics
sudo bash scripts/verify-pi-health.sh main-desktop
```

The VictoriaMetrics recreation reloads its scraper configuration but preserves
its existing bind-mounted history. It does not recreate Pi-hole, wg-easy or
the Windows collector. The new **HomeAway · System Health** dashboard is
provisioned from `grafana/dashboards/system-health.json`; the Grafana file
provider refreshes its dashboard files automatically.

The Windows *last observed sample age* is only available if there has been
at least one reading within the past 24 hours. A stale sample proves no newer
reading reached VictoriaMetrics, **not** that Windows crashed. Sleep, power off,
LAN outage, exporter authentication and collector failure require separate checks.

No remote alerting/notification policy is configured by this addition.
