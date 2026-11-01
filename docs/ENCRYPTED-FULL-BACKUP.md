# Encrypted full-state backup: proposed operator-run workflow

**Status: draft, not yet validated or executed on the Pi.** Existing
`scripts/backup.sh` is unchanged. Do not schedule or run `--apply` until
the read-only plan and the operator's recovery-key and network-access
requirements are reviewed.

## Design

`scripts/backup-full.sh` archives the currently deployed application's
`.env`, `secrets/` and `data/` plus fixed host-managed state:
`/etc/homeaway-pcctl`, `/home/pcctl/.ssh`,
`/var/lib/homeaway-pcinventory`, three installed /opt agent directories,
their six systemd service/socket units and two tmpfiles configurations.

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
