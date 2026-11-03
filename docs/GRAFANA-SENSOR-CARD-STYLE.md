# Sensor-attributed Grafana Stat cards

SensorDash remains the source of truth for sensor identities, aliases, cooling
associations, and physical controls. Dashboards do not hard-code alternative
names or infer hardware associations from arbitrary labels.

## Presentation contract

- A **maximum / peak from multiple physical sensors** must retain the actual
  winning sensor's identity. Do not use a final `max(...)` aggregation that
  discards the source label if the card purports to identify a sensor.
- Prefer the SensorDash alias when assigned; otherwise fall back to a
  meaningful physical sensor name. Keep raw `host`, `sensor`, `device`,
  and `name` labels for inspection.
- Normalize display-only underscore runs to spaces and trim edge whitespace.
  Never rewrite stable sensor IDs, or make Grafana another alias editor.
- For compact sensor-attributed Stat cards, use `value_and_name`, vertical
  orientation, `wideLayout=false`, and `justifyMode=center`, with the sensor
  label above the numeric value. Match Overview's small title and readable
  value sizes.
- Do **not** assign a fictitious winning sensor to averages, sums, multi-sensor
  totals, or counts. Single-source gauges need no extra attribution label.
- Respect whether a query represents the current sample, a selected-time-range
  historical peak, or a rolling period. History must not silently claim to
  be live, especially when a Windows computer is sleeping.

## Maintenance

Run the read-only repository-wide audit after editing Grafana JSON:

```bash
python3 scripts/audit_grafana_stat_labels.py
python3 scripts/audit_grafana_stat_labels.py --show-all
python3 -m unittest discover -s scripts/tests -p 'test_peak_sensor_provenance.py'
python3 -m unittest discover -s scripts/tests -p 'test_grafana_stat_audit.py'
```

The audit scans every JSON dashboard on disk. It distinguishes real
unattributed maximum cards from intentionally changing maximum sparklines,
derived freshness metrics, and period-to-period comparisons. The 12 known
Overview and compact portal live-sparkline exceptions are enumerated in the
audit script; review that allowlist whenever the design or panel IDs change.
These charts show a **moving sensor-family maximum** over time, not a fixed
source sensor, so forcing one name onto the sparkline would be misleading.

The cross-dashboard pass standardized the six Reporting Summary selected-range
peak cards, three Cooling Systems RPM/PWM cards, and retained the existing
normalized historical peaks in Overview, the storage peak in Drives &
Retention, and the two SensorDash hardware peak cards. Current-value moving
maximum sparklines remain unchanged until we can show a separate current
winner without obscuring history. The four derived freshness/comparison
cards remain aggregate by design.
