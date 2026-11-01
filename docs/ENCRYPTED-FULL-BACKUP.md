# Encrypted full-state backup: proposed operator-run workflow

**Status: first supervised Pi backup and off-device decryption/listing completed on November 1, 2026; isolated full restore remains outstanding.** Existing
`scripts/backup.sh` is unchanged. Do not schedule or run `--apply` until
the read-only plan and the operator's recovery-key and network-access
requirements are reviewed.

## Design

`scripts/backup-full.sh` archives the currently deployed application's
`.env`, `secrets/` and `data/` plus fixed host-managed state:
`/etc/homeaway-pcctl`, `/home/pcctl/.ssh`,
`/var/lib/homeaway-pcinventory`, three installed /opt agent directories,
their six systemd service/socket units and two tmpfiles configurations.

**Recovery-first scope:** `data/victoria-metrics/` is deliberately excluded from all new full-state archives. VictoriaMetrics sample history is disposable and starts fresh on the recovered host. This does **not** exclude `data/grafana/` (dashboard preferences and Grafana local configuration), `data/pihole/`, `data/control/`, or other persistent application state. The repository's provisioned dashboards are restored from Git.

It records a small version/commit metadata file within the encrypted archive,
uses tar's ACL/xattr/numeric-owner preservation, and streams tar directly
into `age` encryption. **No plaintext archive is written to disk.**
Encrypted output and its SHA-256 checksum remain on the Pi pending **explicit**
off-device transfer. The private decryption identity must be held separately,
never embedded in this repo, the backup, a log, or a chat.

The current implementation stops all running Compose project containers,
then stops the PC-control/inventory/verification sockets and writer services
that were active beforehand. Only after quiescence does it collect the host
files. The shell exit trap attempts to restart those previously active units
and containers, including after a partial failure. The snapshot does NOT
include the boot partition, OS packages/configuration outside the listed
paths, router data, or connected Windows clients. Full machine recovery
still requires a fresh Pi OS installation and the repository/host installers.

## Before trying a backup

Use **LAN-side SSH**, not a session that depends on the Pi's WireGuard VPN,
because this procedure intentionally interrupts the VPN and Pi-hole.
During `--apply`, remote computer actions, portal/login, DNS and metrics
may be unavailable. Ensure that the Pi is reachable without its own DNS.
Do not choose a time when computer inventory is being edited. This script
does not shut down or otherwise modify Windows computers.

The external `age` program and a separately protected X25519 age key pair
are required. For example, create a decryption identity on a **different
trusted machine**, keep it backed up independently, and provide only its
public `age1...` recipient on the Pi. Do not generate a new encryption key
every backup without preserving previous private identities.

## Read-only plan and static checks

On a clean feature branch checkout:

```bash
bash -n scripts/backup-full.sh
bash -n scripts/backup-recovery.sh
bash scripts/test-backup-recovery.sh
sudo python3 scripts/plan-host-backup.py --plan
sudo bash scripts/backup-full.sh --plan
```

These static checks, mocked recovery test, and the plan do not stop services or create backups. The mock test covers previously active socket/service restoration, stopped versus already-running containers, and continuing recovery after a simulated failure. It does not prove actual Pi service quiescence or archive restoration.

Once `age` and `age-keygen` are installed, test the cryptographic tools
without an outage or accessing any HomeAway secrets:

```bash
bash scripts/test-backup-encryption.sh
```

It creates one temporary local test key and synthetic payload, encrypts,
decrypts, verifies their SHA-256 hashes, and removes the temporary files.
This smoke test is **not** a backup or a recovery-key custody check; the
production key must be generated and kept on another trusted device.

## Explicit operator apply — NOT YET ACCEPTED

Once reviewed and approved, with `age` installed and the public recipient
available, the planned invocation is:

```bash
sudo bash scripts/backup-full.sh --apply --recipient age1YOUR_PUBLIC_RECIPIENT
```

Never paste your `AGE-SECRET-KEY-...` decryption identity in this command.

After any apply, independently verify the running containers and the
six host-side units. Confirm expected services work, and verify the
encrypted archive checksum. Copy the encrypted archive and checksum
off the Pi. A backup stored only on the Pi does not protect against
device/SSD failure.

## First live backup acceptance (November 1, 2026)

The operator successfully created an encrypted full-state archive with 14 Compose containers and six active host-side units in the planned scope, confirmed the containers and sockets restarted, copied the archive off the Pi, and reported successful Windows decryption and archive listing. The protected on-Pi original was retained and temporary SCP transfer copies removed. The script's simulated recovery tests passed earlier. This establishes a readable encrypted snapshot, **not** a clean-host restore, real power-state transition test, or guaranteed unattended backup reliability.

## Read-only local retention inventory

```bash
sudo python3 scripts/plan-backup-retention.py --plan --keep-local 3
```

This lists encrypted full-state archives in the root-only directory, labels the newest three KEEP, and flags older archives for review **without deleting anything**. It checks whether corresponding checksum files exist, not their contents. Before deleting an old local archive, independently verify a secure off-device copy and decryption with the saved recovery identity. There is deliberately no automatic pruning or deletion command.

## Fast rebuild after storage/host failure

Target recovery: restore core services and access, not historical graphs.

1. Prepare a replacement Pi/boot drive with Raspberry Pi OS Lite arm64, Docker Compose, `age`, Git, and LAN connectivity. Use a LAN-side SSH session and restore the reserved Pi LAN address before switching clients over.
2. Clone `HomeAwayFromHome` from GitHub. The archive contains a commit identifier in `backup-metadata.txt`; use the corresponding reviewed Production code. **Source code and the OS are not inside the archive.**
3. Transfer the encrypted archive and checksum from **off-device** storage. Verify checksum before decryption. The private `age` identity must be available separately.
4. With HomeAway containers and host management sockets **stopped**, decrypt and extract the archive into the proper repository root and absolute host paths. Verify owners, service account UIDs/GIDs, executable permissions and unit locations first. **Never extract over the live Pi.** The archive uses numeric ownership and contains private credentials.
5. Run the project host-agent installers/recovery checks and Compose validation against the restored state. Start the Compose services, restore the configured Caddy/TLS, LLDAP/Authelia, DNS and VPN state, and test login and a known WireGuard peer. Restore PC-control inventory/trust and test a known host.
6. VictoriaMetrics starts with an empty storage directory and ingests new samples. Do not attempt to recover previous metrics. Check dashboard availability, not historical graph completeness.
7. Point router DHCP/DNS to the new Pi only **after** verifying DNS resolves, remote access and the portal work.

The exact isolated-restore exercise is **not yet complete**. The existing
backup script still pauses the services for a consistent snapshot. To reduce
future backup time and outage duration, investigate an app-consistent backup of
the small databases without stopping the full stack; do not assume this is safe
without a restore test.

## Restore validation (separate milestone)

On an isolated Linux machine or spare disk with the private age identity
available locally, decrypt **into a pipe** to inspect the tar manifest
before extraction:

```bash
age -d -i /private/path/to/identity.txt -o - full-...tar.age | tar -tf -
```

This reads archive filenames only; it does **not** install anything. Never
extract onto the live Pi or overwrite an active inventory. Verify recovery
of the application and host-state files, owners/modes, installed agents,
directory service, certificates, and fixed sockets on disposable storage.
Recreate system users/groups first; `--numeric-owner` alone does not
guarantee that a fresh Pi has the expected identity/group mappings.

A decryptable archive, checksum, and offline listing are useful checks but
not a tested restore. Do not claim disaster recovery is complete until
a clean-host recovery exercise succeeds.
