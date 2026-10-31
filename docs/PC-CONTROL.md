# HomeAway authenticated PC control

**Status:** implemented as an opt-in, reviewed feature branch; no changes are made to your Pi, Windows PCs, DNS, or VPN until you apply this runbook. The host-side `pcctl` service and its browser controller are separate components. All network and authentication steps require real-machine validation.

## Design (no duplicate control system)

```text
iPhone Shortcut -- restricted SSH key --> pcctl-dispatch --> pcctl CLI --+
                                                                         |
HomeAway HTTPS :8443 -> Caddy -> Authelia 2FA -> /control/ -> pc-control |  Pi host
                                             (no SSH keys in Docker)    |
                                           Unix socket (pcctl group) ---+
                                                                         |
                                            one JSON host inventory <----+
                                                    |                 |
                                                  Wake UDP        Windows OpenSSH
                                                    |                 |
                                                 MAC+WOL       non-admin pcctl +
                                                            Windows ForceCommand
```

HomeAway's existing HTTP :8088 portal remains read-only. HTTPS :8443 serves the same HomeAway page plus an authenticated `/control/` section. The controller does not run arbitrary commands, store SSH keys, have host-network privileges, mount the Docker socket or expose its own TCP port. The Pi's socket-activated agent executes only `status`, `wake`, `sleep`, `restart`, `shutdown`, for IDs in a validated JSON inventory. All changes are explicit POST requests, protected by Authelia forward-auth, exact Origin checks, a non-simple custom header, and confirmations for restart/shutdown. Calls are logged to a bounded local audit file.

Default Authelia policy demands **two factors** for the `/control` path (local password plus enrolled TOTP). The rest of HomeAway's read-only portal does not require a login. Signed-in session cookies are handled by Authelia, never by custom portal JavaScript. Status checks only test an SSH TCP listener; a command response means *requested*, not proven completed.

## 0. Prerequisites / decisions

- You already have a Pi with the reviewed HomeAway Compose deployment and WireGuard LAN reachability. Do not open port 8443, 3000, 8088, 22 or 53 on your router. Only VPN UDP 51820 is public.
- Pi LAN IP is reserved by router. Verify Pi's LAN subnet and each Windows PC's wired/wireless interface MAC. Wake-on-LAN may require BIOS/UEFI, NIC/driver and Windows Fast Startup settings; Wi-Fi WOL is device-dependent.
- Choose private DNS hostnames `homeaway.home.arpa`, `auth.home.arpa`, `metrics.home.arpa`. Add these to Pi-hole Local DNS pointing to **the Pi's actual LAN address**. Make sure VPN clients use this Pi DNS; test hostname resolution on LAN and over VPN. You may use different private hostnames but must update env/bootstrap accordingly.
- Caddy serves internally issued HTTPS certificates. Clients (including iOS) must explicitly **trust Caddy's local root CA**, after verifying it was obtained privately from your own Pi. Accessing an untrusted HTTPS page by clicking through certificate warnings is not an acceptable workaround for the control UI. This is deliberately not a public Let's Encrypt exposure.
- The existing `work` and `gaming` commands and your iOS Shortcut remain supported. Their original SSH keys/authorized_keys must be backed up before migration.
- If Windows `pcctl` hardening is already installed and functioning, **do not blindly run its setup script again**. Review and retain that configuration. The Pi host install detects existing wrappers and preserves them unless explicitly told to replace them.

## 1. Set up the single inventory on the Pi (easy to add PCs)

Copy and edit the example (use real MACs, no placeholder string):

```bash
sudo install -d -m 0755 /etc/homeaway-pcctl
sudo cp control/pcctl/hosts.example.json /etc/homeaway-pcctl/hosts.json
sudo nano /etc/homeaway-pcctl/hosts.json
python3 -c 'import sys; sys.path.insert(0,"control/pcctl"); from core import load_hosts; print(load_hosts("/etc/homeaway-pcctl/hosts.json").keys())'
```

Schema (a single source of truth, not hardcoded computers):

```json
{
  "hosts": [
    {"id":"gaming","name":"Gaming Desktop","address":"192.168.1.88",
     "mac":"aa:bb:cc:dd:ee:ff","broadcast":"192.168.1.255",
     "ssh_user":"pcctl","ssh_port":22}
  ]
}
```

