# Clean-host monitoring recovery: two-runner CI contract

**Status: test-only stacked PR on #105, which itself depends on #104.**
This is not a user-accessible restore command and does not change any
production HomeAwayFromHome Pi, Windows SensorDash or external DNS/VPN setting.

## What is different from the isolated-volume rehearsal?

The original #105 test validated backup contents on the same disposable
GitHub runner that created them, using scratch Docker volumes and an
unpublished VictoriaMetrics process. This new workflow uses **separate
ephemeral GitHub-hosted Linux runners** for the source and the recovery
target, on both x86-64 and ARM64.

The source runner initializes a cooling-only backend, sends one synthetic
SensorDash observation over authenticated, verified HTTPS, and creates a
stopped-state age-encrypted full backup including all four named Docker
volumes, generated configuration, Caddy CA and application secrets. That
runner and all its containers are disposed of afterward.

The target runner begins with **no HomeAway runtime configuration and no
existing HomeAway containers or volumes**. It installs the exact package
version used by the source, and obtains only the test archive, its
checksums/manifest and the ephemeral synthetic test identity.

The CI-only recovery orchestrator then:

1. Rejects backups with mismatched project, components, volume inventory,
   age header, timestamp or SHA-256 checksum.
2. Decrypts a complete stream through the strict archive validator.
   Absolute and parent-traversal paths, symlinks, hardlinks, device files,
   duplicate members and unknown volume names are not permitted.
3. Allocates randomly named new Docker volumes and decrypts a second time
   directly into those volumes. It never writes a plaintext tar file or
   references live HomeAway volume names.
4. Checks recovered config, Grafana SQLite integrity and Caddy public CA.
5. Copies only recovered configuration and secrets into a new root-only
   temporary runtime directory; it **regenerates** Compose, Caddy and
   VictoriaMetrics scrape configuration for the new machine, bound to the
   CI-only loopback interface and a separate HTTPS port.
6. Creates a distinct Compose project with the scratch volumes configured
   as external. Nothing is mounted from or written to a previous stack.
7. Boots the complete cooling-only application: Caddy, Grafana,
   VictoriaMetrics and the authenticated SensorDash telemetry receiver.
8. Verifies that the original Caddy CA fingerprint is retained, TLS is
   trusted, anonymous Grafana is denied, bearer authentication and schema
   validation remain enforced, the original synthetic metric is queryable
   through VictoriaMetrics **and** Grafana, and a **new** synthetic metric
   can be ingested after recovery.
9. Verifies that the selected Grafana dashboards are present, then stops
   the recovery project and removes only its randomly allocated scratch
   Docker volumes and temporary configuration.

## Scope and security

The two-job artifact contains **only synthetic CI-generated telemetry,
credentials, an encrypted backup and an ephemeral private age identity**.
Because the test key and ciphertext travel together in the one-day GitHub
Actions artifact, this artifact is **not treated as confidential**.
It is strictly forbidden to substitute real user backups, production keys,
real sensor recordings or real account credentials.

The test executable requires both a CI-specific guard and the
GitHub Actions environment. It is intentionally excluded from the runtime
Debian and portable package payloads. There is no \`homeaway-cooling restore\`
command and no operator-facing production restore path.

The target reuses the source's local CA and token to test identity continuity,
but changes host-specific configuration only within a private scratch
directory. It does not change the host DNS server, firewall rules, WireGuard,
Pi-hole, systemd sockets, or mounted full-household state.

CI explicitly tests the absence of previously initialized runtime state and
that no source/project volumes are present. All services publish only the
chosen loopback HTTPS listener; no other public ports are added.

## What passing CI does and does not prove

A passing job demonstrates application boot, restored telemetry credentials,
HTTPS identity, Grafana login and dashboards, historical metric queries,
and fresh ingestion on a separate newly provisioned Linux machine using
the same release version. Both target architectures are exercised.

It does **not** yet validate migration between incompatible database
versions, revocation/re-pairing with real Windows SensorDash machines,
new-host publicly trusted certificates, full production Docker image
availability, Pi-specific SMART monitoring, or the complete 14-container
home system. A safe human-facing restore interface and off-site key
recovery remain separate engineering and acceptance gates.

See [isolated recovery rehearsal](MODULAR-RESTORE-REHEARSAL.md),
[component lifecycle and backup](MODULAR-LIFECYCLE.md), and
[developer architecture](DEVELOPER-ARCHITECTURE.md).
