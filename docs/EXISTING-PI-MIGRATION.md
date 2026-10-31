# Raspberry Pi 4: existing-device migration and live cutover

This runbook documents the **October 31, 2026** headless migration of an existing Pi 4 (8 GB) from **32-bit Raspbian Bullseye on a 128 GB microSD** to **Raspberry Pi OS Lite 64-bit / Debian 13 (Trixie)**, while retaining the original PC-control (`pcctl`) workflow. The current installation is **not** a claim of complete VPN/DNS end-to-end validation: initial service health/smoke checks passed, but iPhone cellular LAN routing, Pi-hole DNS query coverage, and household-wide eero DHCP DNS cutover still require explicit tests.

This is a **destructive operating-system reinstall**, not a safe in-place OS upgrade. Do not follow it on a live DNS/VPN server until an independent recovery path and off-device backup are verified.

## Previously existing host

- Pi 4 Rev 1.4, 8 GB; 64-bit kernel (`aarch64`) but 32-bit userland (`armhf`), Bullseye 11.
- 128 GB microSD (`/dev/mmcblk0`), original root `/dev/mmcblk0p2`.
- Native Pi-hole v5 + Unbound, PiVPN/WireGuard UDP 51818, `pcctl`, and obsolete Minecraft services. Docker was installed but had no active containers; stale volumes were not retained.
- An eero gateway uses router-managed DHCP. The Pi retains its router DHCP reservation. **Keep independent working public DNS on eero during initial testing.**
- Pi-hole, PiVPN, Unbound and Minecraft were intentionally replaced or discarded. `pcctl` was intentionally preserved.

## 0. Back up *off the Pi*

From a trusted Windows machine, capture a verified tar archive (and SHA-256) before erasing the microSD. The backup created during this migration contained:

- `/etc/pcctl.conf`, `/usr/local/bin/pcctl`, `/usr/local/bin/pcctl-dispatch`
- `/home/pcctl/.ssh/authorized_keys`, `windows_ed25519`, `windows_ed25519.pub`, `known_hosts`
- Pi-hole configuration and SQLite snapshots, WireGuard/PiVPN files, Unbound settings, relevant network/SSH/systemd configuration and package inventory.

**Do not restore entire `/etc/ssh`, `/etc/network`, user home directories or old systemd settings onto the new OS.** The archive contains credentials and must be stored encrypted/off-device, never committed to GitHub or pasted into tickets.

Also record the **router's** current DHCP DNS settings, reservation, external VPN hostname, and UDP port forwarding; a Pi filesystem backup does not back up router state.

## 1. Use temporary USB rescue boot

1. With Raspberry Pi Imager on Windows, write Raspberry Pi OS Lite **64-bit** to an expendable USB flash drive. Configure a username/password or trusted public key, **enable SSH**, and connect the Pi via Ethernet. No display required.
2. On the old Pi, confirm `vcgencmd bootloader_version` and `sudo rpi-eeprom-config`. Its original `BOOT_ORDER=0x1` was SD-only. Use `sudo rpi-eeprom-config --edit` to set `BOOT_ORDER=0xf14` (USB first, SD fallback); review the pending bootloader change before reboot.
3. With the rescue USB attached, reboot and SSH into the USB OS. Its initial boot may take longer; SSH host keys will differ. Verify any new SSH fingerprint through a trusted path before replacing a known-host entry.
4. **Do not image anything until the storage roles are verified:**

   ```bash
   uname -m
   dpkg --print-architecture
   findmnt -n -o SOURCE,FSTYPE /
   lsblk -o NAME,SIZE,TRAN,FSTYPE,MOUNTPOINT
   ```

   The successful rescue environment reported `aarch64`, `arm64`, live root `/dev/sda2` (USB), and an **unmounted** microSD `/dev/mmcblk0`. Device names **must be verified**, never assumed.

## 2. Reimage the microSD while booted from USB

