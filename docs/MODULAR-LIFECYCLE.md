# Optional-component lifecycle and encrypted backup

**Experimental draft PR #104.** This workflow applies **only** to the isolated
`homeaway-cooling` Compose project on a fresh test Linux host, not the
existing full household HomeAwayFromHome deployment.

## Operating model

- `homeaway-cooling change --components ...` is a read-only difference/impact
  plan unless **both** `--apply` and a recent verified `--backup-file` are supplied.
- The first supported live changes are `linux-host-monitoring`,
  `monitoring-diagnostics` and `home-portal`. The installer automatically
  adds and removes shared dependencies subject to the component catalog.
- Enabling or disabling `cooling-monitoring` remains blocked until the
  sender/token, upstream CA and TLS cutover are designed and tested.
- Changing DNS, VPN, accounts, remote PC management or host services is not
  supported in this experimental modular backend.
- Every change retains the same **four named volumes**, including
  VictoriaMetrics history, Grafana state and Caddy CA. Existing Portal and
  SensorDash secrets are retained even when their services are removed.
- The operator explicitly confirms a brief service interruption; Compose
  reconciles only this project's containers, with `--remove-orphans`
  **without** `-v`. There is no pruning of other projects.
- Configuration and generated definitions are checkpointed locally before
  a change. If Compose reconciliation fails, the old configuration and
  service graph are restored on a best-effort basis; manual recovery can
  still be needed if Docker or the host fails during rollback.

## Age-encrypted full-state backup

Install an age implementation (for example, the `age` package on Debian).
Generate an identity on a trusted machine and **keep its private key
separate from the Linux server being backed up**. Use its public recipient
(`age1...`) for encryption.

```bash
homeaway-cooling backup

# Follow your age tool's instructions to make and securely store a recipient.
sudo homeaway-cooling backup --recipient age1YOUR_PUBLIC_RECIPIENT --apply
```

`backup` is read-only by default. With `--apply`, it asks for typed
confirmation, stops only the installed monitoring containers, reads the
scoped configuration/secrets/generated definitions and all **four named
volumes** into a tar stream, encrypts that stream directly with age and
restarts the services. The snapshot helper has no network, no published
port, read-only mounts and no write access to volume contents. Its
capability to read files owned by Grafana is limited to that temporary,
offline snapshot process.

The resulting `/var/lib/homeaway-cooling/backups/*.tar.age` is mode 0600;
a neighboring `.json` file records selection, timestamp, checksum,
volume inventory and local config hash. There is **no plaintext archive
on disk**. A backup is only eligible as a change prerequisite for one
hour, if its contents/checksum and installed selection have not changed.

**Important:** A checksum manifest is not a digital signature, and an
encrypted file is not proof the private key is safely stored. CI performs
a real decryption/listing check. You must also store the archive, manifest
and private key in independent locations, check disk space, and periodically
practice a restore. The backup does **not** capture all full-household Pi
containers, host systemd agents or your router settings.

## Safe, explicitly confirmed change

```bash
# Dry-run: no file, secret, container or service changes.
homeaway-cooling change \
  --components cooling-monitoring,linux-host-monitoring,home-portal --json

# Use the latest successful backup returned by the preceding backup command:
sudo homeaway-cooling change \
  --components cooling-monitoring,linux-host-monitoring,home-portal \
  --backup-file /var/lib/homeaway-cooling/backups/YOUR_BACKUP.tar.age --apply
```

The target must still contain a monitoring source; the resolver checks
required dependencies and exact service mapping. Before writing any
change, the CLI verifies the age backup reference, previous installed
definition and that all existing monitoring services are running.

The CLI records a root-only rollback checkpoint in
`/var/lib/homeaway-cooling/rollback-checkpoints/`, atomically updates
managed files, validates the exact newly selected Compose graph, then
reconciles containers. It waits for the expected services to be running.

Removing an optional Portal **does not** remove its password, historical
metrics or shared backend. A future dedicated credential-revocation
operation can handle deliberate permanent deletion, but is not part of
component removal.

To remove optional features later, take **a new encrypted backup of the
new installed selection first**, then preview and explicitly confirm the
change. Old backup metadata will not satisfy the new selected-feature gate.

## Restore limitations and next gates

This iteration verifies that age-encrypted archives actually decrypt and
contain the expected files, and that metrics survive add/remove round trips.
It does **not** claim that automatic volume restoration is safe or complete.
Restoration onto a blank host, UID/GID preservation, archive extraction safety,
encryption key recovery and rollback across real database schema changes need
a separate, isolated restore pipeline before automated restore is offered.

The rollback checkpoint is **not** a substitute for an off-host full-state
backup: it restores only managed config and the previous service topology.
Data in persisted volumes isn't automatically rolled back if an application
version performs incompatible schema writes.

No workflow can merge, deploy or alter your working Raspberry Pi without a
separate explicit decision.
