# Component selection and dependency resolution

**Status:** implemented selection and resolution for draft PR #104, but only
the standalone **cooling-monitoring** bundle can actually be installed.
All other components in the catalog are **planned** and cannot be deployed
by this release. The running household Pi is not part of this workflow.

## Try the selection commands

From an installed test Debian package or portable archive:

```bash
homeaway-cooling components
homeaway-cooling components --json
homeaway-cooling plan --components cooling-monitoring
homeaway-cooling plan --components cooling-monitoring --json
```

An example of a **design-only** plan combining cooling history and
Pi-hole monitoring without actually installing any of it:

```bash
homeaway-cooling plan --components cooling-monitoring,dns-metrics --design
homeaway-cooling plan --components vpn-metrics,dns-metrics --design --json
```

The plan shows explicitly selected features, dependency-first ordering,
automatically included components, unique services, host requirements,
optional *suggestions* that are **not automatically selected**, and whether
the whole combination is deployable. JSON output is suitable for a future
graphical/terminal installer and automated tests.

The catalog is `deploy/components/catalog.json` and is validated by the
pure-Python resolver `scripts/components.py`. It rejects unknown IDs,
bad/duplicate definitions, missing dependencies, cyclic dependencies,
conflicts, and attempts to directly select internal-only capabilities.
Dependencies are deduplicated in deterministic order, including the shared
`metrics-storage` capability.

For example, selecting `cooling-monitoring` resolves to:

```text
Selected features: cooling-monitoring
Automatically required: metrics-storage
Resolved order: metrics-storage -> cooling-monitoring
Unique services: gateway, grafana, telemetry-ingest, victoria-metrics
Deployment: compose.cooling-only.yaml
```

Selecting `dns-metrics` would imply `dns-filtering` and `metrics-storage`,
but both are not currently deployable; with no `--design` flag the command
fails before any service/configuration changes. `--design` **never permits
initialization or deployment**, even if a selection happens to be deployable.

## What gets saved

On a **fresh test host**, initialize the only runnable selection:

```bash
sudo homeaway-cooling init --components cooling-monitoring \
  --bind-ip 192.168.1.50 --https-port 9443
sudo homeaway-cooling plan
sudo homeaway-cooling start
```

Initialization stores the explicit choice in the root-restricted
`/etc/homeaway-cooling/cooling.env` as
`COOLING_COMPONENTS=cooling-monitoring` alongside the local listener
configuration. The portable checkout uses `deploy/cooling/.env` instead.
The resolved, shared `metrics-storage` capability is not separately
selected or installed twice.

Every later `start` and `stop` re-resolves the saved selection before
invoking Docker and verifies that the Compose service list exactly matches
the supported bundle. Trying `--components dns-filtering`, editing the
configuration to include planned features, or changing the requested
selection without a supported migration fails **closed**. Selecting
components later or removing one from a live installation is not yet
implemented; this isn't a destructive toggle.

The previous prototype's settings with no `COOLING_COMPONENTS` line are
interpreted as the original cooling-only selection so that package upgrades
do not require reinitialization.

## Current scope and next stages

The resolver is functional and exercised by unit tests and the Linux
package-runtime pipeline. A selectable feature in the catalog is not
automatically an available deployment. The current release has one
intentionally hard-coded, tested wiring contract to the four-container
`compose.cooling-only.yaml`.

Subsequent stages will introduce other independently deployable bundles,
Compose overlay selection, optional exporters and host integrations,
component-aware status/backup/upgrade and a safe adoption path for the
existing full Pi. No component selection silently alters DNS, VPN, network
firewall, certificates, fan control or account state.

See [modular architecture](MODULAR-ARCHITECTURE.md),
[cooling release packaging](COOLING-RELEASES.md), and
[cooling-only setup](COOLING-STANDALONE.md).
