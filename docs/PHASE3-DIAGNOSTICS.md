# Phase 3 — Admin diagnostics foundation

This implementation extends the existing **Setup & Diagnostics** view behind
Caddy/Authelia at `/control/#setup-readiness`. It does not change Pi-hole,
WireGuard, Windows SensorDash, fan control or the Grafana dashboards.

## Why a tiny separate observer?

The `pc-control` container is isolated on the private `pc-control` bridge.
VictoriaMetrics and its exporters stay on `infra`. The stateless
`metrics-diagnostics` adapter alone joins both bridges:

```text
Admin browser -> Caddy + Authelia -> /control/api/admin/diagnostics
                                    -> pc-control (enforces admin role)
                                    -> metrics-diagnostics (fixed GET, 20s cache)
                                    -> VictoriaMetrics (pre-existing data)
```

There is no new metrics database, query language endpoint, durable state,
Docker socket, host filesystem mount, credential or LAN/WAN port. The control
API is not added to the broad infrastructure network. The public read-only
`:8088` portal cannot retrieve the per-PC telemetry freshness data.
Requests not authenticated as an administrator receive 401/403 before an
observer request. The observer is not a new fan or machine controller.

Only a fixed allowlist of bounded instant MetricsQL queries executes, with
up to four concurrent queries, 2-second query timeouts, and a 20-second
adapter cache. Queries have no browser-selected host, label, path or time
window; responses include no raw sensor values or per-sensor labels.

## Evidence presented

| Display | Actual source | Important limitation |
| --- | --- | --- |
| Pi exporter | `up{job="pi-node"}` | Confirms scraper's latest response, not complete Pi health |
| Pi CPU | 5-minute CPU idle-counter rate | May be unavailable shortly after first scrape |
| Pi memory | MemAvailable / MemTotal | Observed memory utilization, not a diagnostic cause |
| Pi load | 1-minute load average | CPU load, not per-process usage |
| Pi thermal | Maximum reported `node_thermal_zone_temp` | Hottest exported zone, not necessarily CPU die temperature |
| Storage free | `vm_free_disk_space_bytes` | VictoriaMetrics *data filesystem*, not the Pi's whole root disk |
| Windows samples | Latest timestamp among `sensordash_reading_value` per host, 24h | No new samples may mean sleep, shutdown, disconnected client, or exporter issue |

The last-sample query uses VictoriaMetrics' `timestamp(series[24h])`
lookbehind form. If no series appears in 24 hours it reports **Unknown**,
not "computer off". A last sample older than five minutes is
**Stale export**, which does not establish Windows or fan-control state.

Partial failure is safe: if metrics aren't configured, the observer is
stopped, a series isn't exported, or a query exceeds its deadline, the
corresponding card reports **Unknown** without changing DNS/VPN indicators.
The API returns a versioned check schema and labels the source/timestamp.

## Operator deployment (after reviewed merge)

On the Pi, from the clean `Production` worktree:

```bash
cd ~/HomeAwayFromHome
bash scripts/deploy-ui.sh --plan
bash scripts/deploy-ui.sh --apply
bash scripts/deploy-diagnostics.sh --plan
bash scripts/deploy-diagnostics.sh --apply
```

The last command builds/starts only `metrics-diagnostics` with
`--no-deps`, *after* confirming that the existing `pc-control` and
`victoria-metrics` services are running. It does not restart either
service, and it does not modify DNS, VPN, TLS, users or Windows.

The observer is new and intentionally isn't installed implicitly by the
existing guarded UI deployment. First installation requires that separate,
explicit command. A later UI-only change still uses
`scripts/deploy-ui.sh`; observer code changes require re-running the
diagnostics-specific deployment command.

Open `https://homeaway.home.arpa:8443/control/#setup-readiness` as an
admin and refresh. Verify the Pi metrics values reflect Grafana
`HomeAway System Health`, telemetry age is plausible for Windows, and
the UI never marks a sleeping PC "off". An unavailable metrics service
must display Unknown instead of interrupting other HomeAway controls.

To stop only the observer if troubleshooting, run:

```bash
sudo docker compose -f compose.yaml -f compose.control.yaml \
  --profile control --profile metrics stop metrics-diagnostics
```

The rest of the stack keeps running. This is a narrow, non-disruptive
rollback for the new observations; it is not a full disaster-recovery test.

## Deliberately out of scope

Browser/public Let's Encrypt certificates and private telemetry CA expiration,
real WireGuard client handshakes, client DNS settings, backup verification and
automatic changes remain separate tasks. The hostname/Caddy DNS-01 migration
will be staged and reversible, with explicit operator actions and no new
WAN port. Full backup/restore validation is deferred to Phase 4 while this
greenfield system is evolving; existing manual recovery procedures remain.
