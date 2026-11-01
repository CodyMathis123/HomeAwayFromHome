# Phase 4 — backup maintenance (preview-only)

This first increment adds a **weekly read-only preflight**, not unattended
full-state backups. Existing `backup-full.sh --apply` still requires a human,
because it temporarily stops DNS, VPN, HTTPS and PC management. The preflight
neither stops services nor creates/deletes an archive.

## Preview immediately (no change)

```bash
cd ~/HomeAwayFromHome
sudo python3 scripts/backup-maintenance.py --plan
sudo bash scripts/install-backup-preflight.sh --plan
```

The checker calls only `backup-full.sh --plan` and
`plan-backup-retention.py --plan --keep-local 3`. Neither may delete archives.
Results are limited to pass/attention; no secret paths, encryption recipients
or backup names are sent to the browser.

## Optional weekly preflight

After checking the installer plan:

```bash
sudo bash scripts/install-backup-preflight.sh --apply
```

This installs two systemd units and reloads systemd. **The timer is NOT
enabled or started.** To enable just the read-only checks:

```bash
sudo systemctl enable --now homeaway-backup-preflight.timer
sudo systemctl list-timers homeaway-backup-preflight.timer
```

Default schedule: Sundays at 03:00 Pi local time. The timer only invokes
`backup-maintenance.py --publish`. To test now without waiting:

```bash
sudo python3 scripts/backup-maintenance.py --publish
```

Operations & Diagnostics displays sanitized preflight, retention, timer and
last check information after the `pc-control` UI container is rebuilt.
No web endpoint can run shell commands, stop services, delete archives,
schedule a backup or access `age` keys.

To stop future weekly preflight checks:

```bash
sudo systemctl disable --now homeaway-backup-preflight.timer
```

## Deferred before unattended backups

Do not schedule `backup-full.sh --apply` yet. First complete an isolated
restore and off-device backup verification; decide whether short DNS/VPN
outages are acceptable, obtain explicit maintenance-window approval, and
design failure alerting and recovery checks. Archive retention is advisory;
automatic deletion is not implemented.
