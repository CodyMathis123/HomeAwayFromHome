# Isolated age-backup restore rehearsal

**Draft, CI-only, stacked on PR #104.** These scripts are not packaged into
the runtime .deb/tar distribution and do not offer production restoration.

## Purpose

Decrypting an archive and listing filenames does not prove application data
is recoverable. This rehearsal uses a genuine encrypted snapshot created by
the modular monitoring backend, extracts it into **new disposable Docker
volumes**, and tests recovered application data without touching the running
stack.

## CI sequence

1. Install the experimental monitoring package on an ephemeral Ubuntu runner.
   Record synthetic SensorDash telemetry through authenticated HTTPS and
   verify the metric through Grafana/VictoriaMetrics.
2. Generate a temporary age identity, stop only the monitoring project to
   capture a consistent age-encrypted snapshot of configuration, credentials,
   generated files and all four persistent volumes; restart the project.
3. Verify the encrypted archive's SHA-256, age header, timestamp, selected
   components and volume inventory. Stream-decrypt it completely into a
   **read-only validation pass**. Reject absolute/traversal paths, duplicate
   members, unknown volumes, file links, devices, missing essential files and
   unreasonably large archive entries.
4. Allocate **randomly named** scratch Docker volumes for restored
   configuration/state and for the four application volumes. They never use
   the names of any installed HomeAway volumes.
5. Stream-decrypt a second time directly into a network-disabled Python
   extractor with only those scratch volumes mounted. The plaintext tar is
   never written to disk; file permissions and UID/GID ownership are restored.
6. Check the restored configuration checksum, Grafana SQLite integrity,
   presence of the local Caddy public CA, and existence of expected secrets.
7. Start a **separate, unpublished VictoriaMetrics instance** using only the
   scratch metrics volume, on its own internal Docker network, and query the
   original synthetic time series through the VictoriaMetrics HTTP API.
8. Remove only the scratch container/network/volumes created during this run.
   Verify the original project's container IDs, volumes and old metric are
   unchanged and no rehearsal volumes remain.

The orchestration script requires a CI-specific environment guard and does
not provide an operator command that overwrites an installed Compose
project. The scripts intentionally remain in repository test tooling rather
than the released package.

## Current limits

This proves that a backup can be decrypted, safely extracted, and recover
**readable Grafana state and queryable VictoriaMetrics history**. It does not
yet prove a complete clean-host restoration of the HTTPS frontend and all
application services, database-schema migration across releases, Docker
image availability months later, or Windows client re-pairing.

It also does not restore the full household Pi with Pi-hole, VPN, systemd
agents or account management. A future production restore tool needs a
separate user-approved design for destination ownership, locked services,
key recovery, decommissioning old hosts, and rollback on failure.

The age key used for CI is ephemeral and discarded. A user's private key
must be stored independently from the backups and must not be included in
telemetry, CI logs, shared docs or a package.

Related: [modular lifecycle](MODULAR-LIFECYCLE.md) and
[developer architecture](DEVELOPER-ARCHITECTURE.md).
