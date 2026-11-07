# HomeAwayFromHome: modular installation architecture (design proposal)

**Status:** design + cooling-only proof of concept; not yet a released
installer or supported migration. This work is intentionally separate from
the existing full Pi deployment and from the unified CLI draft in PR #103.

## Product goal

One release, optional capabilities, and a minimum host footprint. A person
who only wants SensorDash history must never need Pi-hole, WireGuard, LLDAP,
PC-control, or this project's original household domain.

Windows SensorDash remains the source of truth for local hardware reads,
aliases, fan profiles, calibration, PWM, fail-safes and outbound telemetry.
No HomeAway backend has any fan-control command or authority.

The backend offers data retention, aggregation, dashboards and explicitly
selected network/household management features. Docker Compose remains the
runtime for the initial Linux implementation.

## Component model

- **Features** are user-selectable: cooling-monitoring, linux-host-monitoring,
  dns-filtering, vpn, computer-management, account-directory, home-portal.
- **Capabilities** are deduplicated implementation dependencies: metrics store,
  telemetry intake, Grafana, HTTPS endpoint, local identity, host agents.
- **Optional integrations** never pull in the parent service silently.
  A Pi-hole exporter is useful only when Pi-hole and a metrics store are
  selected, but the presence of a metrics store must not install Pi-hole.
- **Components have explicit lifecycle states:** absent, planned, installed,
  enabled, unhealthy, suspended, or removal-pending. Data retention is a
  separate decision from service installation state.
- **Manifest first:** machine-readable draft at
  [deploy/components/catalog.json](../deploy/components/catalog.json) declares
  IDs, required and suggested dependencies, host requirements and status.
  It is not yet an executable dependency resolver.

### Suggested feature bundles

| User choice | Required backend | Optional integrations |
| --- | --- | --- |
| Cooling history | VictoriaMetrics, authenticated telemetry receiver, Grafana, private HTTPS ingress | Linux-host stats |
| Linux host health | Metrics backend, node-exporter and optional SMART collector | Cooling dashboards |
| Pi-hole DNS | Pi-hole, port 53, explicit router/DNS cutover | Pi-hole metrics |
| WireGuard | Kernel-capable VPN host, wg-easy, explicit peer/router setup | WireGuard metrics |
| Remote PCs | Authenticated operator service, host pcctl sockets, SSH trust, enrollment | LLDAP household directory |
| Household accounts | Identity + LLDAP + broker and explicit recovery setup | Resend notifications |
| Overview portal | Component-aware read-only portal, HTTPS routing | Any installed features |

**Feature selection is not just Docker Compose profiles.** It also determines
host-level packages, credentials, network listeners, service checks, assets,
backup scope, UI navigation, migrations and removal rules. Host systemd
sockets are never Docker containers and cannot be made portable by Compose.

## Proof: cooling without the home appliance

Files:
- [compose.cooling-only.yaml](../compose.cooling-only.yaml)
- [deploy/cooling/scrape.yml](../deploy/cooling/scrape.yml)
- [deploy/cooling/Caddyfile](../deploy/cooling/Caddyfile)
- [scripts/init-cooling-standalone.py](../scripts/init-cooling-standalone.py)
- [Cooling setup and acceptance](COOLING-STANDALONE.md)

This has a separate Compose project (homeaway-cooling), no Pi-hole, VPN,
Authelia, accounts, portal, host sockets or household-specific domain.
The existing ingest validator, VictoriaMetrics image, Grafana provisioning
and selected SensorDash dashboards are reused. It intentionally does NOT use
the existing full-stack compose.yaml or monitoring/scrape.yml.

Caddy issues a **private** browser/Windows-facing certificate with
'tls internal'; clients must explicitly trust its PUBLIC root CA. Grafana
requires a separate login; the ingest path still requires the long bearer
token and validates an exact metric schema. Caddy verifies the private
upstream TLS CA instead of using insecure_skip_verify. Only the host-bound
HTTPS port is published, with no WAN/router configuration changes.

The standalone proof defaults to port 9443 to avoid colliding with the
existing full-stack Caddy 443 listener. A new installation may choose
another port after checking for conflicts. HTTPS is not automatically
internet-public: it must remain on a trusted LAN or independently secured
VPN. We do not add user-supplied Docker socket access.

## Proposed release/layout strategy

A shared package payload should carry version-pinned Compose fragments,
scripts, dashboard JSON, service unit templates and schema migrations.
Installation-specific configuration/keys remain outside the immutable
payload. Suggested Linux conventions:

