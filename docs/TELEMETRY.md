# SensorDash -> HomeAway telemetry proof of concept

**Stage:** opt-in; keeps all current Windows fan control, tray, cooling configuration, SQLite history and browser UI. Nothing is enabled automatically. Grafana replaces no local UI in this PR.

## Data flow and security

```
Windows collector -> local SQLite samples -> opt-in outbound exporter
   -> HTTPS https://PI_LAN_IP:8429/ingest (private CA + bearer token)
   -> restricted Python gateway -> internal VictoriaMetrics HTTP 8428
   -> Grafana (authenticated LAN/VPN HTTP 3000)
```

The VictoriaMetrics API has **no published host port**. The gateway accepts only `POST /ingest`, checks a long bearer token using constant-time comparison, enforces a 256 KiB payload cap and one whitelisted SensorDash metric schema, then forwards to VictoriaMetrics. The gateway is the *only* LAN-published ingest service, bound to `PI_LAN_IP` and running with dropped capabilities and a read-only root filesystem. Router forwards must still expose **only WireGuard UDP**, not port 8429, 3000 or 8088.

Transport uses a dedicated private CA rather than changing the Windows system trust store. A 365-day server certificate is generated with a SAN bound to `PI_LAN_IP`; changing the Pi IP or expiry requires regenerating the TLS identity, updating Windows' CA copy and restarting the Windows collector. Never disable Windows certificate verification or publish the CA private key.

Grafana has its own login, with a generated initial password. HTTP port 3000 is LAN/VPN-only and does not carry fan-control authority. Add an HTTPS reverse proxy before administering Grafana over an untrusted LAN; WireGuard already encrypts VPN clients.

## Prepare Raspberry Pi

1. Complete the [core Pi-hole and VPN installation](INSTALL.md). Reserve a stable Pi LAN IP.
2. Generate a Grafana password: `openssl rand -hex 24 | sudo tee secrets/grafana_admin_password >/dev/null` then `sudo chmod 600 secrets/grafana_admin_password` (or use `bash scripts/init.sh` for a new installation). Do not commit it.
3. Generate telemetry TLS and bearer credentials, only once:

```bash
sudo bash scripts/init-telemetry.sh
```

The script creates `secrets/telemetry-ca.key` (unmounted, offline/private) and `secrets/telemetry/{ca.crt,server.crt,server.key,token}`. Credentials are not overwritten if they already exist.

4. Grafana's container user normally uses UID 472; prepare its bind-mounted storage once:

```bash
sudo mkdir -p data/grafana data/victoria-metrics
sudo chown -R 472:472 data/grafana
```

5. Build and start the **optional** metrics profile, without redeploying the DNS/VPN core:

```bash
docker compose --profile metrics up -d --build grafana telemetry-ingest
docker compose --profile metrics ps
```

6. Open `http://PI_LAN_IP:3000`, sign in as `admin` using the generated password, and visit **HomeAway → SensorDash · Hardware Telemetry**. The data source and dashboard are provisioned from versioned files. Charts stay empty until the Windows collector is explicitly configured and running.

## Prepare Windows SensorDash

Install a build of the separate `feature/telemetry-grafana-export` SensorDash PR; do not replace a running system until you approve the tested MSI. Stop its `SensorDashCollector` Windows Service before editing the service-owned config.

As administrator, create under `%ProgramData%\SensorDash`:
- `telemetry-ca.crt` — copy **only** `secrets/telemetry/ca.crt` from the Pi.
- `telemetry-token.txt` — copy the secret value from `secrets/telemetry/token` **privately**; restrict the file's Windows ACL to SYSTEM and Administrators.
- `telemetry-export.json`:

```json
{
  "enabled": true,
  "endpoint": "https://192.168.1.10:8429/ingest",
  "tokenFile": "C:\\ProgramData\\SensorDash\\telemetry-token.txt",
  "caCertificateFile": "C:\\ProgramData\\SensorDash\\telemetry-ca.crt",
  "hostLabel": "my-windows-pc"
}
```

**Replace the example IP with your actual Pi IP.** The exporter starts only after a service restart and only if `enabled=true`. An absent configuration leaves the existing application completely unchanged. The host label is a persistent display identifier; changing it creates a different time series identity.

The exporter **does not read hardware a second time**. It reads the collector's existing SQLite `samples` table in small batches, emits original sample timestamps in Influx line protocol and persists a resume cursor after successful HTTPS delivery. On network failures it retries without affecting fan control. It uses the existing history retention policy, so unsent samples older than the local retention window can be lost. The first activation starts at the latest already-archived sample, so it does **not** replay an entire prior month unexpectedly. Historical import will be a separate explicit migration.

Current MVP metrics look like:

```text
sensordash_reading,host=my-windows-pc,sensor=/amdcpu/0/temperature/0,kind=Temperature value=61.5 1793450400000
```

Query the Prometheus-compatible series `sensordash_reading_value{host="my-windows-pc",kind="Temperature"}`. A panel may show opaque sensor IDs until we implement the next catalog/alias synchronization phase; that is intentional to avoid silently changing series identity on alias edits.

## Behavior and limitations

- **Fan curves, authorizations and failsafes:** 100% on the Windows collector; Grafana cannot send commands.
- **Cooling systems and calibration:** authoritative in SensorDash, never duplicated as editable Grafana state. The exporter now sends a bounded read-only sensor catalog and cooling-component bindings. Grafana's `SensorDash · Cooling Systems` dashboard joins historical raw readings with this metadata; see [cooling integration notes](COOLING-GRAFANA.md). Metadata changes are explicitly marked with `value=0` tombstones; the previous acknowledged snapshot is persisted on Windows.
- **Derived fan/pump power:** now archived by the Windows collector in its existing SQLite file, with original sample timestamps, measured/calibrated/curve/rated provenance and a deterministic electrical-configuration revision. It is exported using a separate durable cursor so outages don't cause replay under new calibration. Grafana's third dashboard, **SensorDash · Fan & Pump Electricity**, visualizes this without gaining hardware control. **Gap-aware watt-hours and observation coverage remain deliberately unimplemented pending real parity checks**.
- **Existing SensorDash charts:** remain intact during comparison.
- **Storage:** VictoriaMetrics retains 90 days; Windows local SQLite retention remains configurable independently. Check disk use on the Pi SSD.
- **Back-pressure:** the exporter reuses local SQLite history and processes at most 384 rows per HTTPS request, never holding a large backlog in memory.
- **Authorization:** one token gates write-only ingress. It does not allow reads of VictoriaMetrics, access to Grafana, fan control, or system administration.
- **TLS:** Windows verifies both the endpoint hostname/IP and chain against the configured dedicated CA. Do not use `http://` or disable validation.
- **Remote iPhone:** Grafana via the LAN or WireGuard, not port-forwarded to the public internet.

## Next milestone

The sensor catalog, cooling association metadata and first cooling dashboard are implemented. Next: export derived electrical power with explicit measurement provenance/revisions and compare gap handling, historical peaks and paired-sample correlations with SensorDash's local UI. The Pi's **VictoriaMetrics query compatibility is now exercised in CI against a real container**, without touching the actual Pi.

Official references: [VictoriaMetrics Influx write API](https://docs.victoriametrics.com/victoriametrics/integrations/influxdb/), [Grafana file provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).
