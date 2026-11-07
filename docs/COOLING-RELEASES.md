# Cooling backend release packaging and Linux CI

**Status: experimental, draft PR #104.** This is a first packaging and
runtime-integration milestone, not a supported production release.

## What is built

One allowlisted payload (Python ingest service, Compose definitions,
Caddy configuration, VictoriaMetrics scrape configuration, provisioned
Grafana dashboards, and operator CLI) produces:

- `homeaway-cooling_0.1.0_linux-portable.tar.gz`
- `homeaway-cooling_0.1.0_all.deb`
- `SHA256SUMS`

Version 0.1.0 is a **test packaging version**; it is not an official stable
release. Both archives contain identical application assets with a
`release-manifest.json` containing per-file SHA-256 checksums. No credentials,
private keys, historical metrics, machine configuration, database files or
container images are included. The Debian package has no pre-install or
post-install scripts and does not start any service.

`Architecture: all` applies to the package's architecture-neutral Python
and configuration assets, **not** a claim of verified CPU support. Containers
still need images supporting the host's architecture. CI now exercises both x86-64 and ARM64 GitHub-hosted Linux runners.
That is genuine ARM64 container validation, but not yet a physical Raspberry
Pi hardware acceptance test.

## Installed layout

The .deb installs code under `/opt/homeaway-cooling` and an entry point at
`/usr/bin/homeaway-cooling`. At first-run initialization (not at package
install) the CLI creates:

- `/etc/homeaway-cooling/cooling.env`: root-restricted machine settings
- `/var/lib/homeaway-cooling/secrets/`: root-restricted TLS keys, Grafana
  admin password and the collector bearer token
- Docker named volumes: VictoriaMetrics history, Grafana data and the Caddy
  local CA. The Caddy volume contains sensitive CA signing material.

Both a package upgrade and `dpkg -r homeaway-cooling` preserve existing
runtime state and Docker volumes. There is intentionally **no automated data
purge**. The current package does not include automated backup or restore of
Docker named volumes; retain the existing encrypted backup procedures for
other HomeAway installations.

## Local Debian installation (test host only)

After downloading CI-built artifacts and verifying `sha256sum -c SHA256SUMS`,
use a clean Linux test machine:

```bash
sudo apt install ./homeaway-cooling_0.1.0_all.deb
homeaway-cooling version
sudo homeaway-cooling plan
sudo homeaway-cooling init --bind-ip 192.168.1.50 --https-port 9443
sudo homeaway-cooling plan
sudo homeaway-cooling start
sudo homeaway-cooling status
```

The bootstrap and start commands require a real interactive terminal and
typed confirmation. The Caddy frontend uses a **private** CA; explicitly trust
ONLY its public root certificate in the browser and Windows SensorDash
outbound collector. Sender must also supply the generated bearer token.
Do not expose this test endpoint on WAN or disable TLS verification.

To stop (preserves all named volumes):

```bash
sudo homeaway-cooling stop
```

To remove application binaries (retains state and volumes):
`sudo apt remove homeaway-cooling`.

## Portable archive (test host only)

Extract `homeaway-cooling_0.1.0_linux-portable.tar.gz` into a safe directory;
the archive contains the same application files, with a local
`./homeaway-cooling` launcher. Runtime state defaults to the extracted
folder's `data/cooling` and `deploy/cooling/.env`; these are never included in
the tarball. No package-manager scripts run.

## CI test scope

[Cooling package and Linux end-to-end](../.github/workflows/cooling-linux-e2e.yml)
runs on disposable GitHub-hosted Ubuntu on **x86-64 and ARM64**:

1. Builds and verifies both package types, hashes and the strict release
   allowlist.
2. Installs the newly built Debian package; asserts **no state, containers or
   listeners** were created by the package installation.
3. Bootstraps fresh test-only credentials through the interactive guard in a
   pseudo-terminal, validates the exact four-service Compose graph and starts
   the installed backend.
4. Obtains the Caddy **public** root CA and verifies client-side HTTPS trust.
5. Proves Grafana requires authentication and the ingest receiver rejects
   anonymous (401) and malformed authorized (422) submissions.
6. Posts a valid synthetic SensorDash-format temperature sample and confirms
   HTTP 204, VictoriaMetrics ingestion and an authenticated Grafana datasource
   query. Confirms the SensorDash dashboard was provisioned.
7. Stops/restarts only cooling containers and re-queries the **earlier** sample
   to verify named-volume persistence.
8. Installs an updated 0.1.1 package over the 0.1.0 test install, asserts
   no container was recreated or restarted, and queries the earlier sample.
   This is a same-payload package upgrade check, **not** a schema-migration test.
9. Removes the Debian package and verifies runtime config/secrets remain.
   CI-only state is destroyed when the ephemeral runner exits.
10. Publishes the original 0.1.0 packages and checksums as short-lived,
    architecture-labeled CI artifacts.

This validates Linux packaging, networking, gateway TLS, auth and the actual
telemetry query path. It **does not** prove Windows MSI/collector compatibility,
fan-control fail-safe continuity, production DNS/VPN behavior, physical Pi hardware compatibility, certificate rotation, upgrades involving schema migrations, or
full backup/restore. Those remain explicit acceptance gates.

No workflow deploys to a home Pi, publishes a package repository, opens
router ports, or merges the PR automatically.

## Next milestones

Expand matrix testing to ARM64 after initial amd64 integration passes, add
version-to-version upgrade testing and encrypted Docker volume backup/restore,
then implement a component resolver and migrate the full Pi only following
an audited operator-approved no-op inventory and restoration rehearsal.