The sample MAC above is illustrative; do not copy it to a real configuration. `id` is the stable name used by both the browser and Shortcut. Editing or adding inventory entries requires no application rebuild or Pi controller restart: each request rereads the inventory. Existing entries should not be renamed casually because Shortcut commands refer to the ID. The dashboard lists all configured entries automatically.

To add a third Windows computer *later*, find its IPv4 and Ethernet MAC and run:

```bash
sudo python3 scripts/add-pc.py --id living-room --name "Living Room PC" \
  --ip 192.168.1.120 --mac aa:bb:cc:dd:ee:ff --broadcast 192.168.1.255
```

Then do the Windows setup in §3. The command validates the inventory, rejects duplicate IDs and invalid addresses, and replaces the JSON file atomically. Do not use the example address/MAC unless they are actually the new computer's values. Up to 64 entries are supported.

## 2. Install or migrate the Pi-side restricted agent

Back up the existing `/usr/local/bin/pcctl`, `/usr/local/bin/pcctl-dispatch`, `/etc/pcctl.conf`, `/home/pcctl/.ssh`, and any original service definitions **off the Pi** before replacing anything. Your iPhone's existing authorized_keys forced command can continue targeting `/usr/local/bin/pcctl-dispatch`.

```bash
bash scripts/install-pcctl-host.sh                  # preview, makes no changes
sudo bash scripts/install-pcctl-host.sh --apply     # installs agent, preserves existing wrappers
```

To switch legacy wrappers to the data-driven implementation, only after verifying the inventory and backups:

```bash
sudo bash scripts/install-pcctl-host.sh --apply --replace-legacy
```

Existing wrappers are backed up with timestamp suffixes. This install never overwrites the SSH private key or the iPhone's `authorized_keys` file. The host service uses the existing `pcctl` Linux account, its known_hosts and SSH key. If those don't exist, generate one dedicated Pi-to-Windows key and retain it off-device as a secret:

```bash
sudo -u pcctl mkdir -p /home/pcctl/.ssh
sudo -u pcctl ssh-keygen -t ed25519 -N '' -f /home/pcctl/.ssh/id_ed25519
sudo -u pcctl cat /home/pcctl/.ssh/id_ed25519.pub
```

Never overwrite a key that already exists. Do **not** use `StrictHostKeyChecking=no`. Validate each Windows host key using a trusted channel before putting it in `/home/pcctl/.ssh/known_hosts` (one entry per IP, and proper port-specific format for non-default ports).

After service installation, check:

```bash
sudo systemctl status pcctl-agent.socket
id -g pcctl
sudo -u pcctl /usr/local/bin/pcctl gaming status
sudo -u pcctl /usr/local/bin/pcctl work status
```

Do not use Wake, Sleep, Shutdown, or Restart for setup validation until you're ready to perform real actions.

## 3. Prepare each Windows computer

Perform these steps on **each** Windows PC (including additional computers). None of them need a HomeAway desktop app.

1. Install the supported Windows OpenSSH Server optional feature and ensure `sshd` works. In Administrator PowerShell, run `Get-Service sshd`; install it via Settings → System → Optional features if missing.
2. On the Pi, obtain the dedicated *public* SSH key: `sudo -u pcctl cat /home/pcctl/.ssh/id_ed25519.pub`. Transfer only the public key to the Windows computer into a temporary file, e.g. `C:\Temp\pi-pcctl.pub`. Do **not** transfer the private key.
3. Review `control/windows/Install-PcctlWindows.ps1` and `control/windows/dispatch.ps1`. If this Windows PC is already securely configured for your existing `pcctl` solution, inspect and retain that setup instead of running a second installer.
4. From Administrator PowerShell in the repository checkout, for a new PC:

   ```powershell
   .\control\windows\Install-PcctlWindows.ps1 -PiAddress 192.168.1.43 -PiPublicKeyFile C:\Temp\pi-pcctl.pub -WhatIf
   .\control\windows\Install-PcctlWindows.ps1 -PiAddress 192.168.1.43 -PiPublicKeyFile C:\Temp\pi-pcctl.pub
   ```

   **Replace the Pi address** with its real reserved address. The script creates a non-admin local `pcctl` user with its own password, adds a key-only, no-TTY, no-forwarding SSH `Match User pcctl` block with a fixed Windows PowerShell dispatcher, restricts the OpenSSH firewall rule to the Pi address, and restarts `sshd`. It backs up `sshd_config` first and refuses to override an existing pcctl Match block. Restricting a pre-existing Windows SSH rule can also affect other SSH clients; assess that before applying.
