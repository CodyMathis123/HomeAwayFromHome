# Unified Pi management command

HomeAwayFromHome uses Docker Compose for the application stack, plus a few
restricted **host-side systemd sockets** for PC control and hardware
observation. This lightweight management command consolidates the safe
operator entry points without replacing Compose, adding Kubernetes, using a
Docker socket from the portal, or introducing unattended updates.

## Install the command on an existing Pi

On a **reviewed** `Production` checkout:

```bash
cd ~/HomeAwayFromHome
bash scripts/install-homeaway-command.sh --plan
sudo bash scripts/install-homeaway-command.sh --apply
homeaway status
```

The installer creates or updates **only** `/usr/local/bin/homeaway`. It is a
small root-owned launcher pointing to the checked-out
`scripts/homeaway.py`. It does **not** change Docker containers, secrets,
systemd services, DNS, WireGuard, Caddy, or the router.

The launcher is tied to the checkout location. If the checkout moves, rerun
the installer from the new location. It follows the checked-out Python script
after reviewed Git updates, so there is no second application installation
to keep synchronized.

This is an installable command and deployment facade, **not yet a .deb
package**. That can be added later if there is demand for machine-wide
installations independent of a Git checkout.

## Command reference

| Operation | Preview / read-only | Apply |
| --- | --- | --- |
| Service and host socket status | `homeaway status` | N/A |
| Configuration and port diagnostics | `homeaway doctor` | N/A |
| New Pi config preparation | `homeaway install` | `homeaway install --apply` |
| Portal and PC UI | `homeaway update` | `homeaway update --apply` |
| Only portal | `homeaway update --target portal` | `homeaway update --target portal --apply` |
| Only PC controls | `homeaway update --target computers` | `homeaway update --target computers --apply` |
| Grafana dashboards | `homeaway update --target dashboards` | `homeaway update --target dashboards --apply` |
| Full encrypted backup | `homeaway backup` | `homeaway backup --apply --recipient age1...` |
| Core DNS/VPN updates | `homeaway update --target core` (guidance only) | Deliberately not automated |

The default mode is **plan**, never apply. Every apply requires a real
interactive terminal and a typed confirmation phrase. No `--yes` bypass is
provided. Run `homeaway` as your regular Pi login, **not with sudo**.
The scoped underlying scripts request elevated privileges when necessary.

### Targeted application updates

`homeaway update` calls the existing `scripts/deploy-ui.sh` script. Its
checks still require a clean `Production` branch, the expected Compose
project, a known current gateway Caddyfile mount, valid optional SMTP
overlays, and already-running selected services. The apply step fetches
and fast-forwards the branch; it does not force-reset local edits.

For `--target dashboards`, the same guarded script selects **only**
`grafana` with `docker compose up -d --no-deps --build grafana`. The
repository's provisioned dashboard JSON files are bind-mounted into the
container. This approach can refresh dashboards without touching
VictoriaMetrics, telemetry ingestion, DNS, WireGuard or identity containers.
Check the actual dashboard and Grafana logs afterward: a running container
is not proof that every panel query is healthy.

`--target core` intentionally remains plan-only. The existing
`scripts/update.sh` and the manual maintenance instructions in
[Operations](OPERATIONS.md) remain the controlled procedure for Pi-hole and
WireGuard, including backup, outage planning and rollback. Do not use a
generic unscoped `docker compose up -d` on the live project.

**Important:** the `--plan` command shows *last-fetched* production state.
It does not fetch the remote branch, so it cannot guarantee there are no
incoming changes. Review the incoming diff and release notes before
confirming apply.

### Encrypted backup

`homeaway backup --plan` delegates to
`sudo bash scripts/backup-full.sh --plan` and remains read-only.

`homeaway backup --apply --recipient age1...` performs a **disruptive**
encrypted full-state backup using the existing host and Compose quiescence
and restoration logic. The stored archive contains private application
configuration, identities, and host PC trust material; protect the
recipient's private key separately. VictoriaMetrics historical samples,
the OS image, and router settings are excluded. Copy the archive and
checksum off-device. An isolated restore is still required before
calling disaster recovery proven.

### Fresh Pi setup

After flashing Raspberry Pi OS Lite 64-bit, setting up SSH, a reserved
LAN address and installing Docker Engine + Compose, clone `Production`
and install the command above. Then:

```bash
homeaway install               # read-only preparation plan
homeaway install --apply       # prepares local .env and generated secrets only
# Edit .env and check private network, domain and VPN settings.
homeaway doctor
```

`homeaway install` refuses to reinitialize an existing `.env` or state
directories and **never starts containers**. New-Pi DNS/VPN rollout,
authentication, certificates, peer creation, service enrollment and
router cutover remain intentionally staged according to
[Installation](INSTALL.md). These steps cannot safely be collapsed into
a silent one-click installer on a household DNS server.

## Testing and limitations

```bash
python3 -m unittest discover -s scripts/tests -v
python3 -m compileall -q portal scripts
bash -n scripts/install-homeaway-command.sh scripts/deploy-ui.sh
docker compose config -q
```

Do not apply the manager from this feature branch on the live Pi; the
deployer intentionally requires `Production`. No container or real VPN
deployment is performed by CI. Prior to production use, verify installed
command path, launcher permissions, targeted Grafana update, operational
status, existing user/PC flows, DNS resolution, VPN handshake, encrypted
backup generation, and restoration strategy.

### Uninstall the launcher only

After verifying that `/usr/local/bin/homeaway` contains the
`# homeaway-managed-command` marker, remove that launcher manually as root.
This does not remove containers, accounts, volumes, host systemd services,
secrets or telemetry history. There is deliberately no destructive
`homeaway uninstall` command.
