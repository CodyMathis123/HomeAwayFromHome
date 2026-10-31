# Cooling-system metadata in Grafana (milestone 2)

The [Windows exporter branch](https://github.com/CodyMathis123/SensorDash/tree/feature/telemetry-grafana-export) now synchronizes a **read-only catalog** of discovered sensors and each configured Cooling System's component-to-sensor bindings. This is *metadata*, not a second hardware poller, control interface, or cooling configuration database. SensorDash still defines all sensor IDs, names, aliases, cooling system roles, fan/PWM mappings and approvals.

## What gets sent

The original raw metric stays exactly the same:

```text
sensordash_reading,host=main-desktop,sensor=/amdcpu/0/temperature/0,kind=Temperature value=54.0 1793450400000
```

Additional info gauges are synchronized about every 120 seconds, and immediately after configuration changes:

```text
sensordash_sensor_info,host=main-desktop,sensor=/amdcpu/0/temperature/0,kind=Temperature,name=CPU_Package,alias=CPU_Core,device=AMD_Ryzen value=1 1793450400000
sensordash_cooling_binding,host=main-desktop,sensor=/amdcpu/0/temperature/0,system=loop1,system_name=Walnut_Loop,component=cpu,component_name=CPU_Block,role=heat-source,kind=Temperature value=1 1793450400000
```

For security, the ingest gateway allows these two exact metric schemas (and the raw-reading schema) but **no arbitrary user-supplied measurement names or control commands**. Aliases/names are represented in sanitized readable ASCII labels (spaces become underscores) with explicit size limits. Raw metric identity remains only `host, sensor, kind`, so changing an alias does not fragment long-term temperature history.

Removed associations and renamed labels are marked with `value=0` **tombstones** on the previous tag set, before/alongside new active (`value=1`) entries. Queries filter on `== 1` so deleted sensors/old aliases aren't returned as active. A successful complete snapshot is persisted in Windows ProgramData and reused to compute deletions after service restarts. Failed metadata delivery is retried independently of numeric samples; a failed catalog update must never affect fan control or prevent raw samples being exported.

## Dashboard mechanics

The provisioned **SensorDash · Cooling Systems** dashboard presents a current association table and time-series panels for assigned temperatures, CPU/GPU power, radiator fans, pump tach, PWM duty, coolant temperature, flow and room ambient (when those readings exist).

Panels join the single-copy raw samples with time-indexed metadata using PromQL's `on(host,sensor) group_right(...)` one-to-many matching. This allows the same temperature sensor to be referenced by more than one component without copying its raw history into separate series.

For example:

```promql
sensordash_reading_value{kind="Temperature",host=~"$host"}
* on(host,sensor) group_right()
(sensordash_cooling_binding_value{system=~"$system",kind="Temperature"} == 1)
```

Metadata exists only **from its first successful synchronization forward**; it cannot reconstruct cooling mappings from before export was enabled. Prior configurations are not fabricated. Old assignments remain correct as-of their original time until a later tombstone, provided retention covers that period. A 120-second refresh keeps info values inside the usual Prometheus-style instant-query lookback for healthy hosts. When the collector goes offline, stale metadata will expire from instant queries rather than presenting obsolete configuration as current.

## What hasn't moved

- **Cooling-system editor, sensor assignments, electrical calibration and fan/pump presets:** remain in SensorDash's local configuration.
- **Fan curves, UAC authorization, PWM output and fail-safe handling:** remain exclusively on Windows.
- **Estimated watts and energy:** not yet exported as derived measurements. They require provenance/revision semantics and parity tests with existing SensorDash calculations.
- **Aliases:** metadata-only display information; metadata labels can churn temporarily with edits but cannot split raw sensor identity.
- **Historical raw peak/energy/correlation:** not yet audited for Grafana parity. Keep existing SensorDash UI/history until comparison is complete.

Access Grafana over private LAN or WireGuard; never port-forward its management UI or the restricted telemetry receiver. See [TELEMETRY.md](TELEMETRY.md) for setup and TLS bootstrap.
