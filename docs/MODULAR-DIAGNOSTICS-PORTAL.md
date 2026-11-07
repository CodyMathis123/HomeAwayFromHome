# Optional Monitoring Diagnostics and Component-Aware Portal

**Status:** experimental PR #104. This is an isolated monitoring-backend
release, not a migration or replacement of your existing full HomeAway Pi.

## Installed features

The previously implemented `cooling-monitoring` and
`linux-host-monitoring` are independently installable. This iteration adds:

| Feature | Dependency | Container | Exposure |
| --- | --- | --- | --- |
| `monitoring-diagnostics` | Existing monitoring source + shared observability | `metrics-diagnostics` | **Private Docker network only** |
| `home-portal` | `monitoring-diagnostics`, plus an existing monitoring source | `portal` | Authenticated route at `https://HOST:PORT/portal/` |

The resolver automatically adds the diagnostics container when the Portal is
selected. It never installs Pi-hole, WireGuard, a VPN agent, LLDAP, Authelia,
remote PC controls, or household admin tools unless those are implemented
as separately approved modules later.

The original household Portal (`portal/app.py`) is intentionally left
unchanged. It depends on Pi-hole/WireGuard-specific links, embedded Grafana,
and household account management. The modular package uses a much narrower
`portal/modular.py` and its own assets, sharing the underlying design
principle (read-only overview, explicit feature links) without claiming full
household-admin compatibility.

## Example installation (clean test host only)

The package includes the same Python CLI, generated Compose files,
VictoriaMetrics data store and Grafana dashboard provisioning:

    homeaway-cooling components
    homeaway-cooling plan --components cooling-monitoring,monitoring-diagnostics
    homeaway-cooling plan --components linux-host-monitoring,home-portal
    homeaway-cooling plan --components cooling-monitoring,linux-host-monitoring,home-portal

For a fresh Linux host, select one supported combination:

    sudo homeaway-cooling init --components linux-host-monitoring,home-portal --bind-ip 192.168.1.50
    sudo homeaway-cooling plan
    sudo homeaway-cooling start

Both init and start require interactive confirmation; installation alone
does not start anything or change the host's DNS, router, VPN or firewall.
**Adding or removing modules from an existing installation is still
unsupported**, including upgrades from the earlier standalone cooling-only
config. Never edit the saved selection and assume Compose will safely
remove stateful services.

## Diagnostic evidence and limitations

The existing metrics diagnostics service gains a modular runtime mode.
When used in a modular installation, its read-only, static query allowlist
contains only:
- VictoriaMetrics scrape status and free storage space.
- Linux host CPU, memory, load, thermal sensors and node-exporter scrape
  state **if** `linux-host-monitoring` was selected.
- Last-known SensorDash host telemetry timestamp **if**
  `cooling-monitoring` was selected.

Pi-hole, WireGuard and Pi-specific probes remain in the existing
full-household mode; they are not queried in the modular mode.
Unavailable metrics report **unknown**, not an invented healthy state.
The original public certificate scanner is disabled in standalone modular
mode because those household names and certificates are absent.

The diagnostics service does not expose a host port, publish VictoriaMetrics
queries to the browser, or accept client-supplied MetricsQL expressions.
The Portal talks only to the diagnostics service on its private Compose
network and returns a bounded, read-only summary.

## Portal security

The standalone Portal has a separate, randomly generated password stored
in a private file on the host under
`/var/lib/homeaway-cooling/secrets/portal_password`.
The Python portal container reads this file through a **read-only** bind
mount; the password itself is **not** copied into image layers, a Compose
environment variable, or the browser API.

The Portal authenticates each request with HTTP Basic authentication
(username `admin`) after Caddy terminates HTTPS using its local CA.
Browsers must trust the **public** CA root certificate. The Portal
password is separate from Grafana's admin password, so an authenticated
Portal session does not itself grant access to Grafana.

Only selected feature cards appear. For example, a Linux-host-only Portal
has no SensorDash telemetry collector, no collector token, and no
Windows sensor UI. The underlying Caddy route /portal/ does not exist
when the feature was not selected and returns HTTP 404. Portal routes do
not offer Docker, fan control, DNS, VPN, PC power or account mutations.

A future unified identity module may replace this minimal Portal login,
but only after explicit compatibility, role and migration design.

## CI validation

The cross-architecture Linux workflow now exercises:
- The six previously supported cooling-only, host-only, and combined
  installations on both x86-64 and ARM64.
- Cooling with standalone diagnostics on x86-64.
- Cooling with Portal (and automatically required diagnostics) on x86-64.
- Linux-host-only with Portal on ARM64.
- Cooling + Linux host + Portal on ARM64.

For each selected bundle the workflow builds and installs the same Debian
package, validates generated Caddy and Compose definitions, starts the
actual services, verifies authenticated HTTPS, conditional service exposure,
the selected Grafana data sources, restart/persistence, package upgrade
without service restarts, and data-preserving package removal.

For Portal combinations it also verifies that unauthenticated access is
denied, valid Portal credentials work, the installed-feature list contains
only selected modules, and diagnostics can be read through the protected
Portal endpoint. For diagnostics-only it verifies the private internal
summary, with no public diagnostic route.

Physical Raspberry Pi acceptance, real SensorDash MSI telemetry,
config/schema migration, release signing, and encrypted Docker-volume
backup/restore remain required before stable distribution.
