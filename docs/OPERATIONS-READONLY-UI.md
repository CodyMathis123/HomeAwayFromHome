# Operations & Diagnostics — first read-only UI milestone

This PR reuses the existing administrator-only `/control/#setup-readiness`
view rather than creating another dashboard or granting a container Docker or
host access. Its sidebar label becomes **Operations & diagnostics**.

## Currently available read-only observations

- Pi exporter measurements and VictoriaMetrics storage volume free space
- SensorDash sample freshness, with no inference that a PC is powered on
- Private DNS, WireGuard management listener and Grafana listener
- Account/computer administration capabilities
- TLS leaf certificate expiry, with trust/renewal limitations clearly stated

The new **Backup & recovery** section explains that full-state backups are
operator-run and cannot be verified inside the web process, which has no
access to the root-owned backup directory, Docker socket or private key.
It lists only fixed read-only commands for checking scope, retention and
permissions on the Pi. It does not add a generic shell API, scheduled
backups, privileged helper, or mutation route.

## Safety and acceptance

- Anonymous/non-admin users must remain unable to access the setup-readiness
  section or admin-only diagnostic endpoints.
- No new secrets, backup filesystem mounts, Docker socket or systemd access.
- This change alters only the existing static admin HTML; the existing
  authentication and server-side role checks are untouched.
- Browser check: after targeted `pc-control` rebuild, login as admin and
  navigate to **Operations & diagnostics**; verify Pi and certificate checks
  still load and backup cards explicitly say **Not monitored**.
- Check a viewer/operator account cannot open the admin section.
- Do not run backup commands from the web UI; read-only terminal inspection
  and a future constrained host agent are separate milestones.

## Follow-up milestones

1. Build a bounded, read-only host-side summary exporter containing backup
   completion timestamp, archive verification state and key-expiry diagnostics,
   without reading keys or returning archive filenames/secret details.
2. Pass that summary over a narrow, read-only socket or trusted channel; do
   not mount the root-owned backup directory in the browser service.
3. Add robust last-known-good/freshness labels and retention status to the
   existing operational view.
4. Investigate certificate observation hostname drift introduced during the
   `.house` migration, then verify against the live DNS-01 gateway.
5. Review controlled maintenance actions separately with exact authorizations
   and per-action audit logs.