5. For **sleep**, place the trusted Sysinternals `PsShutdown.exe` at `C:\ProgramData\pcctl\PsShutdown.exe`. The Windows `pcctl` account must have OS rights for the requested suspend/shutdown operations (e.g. SeShutdownPrivilege); don't give it administrator access just to make the feature work. Confirm with a supervised local test first. Wake requires the NIC/firmware configured for WOL.
6. From Pi, confirm SSH host-key fingerprint out-of-band and enroll it in the dedicated known_hosts file (no auto-trust). Verify `sudo -u pcctl ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -i /home/pcctl/.ssh/id_ed25519 pcctl@192.168.1.88 status` returns `ready`. Validate each new PC independently.

The Windows dispatcher accepts **only** status, sleep, restart, and shutdown. Wake is performed by the Pi with a UDP WOL packet. There is no arbitrary SSH shell, no Windows admin account in the dashboard, and no shared Windows credential.

## 4. Local authentication and HTTPS

Use three Pi-private DNS records. On the Pi's existing HomeAway checkout, add the following values to `.env` after reviewing your reserved address and group ID:

```dotenv
HOMEAWAY_DOMAIN=homeaway.home.arpa
HOMEAWAY_AUTH_DOMAIN=auth.home.arpa
HOMEAWAY_METRICS_DOMAIN=metrics.home.arpa
PCCTL_GID=REPLACE_WITH_OUTPUT_OF_id_-g_pcctl
```

Set `PCCTL_GID` to **only the numeric result** of `id -g pcctl`, not the placeholder above.

Bootstrap a *local* administrator account and secret configuration (no Google account required):

```bash
python3 scripts/init-control-auth.py --user homeowner --email your-local-email@example.com
sudo chown -R 65534:65534 data/control/authelia
sudo install -d -m 0755 data/control/audit data/control/caddy data/control/caddy-config
sudo chown -R 65534:65534 data/control/audit
```

The bootstrap downloads/runs the pinned Authelia hashing CLI locally, generates a random initial account password, prints it **once** for saving in a password manager, creates strong local secrets, and never overwrites existing authentication state. The email is used only by Authelia for account identity and local notification messages. Password reset and TOTP-registration messages use the local filesystem notifier at `data/control/authelia/notification.txt`; treat it as sensitive. Configure a more robust notification delivery method later if desired.

Start only the new opt-in services, with both Compose files specified in each command:

```bash
docker compose -f compose.yaml -f compose.control.yaml --profile control --profile metrics config -q
docker compose -f compose.yaml -f compose.control.yaml --profile control up -d --no-deps --build authelia pc-control gateway
```

Caddy listens on private LAN TCP **8443**, not the WAN. The existing :8088 status dashboard is still read-only. Grafana remains password-protected for administration. To also make the previously implemented Grafana embedded view work on HTTPS, the **metrics hostname** proxies to Grafana, and the overlay changes Grafana's canonical root URL (requires a deliberate Grafana container recreation with both Compose files). The original direct HTTP :3000 may then redirect to the secure hostname; verify both paths.

Caddy's local root certificate is stored under `data/control/caddy/pki/authorities/local/root.crt` (or the `caddy/pki` subdirectory depending on volume layout). **Verify its origin on the Pi**, export/install as a trusted CA on each iPhone/PC, and enable full trust in iOS Certificate Trust Settings. This avoids browser warnings and is needed for a reliable authenticated mobile experience. Do not distribute the private CA key.

Open `https://auth.home.arpa:8443` on a device that trusts the CA. Sign in with the generated password and follow Authelia's TOTP enrollment; read the locally generated registration link from `notification.txt`. Then open `https://homeaway.home.arpa:8443/control/`. Subsequent visits reuse an Authelia session until it expires. Sensitive commands are still confirmed in the UI. The browser is **not** asked to authenticate to the Windows machines.

## 5. iOS Shortcut compatibility

Your working Shortcut can remain: connect by SSH to the Pi as `pcctl`, using the existing iPhone key restricted with `restrict,command="/usr/local/bin/pcctl-dispatch"` in `/home/pcctl/.ssh/authorized_keys`. Commands are unchanged:

```text
pcctl gaming status
pcctl gaming wake
pcctl gaming sleep
pcctl gaming restart
pcctl gaming shutdown
pcctl work status
```

The dispatcher rejects every nonallowlisted action and unknown inventory ID. Test a read-only `status` action first. Adding a new PC creates its Shortcut command automatically using its new ID. If you are satisfied with the existing Shortcut setup, no iOS editing is needed.

