# HomeAway Grafana visual design

The dashboards use **native Grafana 13.3 panels**, not custom CSS injection, a third-party theme plugin, or a patched Grafana image. This keeps upgrades safer and more predictable.

## Visual conventions

- Transparent Markdown section headings, top-row status tiles, live charts first, dense reference tables after the trends.
- A one-hour default time window, with a navigation dropdown for switching between the four HomeAway dashboards while preserving the selected range and relevant filters.
- Blue for coverage; teal for fan/connection state; amber for temperature/PWM; violet for electricity. Red/green are reserved for actual OFF/ON and DOWN/UP states, not inferred health.
- Preserve missing data during sleep gaps. Use explicit units and the latest/max legend. Show zero baselines for RPM, temperature, power and flow and a 0–100 scale for PWM.
- Power provenance remains measured/calibrated/curve/rated. Past revisions remain distinct. No fabricated or double-counted watt-hours.

## Why the underscores appeared

SensorDash's TelemetryLineProtocol.Label serializes an ASCII-restricted metric-label alphabet. Spaces and other unsupported characters become underscores. Stable raw sensor IDs and historical series cannot be renamed casually without breaking joins and comparisons.

Charts now additionally normalize the *human-facing label values* using query-time label_replace before Grafana interpolates the series legend; this avoids relying on display-name transformation behavior alone. Grafana's built-in renameByRegex transformation also replaces underscores globally in any remaining visible chart-series names. Reference tables use query-time label_replace on human-facing name, alias, device, component_name and system_name labels; stable sensor, system, host and component IDs are never modified. This is best-effort formatting: a literal underscore cannot be distinguished from a former space. A future display-string transport can preserve exact original names independently of stable IDs.

## Themes and branding

Grafana OSS supports native Light and Dark interface themes. Use the account avatar → Profile → Preferences → Theme. Individual user settings override higher-level defaults. Extra experimental theme choices may not be available on this instance.

Full custom CSS/logo/login branding is different from ordinary theming, and most official custom branding requires Grafana Enterprise or paid Grafana Cloud. Avoid unsupported patches to the production Grafana container. A SensorDash-like dedicated read-only frontend on top of VictoriaMetrics is a possible separate project, not required for this monitoring stack.

## Apply on the Pi

After CI succeeds, check the existing feature-branch Git status, then pull its latest commit:

    cd ~/HomeAwayFromHome
    git status --short
    git pull --ff-only origin feature/pi4-home-infrastructure

The Grafana file provider rescans the mounted dashboards roughly every 30 seconds. Refresh Grafana; no Docker container restart or Windows fan-control interruption should be necessary. A time range already chosen in your browser can stay selected until you change it.

Review Hardware, Cooling, Electricity and DNS/VPN. SensorDash still owns fan controls, mappings, calibrated wattages and authoritative raw hardware samples. Empty coolant, flow and ambient panels are expected until real probes are installed and assigned.

## SensorDash-inspired unified Overview

A new read-only **SensorDash · Overview** recreates the shape of the Windows Overview: four featured sensor cards with current-history sparklines (CPU/GPU temperature, optional storage temperature and radiator fan RPM), recorded CPU/GPU temperature peaks over the selected range, separately reported CPU/GPU power, trend charts, source-and-revision-aware fan/pump electrical estimates, and a live hardware catalog.

CPU/GPU cards show their **highest available exported sensor reading**; they intentionally do not label that value package/core/hotspot unless a specific sensor is assigned. Power cards show the highest channel, never the sum of potentially overlapping rails. The storage card remains empty when the collector does not export a matching sensor.

Fan/pump electricity remains labeled by measured/calibrated/curve/rated source and configuration revision. No guessed aggregate wattage is presented when revisions overlap, and no watt-hours are extrapolated during sleep gaps. Windows SensorDash remains the owner of fan control, alias editing, energy integration and local sensor retention.

The Overview is an additional Grafana dashboard, not a replacement for the detailed Hardware/Cooling/Electricity views. It appears in the Other HomeAway views menu automatically.

## SensorDash storage and events dashboards

The **SensorDash · Storage** view identifies drive sensors via the canonical `device_type=Storage` metadata exported by Windows, not guessed `/hdd` sensor names. It visualizes physical temperatures, utilization/throughput when available, raw data channels with units unspecified until validated, and three aggregate Windows local SQLite buffer file sizes. The local database remains necessary as the export retry spool; Grafana/VictoriaMetrics own retention, disk analytics and the long-term historical view. No local Windows Storage or History Storage UI is retained.

**SensorDash · Events** visualizes actual threshold-crossing and recovery timestamps as isolated event impulses, plus counts over the selected interval. Free-form event messages remain local to SensorDash. This is deliberately not a remote control channel; threshold settings and event ownership stay in Windows. Both dashboards are initially empty until the Windows exporter update deploys and Pi telemetry intake accepts the new whitelisted schemas.

### Historical storage diagnostics

The Storage dashboard now includes VictoriaMetrics' internal data-store size, filesystem free space, on-disk component breakdown and ingestion throughput. The VM scraper collects its own `/metrics` on the **internal Docker bridge only**; no host/LAN metric port is introduced. The selected-time-range sample count by sensor comes from real retained samples in VictoriaMetrics. It is *not* an estimate of bytes per sensor, because VM compressed structures are shared and allocated collectively. This replaces the old Windows History Storage visualization while keeping Windows SQLite as a retry spool. For these metrics to start on an existing Pi, recreate only `victoria-metrics` with the updated scrape file; its bind-mounted history directory persists.

## Pi host health and ingest freshness

**HomeAway · System Health** uses upstream Prometheus Node Exporter v1.12.1
instead of a second custom Linux collector. Node Exporter runs with no Docker
socket, privileges, published port, secrets, host-network or host-PID access.
Only `/proc` and `/sys` are mounted read-only; the default collector set
is disabled so the Pi 4 only exports CPU, RAM, load, kernel thermal zone,
diskstats, uname and basic kernel counters.

The Grafana dashboard displays Pi CPU busy, a labeled CPU/SoC thermal zone
(if exposed by the running kernel), MemAvailable-based RAM use, host uptime,
VM data-volume free bytes, VM stored bytes, four internal scrape statuses,
disk read/write activity, and real SensorDash sample observations. It does
**not** claim to display Pi OS root filesystem free bytes when VictoriaMetrics
may be on a separate SSD. Disk I/O is restricted to recognizable physical
disk identifiers; an empty plot may mean unsupported kernel naming, not zero
I/O. We deliberately do not show misleading container-network counters
as Pi host traffic.

The Windows freshness tile uses the last observed sample's original timestamp
over a 24-hour lookback. If Windows sleeps, shuts off, the LAN is down, or the
exporter cannot deliver, the value ages; **none of those conditions can be
distinguished from this metric alone**. If no samples exist within 24 hours,
the tile says no data, not a fabricated zero. No alerts or control commands
are provisioned by this change.

[Source: Prometheus Node Exporter container guidance](https://github.com/prometheus/node_exporter#docker).
