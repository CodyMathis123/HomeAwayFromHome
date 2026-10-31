# First real deployment and side-by-side validation

This is the **operator handoff** after repository CI. Nothing in this document is executed by GitHub Actions on your actual Pi or Windows computer. Keep both PRs in draft and the existing PC fan-control application installed until tests pass.

The steps are ordered to avoid interrupting household DNS or the radiator fans. **Do not wipe the existing Pi or install the SensorDash preview MSI without checking current backups first.** When the fan-control service stops/restarts, the firmware-default fan behavior should be confirmed ahead of time.

## Stage 0 — What to have available

- Raspberry Pi 4, 8 GB, Ethernet, 64-bit Raspberry Pi OS Lite and preferably a USB SSD; access from an admin desktop using SSH.
- Raspberry Pi address **reserved in the router**, the home subnet in CIDR notation, and a VPN hostname you control that resolves to your WAN IP. Decide whether your ISP connection allows inbound UDP 51820 (CGNAT can prevent it).
- A backup of the existing Pi and SensorDash settings, ideally on a different disk.
- The current Windows SensorDash MSI/branch remains installed and active until you explicitly choose a maintenance window. No credentials should go into GitHub.

## Stage 1 — Prepare Pi OS and Docker

1. Image a spare SSD or card with **Raspberry Pi OS Lite 64-bit** using Raspberry Pi Imager. Set a named user, SSH key and Ethernet. Keep the old Pi storage unplugged and available as rollback until validation.
2. Boot the Pi and determine its real IPv4 address using `hostname -I`; confirm `uname -m` reports `aarch64`. Reserve that IPv4 in the router. Update packages and reboot.
3. Install Docker Engine and Compose plugin from the official [Docker installation guide for Debian](https://docs.docker.com/engine/install/debian/). Confirm `docker info` and `docker compose version`.
4. Clone the **feature branch**:

   ```bash
   git clone --branch feature/pi4-home-infrastructure \
     https://github.com/CodyMathis123/HomeAwayFromHome.git
   cd HomeAwayFromHome
   bash scripts/init.sh
   nano .env
   ```

5. Set `PI_LAN_IP` to the Pi's real reserved LAN IPv4; `LAN_CIDR` to the home subnet; `VPN_ENDPOINT` to your real public DNS hostname and confirm `VPN_CIDR` does not overlap the LAN or `10.42.42.0/24`. The example `192.168.1.10` is **not** assumed to be your address. Run:

   ```bash
   bash scripts/doctor.sh
   docker compose config -q
   ```

   Fix all conflicts with port 53 before starting Pi-hole. If another service owns it, identify that service first rather than disabling it blindly.

## Stage 2 — Bring up only DNS, VPN and portal

```bash
docker compose up -d --build pihole wg-easy portal
docker compose ps
bash scripts/smoke.sh
```

From another LAN computer, verify a DNS query directly against the Pi:

```bash
nslookup example.com PI_LAN_IP
```

Log in to `http://PI_LAN_IP:8080/admin/` using the **secret from** `secrets/pihole_password` (read only on the Pi; do not share it). Pi-hole must show the query. The read-only portal is at `http://PI_LAN_IP:8088/`.

**Keep the router's household DNS unchanged** until direct LAN DNS works.

## Stage 3 — Configure WireGuard privately

From a separate admin desktop terminal:

```bash
ssh -L 51821:127.0.0.1:51821 PI_USER@PI_LAN_IP
```

Open `http://127.0.0.1:51821` locally, complete wg-easy's v15 wizard with a unique admin login, 2FA where available, the public `VPN_ENDPOINT` and external UDP port 51820. Set DNS to the Pi's LAN IPv4 and create an iPhone peer. Review its AllowedIPs for split-tunnel home-LAN access. **Never publicly forward 51821, Pi-hole ports, Grafana or telemetry intake.**

Forward only router UDP **51820** to the Pi. With the iPhone on cellular and WireGuard enabled, verify an actual peer handshake using:

```bash
bash scripts/inspect-vpn.sh
```

Then test access to the Pi web interface and Windows RDP using their private LAN addresses. A VPN icon or responding wg-easy UI does not prove that LAN routing and DNS work.

## Stage 4 — Install optional Grafana stack on Pi

```bash
sudo bash scripts/init-telemetry.sh
sudo mkdir -p data/grafana data/victoria-metrics
sudo chown -R 472:472 data/grafana
docker compose --profile metrics up -d --build grafana telemetry-ingest
docker compose --profile metrics ps
```

At `http://PI_LAN_IP:3000`, log in as `admin` with the generated `secrets/grafana_admin_password` value. Check the **HomeAway Metrics** datasource and the **SensorDash · Hardware Telemetry**, **SensorDash · Cooling Systems**, and **SensorDash · Fan & Pump Electricity** dashboards. Initially, the charts are correctly empty.

Run `bash scripts/verify-telemetry.sh` now: it should check Grafana and TLS intake but report **no recent Windows data** (nonzero exit), as the exporter isn't enabled yet.

## Stage 5 — Safely configure the new Windows exporter

First deploy the Pi ingestion updates from HomeAwayFromHome, including the new Storage and Events dashboard schemas. Do not install the new Windows MSI before that step.

Make a backup of the current C:\\ProgramData\\SensorDash directory (settings, SQLite, approved profiles, and exporter cursors), keep a known-good installer, and check how radiator fans behave if the Windows service is stopped. The Pi never controls those fans.

From the *existing* local SensorDash web UI, open **Settings → Pi telemetry export**. Enter the HTTPS Pi receiver URL (for this installation: https://192.168.1.43:8429/ingest), the stable host label main-desktop, and enable export. Enter the Pi-issued bearer token and public telemetry CA PEM if they are not already installed. These sensitive values are written to Windows by the collector but never returned by a GET; do not post either the token or CA signing private key in chat. Leave both credential fields empty to preserve already installed values.

Save. The form reports whether the token and CA are installed. A controlled restart of SensorDashCollector is currently required to apply a changed export configuration; the form deliberately cannot restart a privileged fan-controlling service. Confirm the old approved fan profile is present, observe real radiator PWM and RPM and the fixed DDC pump tach before/after the supervised restart.

## Stage 6 — Prove PC → Pi data flow

From the Pi project directory:

```bash
bash scripts/verify-telemetry.sh walnut-desktop
```

It must report recent `sensordash_reading_value` samples. If cooling systems are configured, expect `sensordash_component_info_value` and `sensordash_cooling_binding_value`. If valid electrical instrumentation or calibration exists, expect `sensordash_accessory_power_value`.

Compare the same **30-minute interval** in local SensorDash and Grafana: raw CPU/GPU temperatures, true peak, radiator RPM, PWM, and fan/pump wattage source. Test an alias rename and a component sensor reassignment; old labels should become inactive without losing original raw history. Then briefly **stop only the telemetry receiver**, not SensorDash, to check queued delivery after reconnect. Hardware fan control must remain safe throughout.

Do not change household DHCP DNS during this validation. Leave SensorDash hardware control and the Pi viewer operating through at least one ordinary gaming session and one sleep/resume cycle, and compare current readings and gaps in Grafana to Sensor Explorer and hardware feedback.

## Stage 7 — DNS cutover and rollback

Only after LAN DNS and VPN tests pass, change your router's DHCP DNS from its current value to the Pi LAN IPv4. Renew one client lease, test Pi-hole query logs, then test the household. Note that if the Pi goes offline, DNS may fail; public secondary DNS can preserve connectivity but may bypass filtering.

To roll back DNS, restore the router's previous DHCP DNS. To roll back SensorDash telemetry only, disable Pi export in SensorDash Settings and restart the collector during a safe window during a safe fan-control maintenance window; the current local dashboard and SQLite history remain.

### Definition of done

- Pi DNS and WireGuard proven from both LAN and iPhone cellular.
- Grafana login and internal VictoriaMetrics datasource functional.
- End-to-end HTTPS Windows → Pi telemetry with real sensor data.
- Fan curves/pump behavior unchanged during network outage, Pi reboot and Windows sleep/resume.
- No secret leaks, public management ports or unexpected data gaps.
- Storage retention and disk usage understood after a day or two of normal operation.

**If those tests pass**, future iterations can compare existing historical energy calculations with the new archived derived-power samples and consider phasing out duplicate Windows charts. Until then, do not merge or delete the old visualization code.