## 6. Verify before enabling any real power changes

- **DNS/VPN:** Pi-hole and WireGuard still work; only VPN UDP 51820 is forwarded by the router. Test all three local DNS names over your cellular VPN and LAN.
- **HTTPS:** Caddy's CA trusted, no certificate warning, Authelia login and TOTP both required for `/control/`. Access to `https://homeaway.home.arpa:8443/control/` without a session must redirect to authentication. The HTTP :8088 portal must never expose a control endpoint.
- **Access boundary:** The controller has no published port and no SSH keys. Unauthenticated direct HTTP requests to control endpoints must fail. Cross-site POSTs, missing Origin, wrong computer IDs, and arbitrary commands must fail.
- **Inventory:** Both computers display with appropriate labels. SSH reachable is not the same as Windows healthy. A sleeping PC may show offline; Wake-on-LAN return only means a packet was sent.
- **Windows:** Confirm status and one supervised Wake test before using sleep/restart/shutdown; verify rights for the dedicated non-admin account. Try the original iOS Shortcut after migrating wrappers.
- **Resilience:** Stop the control stack and verify Pi-hole, wg-easy, existing status portal, Grafana and all local SensorDash fan controls still operate normally. If the Pi reboots, systemd socket activation resumes but clients may need to reauthenticate.
- **Log:** `data/control/audit/events.jsonl` records attempts and results. It is not a proof that Windows finished an action. Inspect `journalctl -u pcctl-agent.service` for host-side requests.

## 7. Rollback, backups and adding computers later

To disable browser control without touching Pi-hole/WireGuard:

```bash
docker compose -f compose.yaml -f compose.control.yaml --profile control stop gateway pc-control authelia
```

The existing read-only portal :8088 and the iOS Shortcut remain. If you migrated `/usr/local/bin/pcctl` or `pcctl-dispatch`, restore their timestamped backups to revert the legacy command behavior. The installer never changes authorized_keys. If restoring a different Pi user/home directory, adjust service configuration deliberately.

Back up `/etc/homeaway-pcctl/hosts.json`, the private Pi SSH key/known_hosts, Windows SSH config backups, `data/control/authelia` (contains 2FA secrets, DB and passwords), `data/control/caddy` (contains your private local CA), and `data/control/audit`. Protect backups off-device, encrypted. Backing up only dashboards is insufficient to recover authenticated access.

To add **any number of additional computers up to the configured 64**, repeat:
1. Add the host to the inventory with `scripts/add-pc.py`.
2. Install/reuse the Windows restricted `pcctl` OpenSSH account and Pi public key.
3. Verify the SSH host fingerprint and WOL hardware capabilities.
4. Run `pcctl new-id status`; the web dashboard automatically shows the new host. Optionally add `pcctl new-id ACTION` to your existing Shortcut.

### Security tradeoffs and limits

- Network isolation: only Caddy is LAN-published on :8443 and only over your private/VPN LAN; the pc-control API and Authelia have no published ports, with a separate internal Docker network.
- Authelia requires local 2FA for all computer controls. The Caddy forward-auth identity header is trusted only because the API is reachable exclusively through the isolated gateway, not from arbitrary LAN clients.
- No service credentials or private SSH keys are sent to browsers. The control backend uses a group-restricted Unix socket to the host agent; the host agent alone uses SSH to Windows.
- Only known commands are permitted; there is no user-editable shell command, general SSH proxy, Docker socket, home router control, Windows elevated token, or remote-code execution feature.
- Host key verification is deliberately strict. SSH clients must verify genuine Windows host identities and never silently trust changed host keys.
- With a LAN-only self-signed CA, trust distribution is a real operator step. Don't replace it with insecure HTTPS or make the service WAN-public.
- TOTP enrollment and full end-to-end command effectiveness remain **operator tests**. CI tests logic but cannot certify actual Wake-on-LAN, firmware suspend, Windows privileges, iOS certificate trust, or your Pi runtime.

References: [Authelia/Caddy forward auth](https://www.authelia.com/i/caddy/), [Caddy forward_auth](https://caddyserver.com/docs/caddyfile/directives/forward_auth), [Authelia sessions](https://www.authelia.com/configuration/session/introduction/), [Microsoft OpenSSH](https://learn.microsoft.com/windows-server/administration/openssh/openssh_install_firstuse).
