# Historical HomeAway development milestones

These are retained project notes describing how the Pi monitoring features were implemented incrementally. They are **not** the current installation instructions. Use the [root README](../README.md), [telemetry guide](TELEMETRY.md) and [PC-control runbook](PC-CONTROL.md) for the reviewed operating model. New Windows SensorDash builds intentionally retire local history charts and virtual sensors; historical directions to compare those views may no longer apply.

## SensorDash / Grafana milestone 1

See [docs/TELEMETRY.md](docs/TELEMETRY.md) and [cooling dashboard contract](docs/COOLING-GRAFANA.md): an optional Grafana 13.3.0 + VictoriaMetrics v1.155.0 profile, write-only authenticated TLS intake, a raw-sensor dashboard, a metadata-driven cooling-system dashboard, and a provenance-labeled fan/pump electrical dashboard. SensorDash's exporter remains disabled by default; all existing local control and browser features continue. Do not expose metrics endpoints at the router.

## Real hardware validation

After CI passes, follow [First Pi/Windows rollout and side-by-side verification](docs/LIVE-VALIDATION.md). It describes the staged Pi-hole/DNS cutover, WireGuard test from iPhone cellular, private CA/token transfer to Windows, and the read-only `scripts/verify-telemetry.sh` checker. **Do not merge or delete the original SensorDash dashboard until that comparison passes.**

## Optional Pi-hole and WireGuard dashboards

The metrics profile now has opt-in internal monitoring for Pi-hole v6 aggregate DNS statistics and wg-easy v15 native Prometheus statistics. Both use the existing VictoriaMetrics instance. No additional host port is opened. Follow [DNS & VPN monitoring setup](docs/DNS-VPN-MONITORING.md) to configure app/bearer credentials and enable wg-easy Prometheus. The HomeAway DNS/VPN dashboard remains read-only; peer public endpoints and DNS queries are not collected.

## SensorDash-style unified overview

The read-only [SensorDash Overview](grafana/dashboards/overview.json) puts featured temperatures/RPM, recorded peaks, CPU/GPU power, cooling electrical estimates, temperature trends and a hardware catalog on one Grafana page. See [design and limitations](docs/GRAFANA-DESIGN.md). Windows remains the sole authority for hardware control.

## Storage and events moved to Grafana

Storage-device activity and temperature, local history-buffer footprint, and historical threshold events are now available in the new read-only [Storage](grafana/dashboards/storage.json) and [Events](grafana/dashboards/events.json) dashboards. Windows remains the sole owner of sensor sampling, alert thresholds, fan control, tray icons and cooling associations. Deploy the Pi ingestion schema before upgrading the Windows exporter.

### Embedded read-only Grafana metrics in HomeAway

The portal supports an opt-in, one-iframe compact Grafana view without an extra login. Keep all browser endpoints LAN/VPN-only; read [the enablement and revocation guide](docs/PORTAL-METRICS-EMBED.md) before sharing. By default no dashboard is shared and no iframe loads. Grafana admin authentication remains enabled.

## Authenticated Windows PC control (optional)

The new **HomeAway PC Controls** section uses a pinned Caddy + Authelia 2FA gateway on private HTTPS port 8443. Two Windows PCs (`gaming` and `work`) are inventory examples, not hardcoded assumptions; register additional machines with `scripts/add-pc.py`. WOL and Windows power actions run through an unprivileged host-side `pcctl` agent over a group-restricted Unix socket, not directly from the web application. The iOS Shortcut's forced-command SSH entry point remains compatible.

**Not enabled by default and not deployed automatically.** Start with [PC control and authentication onboarding](docs/PC-CONTROL.md), including Pi inventory, Windows SSH hardening, Authelia bootstrapping, local CA trust, TOTP enrollment, reversible migration and the step-by-step procedure to add a new PC. :8088 remains read-only and the controller has no host TCP listener.
