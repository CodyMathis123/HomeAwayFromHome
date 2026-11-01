# Full-stack integration test: the two correct checkouts

> **State:** code and CI have passed on these feature heads, but the new embedded Grafana, Authelia, and multi-PC command paths have not been tested on the operator's actual Pi, iPhone or Windows PCs. These branches are not yet merged into `Production`. This guide coordinates existing runbooks; it does not automate deployment.

## The complete branch pair

| Repository | Check out this branch | Includes |
| --- | --- | --- |
| [HomeAwayFromHome](https://github.com/CodyMathis123/HomeAwayFromHome) | `chore/docs-and-legacy-cleanup` | PRs [#1](https://github.com/CodyMathis123/HomeAwayFromHome/pull/1) → [#2](https://github.com/CodyMathis123/HomeAwayFromHome/pull/2) → [#3](https://github.com/CodyMathis123/HomeAwayFromHome/pull/3) → [#4](https://github.com/CodyMathis123/HomeAwayFromHome/pull/4): DNS/VPN, telemetry intake, Grafana dashboards, read-only embed, authenticated PC power control, documentation |
| [SensorDash](https://github.com/CodyMathis123/SensorDash) | `chore/sensordash-telemetry-docs` | PRs [#13](https://github.com/CodyMathis123/SensorDash/pull/13) → [#14](https://github.com/CodyMathis123/SensorDash/pull/14): Windows controls-only UI, exporter, metadata, bounded SQLite retry spool, cleanup |

**Do not use the repositories' `Production` branches for this migration test** until the reviewed changes have been intentionally merged. The separate [SensorDash LAN/mobile PR #12](https://github.com/CodyMathis123/SensorDash/pull/12) is **not included**: it was built against the older Windows browser dashboard and has an overlap with the controls-only direction. The HomeAway browser UI already supplies the mobile-facing status/PC controls, and Grafana supplies the remote charts. Resolve whether any PR #12 pieces are still useful after this migration is validated; do not merge it blindly.

For a **fresh checkout**:

```bash
git clone -b chore/docs-and-legacy-cleanup https://github.com/CodyMathis123/HomeAwayFromHome.git
git clone -b chore/sensordash-telemetry-docs https://github.com/CodyMathis123/SensorDash.git
```

For **existing checkouts**, verify `git status` is clean, back up local configuration and use `git fetch origin` followed by `git switch --track origin/<branch-above>` (or `git switch <branch>` if already local). Don't discard local edits, and don't deploy from the wrong branch. On Windows, prefer the **successful PR #14 Windows Actions MSI artifact** to ad hoc local packaging; installer versions are issued by CI. Before installing any MSI, back up `%ProgramData%\\SensorDash`, preserve the current installer, and confirm the motherboard's fan-control fallback during service restart.

## Deployment and live validation order

1. **Baseline and backups:** Check the existing Pi's DNS/VPN and VictoriaMetrics state and preserve a rollback. Do not unnecessarily recreate Pi-hole/wg-easy when enabling optional services. Read [existing-Pi migration](EXISTING-PI-MIGRATION.md), [operations](OPERATIONS.md) and [security](SECURITY.md).
2. **Pi metrics first:** Deploy the HomeAway telemetry receiver/dashboards and verify its Pi-side schema/health **before** installing or enabling the newer Windows exporter. See [telemetry](TELEMETRY.md) and [live Pi/Windows validation](LIVE-VALIDATION.md).
3. **Grafana share:** Provision the compact `HomeAway · Portal Metrics` dashboard, create the explicitly **read-only externally shared link** from Grafana, and configure the token. It is a link-scoped *read-only view*, not Grafana admin authentication. See [embed setup](PORTAL-METRICS-EMBED.md).
4. **Private HTTPS / login:** Configure the three private DNS names, trust Caddy's **local root CA** on the iPhone/browser, initialize the Authelia local user, enroll TOTP and confirm unauthenticated users cannot access `/control/`. The intended gateway hostname is `https://homeaway.home.arpa:8443`, with auth at `auth.homeaway.home.arpa` and Grafana at `metrics.homeaway.home.arpa`. Use **both** `compose.yaml` and `compose.control.yaml` for optional gateway service changes; use the exact commands and rollback in [PC controls](PC-CONTROL.md).
5. **PC inventory and restricted commands:** Create/validate `/etc/homeaway-pcctl/hosts.json`, install the Pi's restricted host agent while preserving existing keys/Shortcut wrappers, and configure each Windows `pcctl` account with forced SSH commands and a verified host key. Use `scripts/add-pc.py` to add computers—no web source edits needed. Start with `pcctl HOST status`, verify read-only status in the HomeAway control page, then supervise Wake-on-LAN.
6. **Windows exporter:** When the hardware maintenance window permits, install the CI-generated SensorDash MSI. Confirm approved radiator/NVMe profiles, DDC pump tach-only behavior, fan fallback after sleep/resume, and local tray readings. Configure `Settings → Pi telemetry export` and verify actual readings flow into Grafana. Export credentials hot-reload without restarting the Windows collector; *MSI installation itself can restart the collector*. See [SensorDash export guide](https://github.com/CodyMathis123/SensorDash/blob/chore/sensordash-telemetry-docs/docs/telemetry-export.md).
7. **Read-only + power-control tests:** Compare current Windows sensor values to Grafana trends and missing-data gaps. Test Grafana iframe in an unsigned-in browser, while checking the separate Grafana admin page still requires login. Check Authelia TOTP/session behavior, unauthorized requests, confirmation prompts and the PC action audit history. Only when ready to risk lost work, test real sleep, restart and shutdown **one machine at a time**. Retest the existing iOS Shortcut. Confirm VPN access from cellular and verify no web-admin ports are router-forwarded.
8. **Rollback/acceptance:** Preserve the old Windows installer and prior host-side `pcctl` commands until success; disable export or stop the optional control containers independently of DNS and VPN if necessary. Keep credentials, peer keys and CA signing keys out of the repos. Full acceptance requires stable fan control even if the Pi or internet disappears.

### Important scope note: Face ID

The current `/control/` gateway is configured for **local password + TOTP two-factor authentication**. Authelia has WebAuthn/passkey capabilities, but **Face ID-backed passkey enrollment was discussed, not configured or end-to-end tested in this branch**. Do not make Face ID a deployment prerequisite or mistake a working password+TOTP flow for passkey support.

## After the successful live test

Avoid new feature branches simply to consolidate. Review and merge the existing HomeAway PR chain in dependency order **#1 → #2 → #3 → #4** and SensorDash **#13 → #14**, preserving their CI and any branch-protection gates. The histories are linear within each chain, so the later PRs will shrink as ancestors merge. Decide whether to close/rework SensorDash PR #12 based on the desired control-only local UI. Only then consider pruning older validation branches; do not delete divergent history as part of a live deployment.
