# Raspberry Pi SSD SMART monitoring

This is an **explicitly opt-in** extension of HomeAwayFromHome's existing
node-exporter, VictoriaMetrics, and Grafana. It does **not** change SensorDash,
Windows fan control, DNS, VPN, or any public network ports.

## How the data moves

A five-minute systemd timer runs a root-owned host collector. The collector
identifies the SATA disk backing the Pi root filesystem (not a hard-coded
device path), queries read-only SMART JSON through smartctl, reads root
filesystem capacity through statvfs, and counts selected kernel/USB failures
since the current boot, including USB disconnect events. It atomically writes a sanitized Prometheus textfile.

Node-exporter runs unprivileged with a read-only bind mount of **only** the
textfile directory, not /dev, Docker, the host root, or the kernel journal.
The already-configured VictoriaMetrics pi-node scrape ingests those metrics.
Grafana adds a Raspberry Pi section to SensorDash · Drives & Retention.
Windows storage observations above it remain from SensorDash.

No serial, raw SMART JSON, disk path, firmware ID, IP address, or log line
is exported. No SMART self-tests or SSD writes are initiated.

## Deploy on the Pi over LAN SSH

Prerequisites: the Pi boots from its USB SATA SSD; smartctl -d sat -H -A
can read its SMART data; preserve the UAS workaround for this enclosure
(usb-storage.quirks=152d:a578:u).

~~~bash
cd ~/HomeAwayFromHome
git fetch origin
git switch Production
git merge --ff-only origin/Production
sudo apt-get install -y smartmontools
bash scripts/install-pi-ssd-monitoring.sh --plan
sudo bash scripts/install-pi-ssd-monitoring.sh --apply
bash scripts/install-pi-ssd-monitoring.sh --status
sudo cat /var/lib/homeaway-ssd-metrics/pi-ssd.prom
~~~

Confirm SMART collection_success equals 1. A failed read publishes 0 and
**omits** previous physical readings; it does not claim the SSD is healthy.

Then recreate **only** pi-node-exporter to mount the new textfile directory:

~~~bash
sudo docker compose --profile metrics up -d --no-deps --force-recreate pi-node-exporter
bash scripts/verify-pi-ssd-metrics.sh
~~~

The scraper runs every 30 seconds. Provisioned Grafana dashboards refresh
from mounted files; Grafana itself does not require a restart.

## Metrics and meaning

- homeaway_pi_ssd_collection_success: 1 = read OK; 0 = read failed;
  missing = no recent sample (not healthy).
- homeaway_pi_ssd_smart_passed: 1 = drive reports SMART PASS; 0 = failed;
  absent if unreadable. This is not a guarantee of future reliability.
- homeaway_pi_ssd_temperature_celsius: smartctl-decoded `temperature.current` in Celsius; raw attribute 194 is packed and must not be used as a direct Celsius value.
- homeaway_pi_ssd_retired_blocks, program_failures, erase_failures,
  uncorrectable_errors, reallocation_events, unexpected_power_losses:
  lifetime vendor SMART counters, not newly detected events.
- homeaway_pi_ssd_life_normalized: SandForce attribute 231 normalized
  value; **NOT an established percent of life remaining**.
- homeaway_pi_ssd_lifetime_writes_gib, lifetime_reads_gib: SandForce
  reported GiB counters (241/242). Grafana converts to bytes.
- homeaway_pi_ssd_power_on_hours: firmware hour count (attribute 9).
- homeaway_pi_ssd_last_attempt_timestamp_seconds: the exact Unix time of the
  most recent host collection attempt; panels show freshness. Collector output
  preserves sufficient numeric precision to prevent false 10+ minute sample ages.
- homeaway_pi_ssd_kernel_collection_success: 1 = kernel journal readable.
  No kernel events are published as zero when journal reading failed.
- homeaway_pi_ssd_kernel_events_since_boot: host-journal diagnostics
  grouped by kind, **gauges reset at reboot**, not continuous counters.
  Some USB events may reflect another USB peripheral.
- homeaway_pi_root_filesystem_*_bytes: actual Pi / filesystem usage,
  not VictoriaMetrics' own accounting metrics.

The main SSD dashboard deliberately omits the ambiguous normalized life indicator and shows observed writes over the past 24 hours instead. This is the range of the lifetime-write counter in that window (max minus min), not a precise moving write rate: firmware counters move in approximately 1 GiB increments, and periods with too few samples may show zero. Temperature history filters implausible values outside 0–125 °C; existing incorrect historical samples are retained but excluded from that chart. SMART fault series show lifetime baselines, not incidents newly detected during the displayed time range. Power-on hours and unexpected power losses remain available as exported metrics for technical queries.\n\nFor this older SandForce SSD, the self-test log is inconsistent, so no
self-test success/failure metrics are reported. The starting values of
3 retired blocks, 1 program failure and 1,566 unexpected power losses
are **historical baselines**; look for *changes* or actual collection failure.

## Verification and rollback

~~~bash
systemctl list-timers homeaway-pi-ssd.timer
sudo journalctl -u homeaway-pi-ssd.service --no-pager -n 30
bash scripts/verify-pi-ssd-metrics.sh
~~~

Rollback is explicit:

~~~bash
sudo bash scripts/install-pi-ssd-monitoring.sh --rollback
~~~

This stops/removes the host timer and service and retains the last textfile
for inspection. To also remove its mount and Grafana panels, revert the
Git commit and recreate only node-exporter. No Pi-hole, VPN, identity
service, or Windows SensorDash restart is required.
