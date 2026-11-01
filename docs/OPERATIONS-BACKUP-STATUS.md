# Operations: read-only encrypted backup status

This feature adds **bounded checksum evidence** to the existing administrator-only
Operations & Diagnostics page. It never mounts the protected backup directory
into a container, never exposes file names, hashes, archive bytes, key identities
or the age recipient, and never runs backup commands from a browser.

## Data flow

An explicit, local root-run publisher enumerates only files matching the
fixed `full-YYYYMMDDTHHMMSSZ-NNN.tar.age` naming scheme under the private
`/var/backups/homeawayfromhome` directory, checks the expected SHA-256
sidecar against the archive bytes, and writes a small sanitized JSON summary.
It does not decrypt, archive or mutate original backup files.

The status file is written atomically to
`data/control/backup-status/status.json`, under a dedicated root-owned
directory. `compose.production.yaml` bind-mounts that directory **read-only**
into `pc-control` at `/backup-status`. Only the already authenticated
`/control/api/admin/backup-status` GET route returns its strict schema,
and the server requires the current role to be `admins`.

The browser considers data older than 24 hours **stale evidence** and
distinguishes a checksum-verified archive from a verified decryption or
clean-host restoration. There is no claim of automatic scheduling,
off-device transfer, or actual restore.

## Initial validation (no service outage)

Use a clean feature checkout. From the Pi:

```bash
cd ~/HomeAwayFromHome
python3 -m unittest discover -s scripts -p 'test-publish-backup-status.py' -v
python3 -m py_compile control/api/app.py scripts/publish-backup-status.py
sudo python3 scripts/publish-backup-status.py --plan
```

The plan reads encrypted archive/checksum bytes, **but does not write a
status file** or restart anything. If the archive hash is valid, it prints
`checksum verified: True`.

## Explicit publish and targeted deployment

```bash
sudo python3 scripts/publish-backup-status.py --publish

sudo docker compose \
  -f compose.yaml -f compose.control.yaml \
  -f compose.accounts.yaml -f compose.computers.yaml \
  -f compose.production.yaml \
  --profile control --profile accounts --profile metrics config -q

sudo docker compose \
  -f compose.yaml -f compose.control.yaml \
  -f compose.accounts.yaml -f compose.computers.yaml \
  -f compose.production.yaml \
  --profile control --profile accounts --profile metrics \
  up -d --no-deps --build pc-control
```

Only the `pc-control` container is recreated, to load the new static page,
endpoint and read-only summary mount. No DNS, VPN, Caddy, Authelia, Grafana,
VictoriaMetrics or telemetry collector needs restarting.

Visit `https://home.homeawayfromhome.house:8443/control/#setup-readiness`
as an admin, click **Refresh checks** and check the backup card. A viewer or
operator must not be able to access the admin-only endpoint or section. The
rest of the existing PC control and certificate checks must still function.

## Limits and next step

The status is a published **point-in-time** checksum observation. If the
publisher hasn't run in the past day the page should say evidence is stale.
An archive can have a valid checksum yet be undecryptable without the proper
private identity, and a decryptable archive may still fail to restore.
Off-device copy and isolated restore checks are still manual.

The publisher is deliberately not scheduled and no privileged socket,
container control API, generic shell access, archive deletion command or
private key mount has been introduced. Evaluate explicit post-backup
publishing only after supervising the post-stop recovery flow.
