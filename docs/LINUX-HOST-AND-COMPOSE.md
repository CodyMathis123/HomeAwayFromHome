# Linux host monitoring and generated Compose

**Experimental implementation in PR #104. Do not deploy to the existing household Pi yet.**

## Installable component choices

| Selected features | Services | What is available |
| --- | --- | --- |
| cooling-monitoring | VictoriaMetrics, Grafana, Caddy, telemetry-ingest | SensorDash history and dashboards |
| linux-host-monitoring | VictoriaMetrics, Grafana, Caddy, node-exporter | Linux CPU, load, memory, disk I/O, thermal sensors and dashboards |
| cooling-monitoring,linux-host-monitoring | The four shared/selected services plus node-exporter (five total) | Both, sharing one metric database and Grafana |

The shared observability-ui capability owns Grafana and HTTPS gateway; the
shared metrics-storage capability owns VictoriaMetrics. Both are automatically
required and never presented as individually installable features.

Linux host monitoring uses the upstream node-exporter and read-only host
/proc and /sys bind mounts. It drops Linux capabilities, has no host devices,
no Docker socket, no privileged container, and no published exporter port.
The initial scope excludes the Raspberry Pi SMART/SSD textfile collector.
CPU thermal metrics may be absent depending on available host sensors.

In a host-only installation, the telemetry-ingest container is absent,
its bearer-token and upstream TLS files are never generated, and HTTPS
/ingest returns HTTP 404 rather than exposing a collector. Grafana requires
authentication in all configurations.

## Test installation (new host only)

After obtaining a CI-built .deb and verifying its SHA-256, run as follows:

    sudo apt install ./homeaway-cooling_0.1.0_all.deb
    homeaway-cooling components
    homeaway-cooling plan --components linux-host-monitoring
    homeaway-cooling plan --components cooling-monitoring,linux-host-monitoring

Choose one selection and initialize on a separate Linux host:

    sudo homeaway-cooling init --components linux-host-monitoring --bind-ip 192.168.1.50 --https-port 9443
    sudo homeaway-cooling plan
    sudo homeaway-cooling start
    sudo homeaway-cooling status

Use cooling-monitoring instead for SensorDash-only, or both names separated by
a comma for the combined backend. Init and start require typed interactive
confirmation. Installation itself never starts containers or publishes ports.
The Caddy frontend uses a private CA and needs clients to trust its PUBLIC
root certificate. Never import or distribute private CA keys.

## Generated deployment artifacts

The resolver and pure-Python generator produce three machine-specific files
under /var/lib/homeaway-cooling/generated/ (portable archives use
data/cooling/generated/):

- compose.generated.json — complete Docker Compose application, accepted by
  Docker Compose as a JSON-compatible YAML document
- scrape.generated.yml — VictoriaMetrics targets for selected modules only
- Caddyfile.generated — HTTPS frontend and conditional write-only intake route

Generated files are root-restricted and not shipped as package payload.
The package's versioned application files stay in /opt/homeaway-cooling.
The command homeaway-cooling plan computes changes without modifying files
or calling Docker. Init writes fresh local configuration and generated
definitions but starts nothing. Start requires explicit approval before
reconciling selected containers. Start and stop reject selections that
differ from the persisted installed configuration.

Changing selected features on an existing installation is *not yet
implemented*. A controlled addition/removal workflow needs explicit backup,
retention, service migration, and rollback rules. Package upgrade and removal
do not delete metrics, Grafana state, credentials or Caddy certificates.

## Linux CI

The Linux end-to-end pipeline runs all three feature selections on both
x86-64 and ARM64 GitHub-hosted runners. It builds and installs the package,
verifies the exact generated Compose service graph and bound HTTPS port,
starts the selected services, checks authenticated Grafana and conditional
telemetry ingestion, verifies Linux host exporter queries, checks the
provisioned dashboard list, restarts containers, upgrades package files
without restarting containers, and verifies data/state preservation after
package removal.

Linux CI does not replace a physical Raspberry Pi acceptance test or real
Windows SensorDash telemetry check. ARM64 CI also does not verify a specific
Pi OS image, real thermal zones, host SMART devices, or actual boot recovery.
