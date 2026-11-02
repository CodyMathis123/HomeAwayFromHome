# Phase status and avoiding overlapping implementations

As of November 2, 2026, the historical roadmap's Phase 3/4 planning branches
are **design references**, not deployable implementation branches. Current
Production already has HTTPS with Cloudflare DNS-01 under the private
`homeawayfromhome.house` names; Authelia/LLDAP and restricted PC inventory;
Pi/SensorDash telemetry using VictoriaMetrics and Grafana; an encrypted
full-state backup and a sanitized checksum status view.

## Ownership boundaries

- **SensorDash/Windows**: hardware discovery, sensor aliases, collector,
  local tray/UI, physical PWM/RPM/fan profiles, cooling system definitions
  and local fail-safe behavior.
- **HomeAway Pi**: identity and roles, computer inventory, allowlisted remote
  PC power operations, restricted host services, authenticated portal,
  observability integration and backup/orchestration.
- **VictoriaMetrics/Grafana**: historical time series, trends, metrics
  retention, dashboard composition and charts.
- **HomeAway Operations**: admin-only **summaries** of existing observations,
  limited operational commands only after separate security review; not a
  second monitoring database, fan controller, generic Docker control panel
  or competing Grafana frontend.

## Remaining acceptance gates

1. Phase 1: LLDAP invitations, user creation/deletion, and Authelia
   security-notification emails are live-verified via Resend. Remaining
   acceptance: independent password-reset redemption and second-user
   factor re-enrollment/error handling ([issue #9](https://github.com/CodyMathis123/HomeAwayFromHome/issues/9)).
2. Phase 2: real second PC enrollment, host-key pinning, verified commands,
   disable/archive/restore and offline test without risking the only PC.
3. Phase 3: finish safe read-only diagnostics and updated setup guidance;
   confirm external DNS/VPN and certificate renewal on actual devices.
4. Phase 4: operational health reporting, guarded backup cadence, off-device
   retention and optional reviewed maintenance workflows.
5. Recovery: successful full-state encrypted backup and off-device decryption
   established; isolated flash-drive restore intentionally deferred.

## Service scrape improvement in this branch

The existing fixed-query metrics observer now reports `up` on three
**already configured** VictoriaMetrics scraper jobs: Pi-hole exporter,
WireGuard exporter and VictoriaMetrics itself. No second exporter,
monitoring database, Docker socket, new host permissions or secret access
is introduced. A successful scrape is merely evidence that a metrics target
answered, **not** evidence of a healthy VPN tunnel or client DNS resolution.

To test this branch without touching running services:

```bash
python3 scripts/test-operations-service-health.py
python3 -m py_compile monitoring/diagnostics/app.py
sudo docker compose -f compose.yaml -f compose.control.yaml \
  -f compose.accounts.yaml -f compose.computers.yaml \
  -f compose.production.yaml --profile control --profile accounts \
  --profile metrics config -q
```

Only after passing: targeted rebuild/recreate of `metrics-diagnostics` and
`pc-control`, then admin browser check; verify viewer/operator remain denied.
Nothing here requires restarting Caddy, Pi-hole, WireGuard, Grafana or SensorDash.

## Incremental diagnostics rendering

The admin interface previously awaited **all** API requests before rendering
any results. In particular, SSH reachability can take longer than metrics,
certificate or backup checks, leaving many unrelated cards stuck on
`Checking…`. Requests now render independently as they complete, and each
fetch has a 12-second timeout. A timeout means `Unknown` with a source-specific
message, never evidence that the underlying service failed.

After deploying, navigate to Operations & Diagnostics, choose Refresh checks,
and confirm that unrelated cards populate while computer reachability may still
be pending. The page should finish with a check time or explicit authorization
failure, rather than leaving unrelated cards indefinitely in `Checking`.

## Diagnostics container startup regression (2026-11-01)

Live Pi logs showed `metrics-diagnostics` restarting with
`python: can't open file '/app/app.py': [Errno 13] Permission denied`.
The repository sources have intentionally restrictive Pi file permissions;
the original Dockerfile preserved those permissions when copying files,
while the container executes as UID/GID 65534. The image Dockerfile now uses
`COPY --chmod=0644 app.py certificates.py ./`, without changing host
permissions. Rebuild/recreate only `metrics-diagnostics`; check its logs and
HTTP endpoints from `pc-control`. The separate `serviceChecks` control-API
schema propagation is also fixed on this branch. Avoid merging until the
read-only metrics and certificate cards populate without 503 errors.

## Last-known diagnostics during refresh

The admin page now uses **stale-while-revalidate** rendering: pressing Refresh
keeps existing cards and their last-observed values in place. Each independent
request replaces only its section once completed. If a request times out or
returns an error, the prior values remain visible with an explicit dashed
**last known** label and the summary counts unavailable sources. A successful
request clears the marker. Revocation/session loss still clears protected
status and hides the admin page rather than preserving privileged observations.

Browser acceptance: first capture known populated readings; click Refresh
and verify cards do not flip to `Unknown`/`Checking`; simulate a diagnostics
request failure and verify the original value remains but acquires
`last known`; restore connectivity and verify a successful refresh clears
that marker. This is not durable server-side caching; reloading the entire
browser page starts from initial unknowns until observations return.

For the consolidated permission/network/gateway-overlay audit and ranked follow-ups, see [November 2 production housekeeping](PRODUCTION-HOUSEKEEPING-2026-11-02.md).