Download a trusted official Raspberry Pi OS Lite arm64 image to the USB filesystem and verify the image/integrity before writing. This migration used a *separately reviewed, interactive helper* that required the exact phrase `ERASE /dev/mmcblk0`, checked that `/` was on `/dev/sda2`, and refused to write if the card was mounted. It is not part of this repository; **do not copy an unreviewed `dd` command into the shell**.

The helper wrote the new OS image only to `/dev/mmcblk0`, configured Trixie first-boot cloud-init with the intended user/SSH/password hash, hostname, and Ethernet DHCP, and did **not** reboot automatically. Image archive integrity checking was performed, but the locally printed SHA-256 alone was **not independent publisher-signature verification**.

After the installer reported success: `sudo shutdown -h now`, disconnect power after shutdown, remove the rescue USB, leave the microSD inserted, then restore power. Verify from SSH:

```bash
hostname
uname -m
dpkg --print-architecture
findmnt -n -o SOURCE,FSTYPE /
```

Observed new system: hostname `homeaway`, `aarch64`, `arm64`, root `/dev/mmcblk0p2`. Preserve the rescue USB unchanged until recovery is tested.

## 3. eero/Pi 4 ARP fault and observed workaround

After both USB and SD boot, the Pi had its proper LAN address and default route but couldn't resolve the gateway through IPv4 ARP (`ip neigh` showed gateway `INCOMPLETE`); local desktop SSH worked. Manually supplying the router MAC restored internet access. Packet capture confirmed that Pi ARP requests left `eth0` but replies were absent. ARP to the Windows desktop worked. The wiring was **fiber/ONT -> gateway eero -> unmanaged switch -> Pi/desktop/other eero**.

The operator ran:

```bash
sudo iwconfig wlan0 power off
```

Immediately afterward, broadcast `arping` to the gateway returned 5/5 replies. After deleting the temporary static neighbor entry, normal ARP, gateway ping and public internet ping worked. Ethernet EEE was observed enabled/active at that point. **This is a machine-specific correlation, not proof that Wi-Fi power saving universally causes wired ARP failures.**

To persist the *observed successful setting*, a local oneshot systemd service was installed:

```ini
# /etc/systemd/system/wifi-powersave-off.service
[Unit]
Description=Disable Raspberry Pi WiFi power saving
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/iwconfig wlan0 power off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

Enable and verify:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wifi-powersave-off.service
iwconfig wlan0
sudo arping -b -I eth0 -c 5 192.168.1.1
ping -c 3 1.1.1.1
```

Reboot-test before treating this as durable. If ARP breaks again, inspect the actual MAC and topology; **do not hard-code a gateway MAC as a permanent default**. The current stable network is wired Ethernet; a different network or a connected WLAN requires re-evaluation.

## 4. Docker and the core Compose deployment

Install Docker Engine, CLI, containerd and the Compose plugin from Docker's **official Debian 13 (trixie) ARM64 repository**; verify `sudo docker version`, `sudo docker compose version`, and `sudo docker run --rm hello-world`. A Docker group is effectively root privilege; using `sudo docker` avoids broadening account privileges.

```bash
git clone -b feature/pi4-home-infrastructure https://github.com/CodyMathis123/HomeAwayFromHome.git
cd HomeAwayFromHome
bash scripts/init.sh
nano .env
bash scripts/doctor.sh
sudo docker compose config -q
sudo docker compose up -d --build pihole wg-easy portal
sudo docker compose ps
bash scripts/smoke.sh
```

On the observed LAN, `PI_LAN_IP=192.168.1.43` and `LAN_CIDR=192.168.1.0/24`. The subnet and address are **not portable**. Keep `UPSTREAM_DNS` independent from the new Pi-hole until it is validated.

**Important:** `VPN_ENDPOINT` and `VPN_CIDR` in `.env` are currently preflight/configuration-documentation values; they **do not configure** wg-easy's internal interface and clients. The v15 UI/wizard is authoritative. The actual new server initially used `10.8.0.0/24` even while a different `VPN_CIDR` was entered in `.env`. Align the documented variable with the actual configured subnet.

### Raspberry Pi nftables compatibility

