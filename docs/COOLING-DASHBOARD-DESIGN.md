# Cooling sensor information architecture

Last reviewed: 2026-11-08. Intended for the SensorDash + HomeAway lab; not an
independent fan-control implementation.

## Where a reading belongs

| View | Decision supported | Contents | Avoid |
| --- | --- | --- | --- |
| SensorDash > Cooling | Map sensors, watch physical hardware live and configure controllers | Coolant, flow, fan/pump RPM, heat-source temperatures, optional conductivity/air | Historical dashboards or repeated per-device summaries |
| HomeAway Portal Metrics | 30-second glance: is anything wrong? | CPU/GPU temperatures, radiator/pump tach, coolant temperature, flow, Pi/DNS/VPN status; selected temperature comparison | Full catalog, chemistry, electrical model or every storage probe |
| Grafana Overview | Explain PC thermal state under workload | CPU/GPU/storage temperatures, coolant in the thermal history, reported power and telemetry freshness | Detailed chemistry, hardware settings or multiple copies of cooling charts |
| Grafana Cooling Systems | Explain liquid-loop performance and flow restrictions | Heat-source temperatures/power, primary coolant and flow charts, radiator/pump/duty feedback, assignment reference; advanced chemistry/alarm details collapsed | Duplicating main coolant and flow charts in an additional NEXT row |
| Grafana Reporting Summary | Compare selected 24h / 7d / 30d periods | CPU/GPU temperature and power peaks/averages, coolant and flow averages with sparklines, coverage statistics, collapsed long-range NEXT chemistry trends | Live detailed sensor catalog or duplicate spool/observation headline cards |
| Hardware Telemetry | Investigate raw physical samples | Exact sensor values and aliases, device identities | Simulated or aggregate cooling indicators |
| Storage / Electricity / Events / System Health / DNS & VPN | Investigate their individual specialties | NVMe and SSD telemetry; **modeled** fan and pump electrical power; warning/recovery series; Pi infrastructure | General cooling overview duplicates |

Intentional repetition is allowed when a reading performs a **different
function** (e.g. current coolant temperature in the portal, long-range
temperature changes in Reporting Summary, and coolant-versus-CPU trends in
Cooling Systems).

## Current physical NEXT sensor inventory

Actual LibreHardwareMonitor entries observed in SensorDash on 2026-11-08:

| Source / kind | Meaning | Presentation |
| --- | --- | --- |
| Water Temperature / Temperature | Coolant temperature at the NEXT mounting position | Main coolant curve input and cooling/thermal history; not radiator outlet heat rejection |
| Flow / Flow | Impeller flow in L/h, observed about 224–226 L/h at fixed DDC speed | Main circulation card and trend; no inferred pump curve |
| Conductivity / Conductivity | Electrical conductivity in µS/cm, observed about 19–20 | Advanced chemistry trending; absolute water-quality judgment is coolant-specific |
| Water Quality / Level | NEXT estimate derived from conductivity | Advanced only; **not** a second independent chemistry measurement |
| Flow Alarm / Factor | NEXT's device alarm state (0 = inactive) | Advanced status / event routing; not a second flow meter |
| External Temperature / Temperature | Optional external probe; presently unavailable | Only display when physically reporting and classified as case/room air |
| VCC / Voltage, VCC USB / Voltage | Internal/USB supply diagnostics | Advanced diagnostics; not evidence of cooling efficiency |
| Dissipated Power / Power | NEXT estimate requiring a second **coolant** sensor | Advanced only; unavailable with unconnected external probe or ambient-air probe |

Also relevant: CPU package/Tdie; GPU core, hotspot and VRAM temperatures; CPU/GPU
power readings; radiator hub tach and PWM; fixed-speed DDC tach; NVMe/storage
temperature. Never conflate GPU memory **temperature** with utilization, nor
sum overlapping power sensors from the same device.

## Derived measurements worth adding later

1. **Coolant-to-air delta-T** = coolant temperature minus separately measured
   intake/ambient temperature. The air probe must be correctly classified
   as case intake vs room; never silently assume that an absent external
   probe is 0 C. This diagnoses radiator effectiveness across changing
   room temperatures.
2. **Block-to-coolant differences** = component core or hotspot temperature
   minus coolant temperature at the same time, always labeled by sensor.
   An unusual persistent rise at normal flow suggests a block/contact issue;
   it is not a new physical sensor and must not be treated as exact radiator
   inlet/outlet drop.
3. **Flow stability and change from healthy baseline**, not just current
   number. Sustained low-flow alarms require a startup grace, fresh samples,
   and a calibrated baseline. A low flow signal warrants intervention; it
   cannot be fixed solely with radiator fans.
4. **Thermal response to component load** = coolant rise versus CPU/GPU watts,
   with clear power source labels and no sum of overlapping rails.
5. **Conductivity trend**, not a generic 100% quality badge. Coolant/additive
   choices materially affect conductivity; trends are diagnostic, not
   laboratory corrosion, pH or inhibitor measurements.

## Graphing and telemetry rules

- **Source of truth**: SensorDash owns physical identity, aliases, cooling
  bindings and all PWM decisions. Grafana is read-only; the Pi cannot issue PWM.
- **Identity**: join time series on `(host,sensor)` with catalog/binding metadata;
  never hard-code the USB HID path or infer physical IDs from aliases.
- **Units**: L/h for NEXT flow, °C for physical temperatures, µS/cm for
  conductivity, rotrpm for real tachometers and percent for fan PWM.
- **Fidelity**: no `vector(0)` fillers, cross-gap interpolation, or pretending
  missing external temperatures are zero. Blank panels explain unavailable data.
- **Legends**: use readable physical sensor names, with alias display-only,
  and preserve the underlying stable sensor ID.
- **Minimal duplication**: one main coolant graph and one flow graph per
  Cooling Systems dashboard. NEXT diagnostics should not reproduce them.
- **Hierarchy**: small current numbers with restrained sparklines at top;
  paired trends in the middle; source assignments and advanced diagnostics below.
- **Multi-host**: Portal aggregates values across exported Windows PCs and has
  no template variables. For meaningful per-PC comparisons, use Grafana's
  host selector in non-public dashboards.
- **Freshness**: latest non-null reading may still be stale while the PC sleeps;
  no "live/healthy" claim without sample-age checks. Add explicit freshness
  presentation in a future pass if the current tiles are misinterpreted.
- **Visibility**: an optional air probe should not occupy the normal layout
  until it reports; advanced sensor diagnostics remain accessible collapsed.

## Validation

Dashboard JSON is covered by `scripts/tests/test_dashboards.py`; static
Grafana provisioning and VictoriaMetrics integration checks run in GitHub CI.
Real Windows NEXT discovery and Pi ingest remain separate acceptance checks.
No new cooling control or hardware requirements are implied by these dashboards.