| Class | Future installed location | Policy |
| --- | --- | --- |
| Executable manager | /usr/bin/homeaway | Owned by .deb/.rpm or portable archive |
| Versioned application | /usr/lib/homeaway or /opt/homeaway/releases | Read-only per release |
| Templates/Compose/dashboard definitions | /usr/share/homeaway | Read-only package assets |
| Host-specific settings | /etc/homeaway | Preserved on upgrades |
| Sensitive credentials | /etc/homeaway/secrets | Root-restricted; never baked into package |
| Persistent runtime state | /var/lib/homeaway | Explicitly preserved during remove/upgrade |
| Backup artifacts | External encrypted destination | Never tied to package removal |

The PoC retains repository-relative paths and named Docker volumes for
isolation; it does not yet implement the future immutable package layout.

Portable Linux installer should be the core. Debian .deb and RPM wrappers
may package the *same* versioned payload without duplicating orchestration
logic. Initially test Debian/Raspberry Pi OS ARM64 and generic amd64 hosts
with Docker Engine + Compose v2. Other distributions and non-systemd hosts
require separate host-integration acceptance, not optimistic compatibility
claims. Image multi-architecture support must be checked per release.

## Resolver and lifecycle contract (future implementation)

1. Discover host CPU/OS, Docker/Compose version, IPs, existing containers,
   ports, systemd and persistent state; show conflicts before applying.
2. Resolve required components as a directed acyclic graph. Reject missing,
   conflicting or platform-incompatible dependencies. Present optional
   recommendations without silently selecting them.
3. Produce a deterministic plan: created/changed credentials, required
   listeners, images, volumes, migration steps, affected services, estimated
   interruption and rollback. No secrets or private telemetry in plans.
4. Explicit interactive apply, with a lock and evidence of operator approval.
   Avoid auto-updating DNS/VPN, gateway and identity as a side effect of
   adding a monitoring module.
5. Record installed component versions and effective manifest. An update
   targets only changed services and runs compatible state migrations.
6. Remove/disable is two-stage: stop a component, then separately decide
   whether historical data/keys may be deleted. Default is **retain**.
7. Backup is component-aware. For full stack, preserve host agent state,
   credentials, identity and service configs; for cooling, provide a
   consistent metrics/Grafana volume backup option. Verify restore in
   isolation before promising disaster recovery.

## Existing full Pi adoption, not reinstallation

Current live production must remain a first-class supported configuration.
The future adopter must inventory existing Docker containers, data folders,
network connections, secrets, gateway Caddyfile variant and three host
PC-management sockets, then propose a no-op mapping to component IDs.
No automatic adoption in this PoC; no rewrites of the original .env, secrets,
volumes, firewall, router or authentication records.

Do not run the new cooling-only stack against existing live Pi data. It
intentionally creates separate storage and a separate Caddy local CA.

## Remaining design decisions

- Should the default frontend be direct authenticated Grafana, a smaller
  cooling portal, or both? First prototype uses Grafana directly.
- Should the client-facing TLS default remain private-CA or offer existing
  operator-managed certs / trusted ACME where achievable? Never silently
  open WAN ports.
- How should a metrics store be shared between modules while preserving
  independent enable/disable and scrape configuration?
- What exact retention and volume backup/restore guarantees do we offer?
- Which host integrations can run without systemd, and which should be
  unavailable for non-systemd distributions?
- Can component additions/upgrades be safely applied to an existing Pi
  while retaining the historical VictoriaMetrics/Grafana data?
- What is the stable long-term API from SensorDash to optional backends?

## Experimental package/release validation

The initial pipeline now builds portable `.tar.gz` and Debian `.deb`
artifacts from one immutable asset list and tests them on a disposable Linux
runner, including actual Docker deployment and synthetic SensorDash-format
HTTPS ingestion. See [Cooling Releases](COOLING-RELEASES.md).
This is not a finished component-aware Linux installer. RPM packaging,
physical Raspberry Pi validation, and migration/rollback for the existing
full Pi remain separate milestones. Package and live-backend CI now passes
on GitHub-hosted x86-64 and ARM64 Linux runners, but does not validate
actual Pi networking, boot persistence or Windows MSI compatibility.

## Acceptance gates before packaging

- Fresh Debian/ARM64 and generic amd64 Linux install of cooling alone, with
  no DNS or VPN services and only one intentional HTTPS listener.
- A real Windows SensorDash MSI sends authenticated observations to the
  new endpoint; Grafana shows real CPU/GPU/fan data; fan control remains
  fully local with backend unavailable.
- Anonymous ingestion returns 401; invalid payload returns 422; Grafana
  requires login; Caddy rejects untrusted upstream certificates.
- Restart, upgrade, component addition/removal and retained-volume recovery
  work across release versions.
- The existing full Pi stays unchanged during the PoC. A separate staged,
  operator-approved adoption rehearsal precedes migration.