On Pi kernel `6.18.50+rpt-rpi-v8`, stock wg-easy v15.5.0 repeatedly crashed while `wg-quick up wg0` attempted `iptables (legacy)` NAT. The `ip_tables` module was absent. The tested fix selected `iptables-nft` and `ip6tables-nft` **inside the container** via `Dockerfile.wg-easy-nft`, leaving capabilities, mounts and ports unchanged. The Compose definition now builds the custom pinned image.

After deployment:

```bash
sudo docker compose ps
sudo docker compose logs --tail=60 wg-easy
sudo docker compose run --rm --no-deps --entrypoint sh wg-easy -c 'iptables --version; ip6tables --version'
```

Both variants should report `nf_tables`. On real hardware, wg-easy recovered, became `healthy`, and logged `Wireguard Interface wg0 started successfully` plus `Firewall rules applied successfully`. **Container health is not proof of VPN handshake or LAN routing.**

### LAN and VPN validation

- Pi-hole was healthy on `PI_LAN_IP:53` with web admin at `:8080/admin/`; portal `:8088` passed its smoke tests.
- Keep the eero's public DNS in place initially. Test one LAN client with `nslookup example.com PI_LAN_IP` first.
- Access wg-easy admin **only** through `ssh -N -L 51821:127.0.0.1:51821 USER@PI_LAN_IP`, then `http://127.0.0.1:51821`.
- Configure a *new* wg-easy client and endpoint hostname in v15 UI; old PiVPN peer configurations are not an automatic wg-easy import.
- Only WireGuard UDP 51820 should be forwarded on the router; remove the old 51818 forward at cutover. Don't forward admin/DNS/metrics/web ports. Validate the public DNS hostname and WAN reachability.
- From iPhone **on cellular**, verify an actual handshake via `sudo docker compose exec -T wg-easy wg show`; then test Pi portal, Pi-hole DNS **query log**, LAN targets and desired split tunnel. iPhone showing “connected” alone is not enough.
- Only after those checks should the eero DHCP DNS be changed for the household. Document rollback to public resolvers.

## 5. Selectively restore pcctl

The old Pi's restricted `pcctl` installation was preserved in the off-Pi archive. Restore only `/etc/pcctl.conf`, `/usr/local/bin/pcctl`, `/usr/local/bin/pcctl-dispatch`, the dedicated `pcctl` account, and its four key-related `.ssh` files. **Do not overwrite the new OS's SSH daemon configuration or the main login account's `authorized_keys`.**

The migration used a selective staging/restoration helper external to this repository; its installation succeeded. Security requirements:

- The dedicated `pcctl` account remains unprivileged and outside the Docker group.
- iPhone SSH authorization retains restrictive `restrict,command="/usr/local/bin/pcctl-dispatch"` forced-command options.
- `/home/pcctl/.ssh/windows_ed25519` remains private (mode `0600`), correct owner, and never appears in Git/logs.
- Existing Windows restricted SSH target account and access restrictions remain intact.

Verify on Pi without issuing destructive PC commands:

```bash
sudo -u pcctl /usr/local/bin/pcctl work status
sudo -u pcctl /usr/local/bin/pcctl gaming status
```

Then test the original iPhone shortcut **over the new VPN**, carefully checking that it targets the intended Windows PC. Successful restoration of files does not, by itself, prove all remote shortcuts work.

## 6. Subsequent work, not yet validated

1. Real cellular WireGuard handshake + split-tunnel routing + Pi-hole DNS coverage.
2. Household eero DNS cutover with a tested fallback.
3. Optional Grafana/VictoriaMetrics and SensorDash export side-by-side, **without changing local fan control**. While running from microSD, observe write volume/storage and set retention deliberately; USB SSD remains a future option.
4. Final end-to-end `pcctl` tests, reboot/power recovery, and consistent off-Pi backups for the new stack.

See [INSTALL.md](INSTALL.md), [LIVE-VALIDATION.md](LIVE-VALIDATION.md), and [OPERATIONS.md](OPERATIONS.md) for additional service specifics.
