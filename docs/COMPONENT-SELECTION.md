# Component selection and dependency resolution

**Experimental in PR #104.** The executable catalog now supports two
independently selectable Linux monitoring features, with a shared, generated
Compose deployment. The existing household Pi is not changed.

## Supported selections

| User-selected features | Required shared capabilities | Containers |
| --- | --- | --- |
| cooling-monitoring | observability-ui, metrics-storage | Caddy, Grafana, VictoriaMetrics, telemetry-ingest (4) |
| linux-host-monitoring | observability-ui, metrics-storage | Caddy, Grafana, VictoriaMetrics, node-exporter (4) |
| cooling-monitoring,linux-host-monitoring | observability-ui, metrics-storage | All five, no duplicate backend |

The CLI offers both text and JSON output:

    homeaway-cooling components
    homeaway-cooling components --json
    homeaway-cooling plan --components cooling-monitoring --json
    homeaway-cooling plan --components linux-host-monitoring
    homeaway-cooling plan --components cooling-monitoring,linux-host-monitoring

For planned features, only a non-installable read-only design preview is
available:

    homeaway-cooling plan --components cooling-monitoring,dns-metrics --design
    homeaway-cooling plan --components vpn-metrics --design --json

The resolver validates catalog schema, unique IDs, known dependencies,
topological ordering without cycles, declared conflicts, and an explicit
allowlist of implemented service graphs. It adds required capabilities,
deduplicates shared services, reports host requirements and shows optional
suggestions without selecting them.

Components beyond cooling and Linux host metrics, including Pi-hole,
WireGuard, household accounts and remote PC management, are planned
only. They cannot be installed or selected for a running deployment.

## Installation and lifecycle

Install an experimental package on a fresh test host only; verify its
published checksums first.

    sudo apt install ./homeaway-cooling_0.1.0_all.deb

Choose a feature set at the first installation:

    sudo homeaway-cooling init --components linux-host-monitoring --bind-ip 192.168.1.50
    sudo homeaway-cooling plan
    sudo homeaway-cooling start
    sudo homeaway-cooling status

Initialization requires a typed confirmation, creates only the credentials
needed by selected components, writes the selected IDs into a protected
runtime configuration, and generates Compose/Caddy/VM scrape files. It
does not start any services. Starting also requires confirmation. A
host-only selection does not create a SensorDash telemetry token/CA or
expose the ingestion endpoint.

A saved feature set is revalidated before start and stop. Changing the
selection of a running installation is **not supported yet**: the CLI
refuses changes without a controlled migration instead of silently
deleting existing containers or historical data. A previous standalone
cooling-only configuration without an explicit selection still resolves
as cooling-only.

The generator runs in memory for plan mode and never calls Docker or
writes files until initialization or an explicitly confirmed start.
Generated Compose has a single HTTPS listener, private data networks,
required services only, and safe read-only node-exporter host mounts.

Read [Linux Host and Compose](LINUX-HOST-AND-COMPOSE.md) for design details,
generated file locations, monitoring scope and the six-job Linux CI matrix.
See [Cooling Releases](COOLING-RELEASES.md) for package release behavior.
