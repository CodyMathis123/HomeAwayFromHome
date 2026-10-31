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
