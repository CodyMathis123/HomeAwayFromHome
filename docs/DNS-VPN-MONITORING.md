# HomeAway DNS and VPN telemetry

## Design

The existing VictoriaMetrics container now scrapes two internal targets every 30 seconds. There is NO new Prometheus server, new host-facing listening port, router forwarding rule, or internet-facing control endpoint.

- wg-easy 15.5.0: native read-only Prometheus endpoint at /metrics/prometheus, accessed on wg-easy:51821 from Docker's private bridge. Uses a bearer credential. The existing host-side wg-easy admin binding remains 127.0.0.1:51821.
- Pi-hole v6: a minimal separate internal Python exporter at pihole-exporter:9187. The exporter obtains expiring Pi-hole API sessions via POST /api/auth using an application password and GETs only /api/stats/summary and /api/dns/blocking.
- VictoriaMetrics: native Prometheus scraper (-promscrape.config) uses monitoring/scrape.yml. The time-series database is the same one used for SensorDash. This scrape config deliberately drops the wg-easy peer endpoint metric, which includes a remote client's public IP:port.
- Grafana: read-only HomeAway · DNS & VPN dashboard automatically provisioned from grafana/dashboards/dns-vpn.json.

The Pi-hole app password can authenticate to the Pi-hole API and is NOT read-only scoped. Its exposure is limited by the isolated Docker container, bind-mount-only secrets, no published exporter port, and read-only Docker filesystem. Do not share it in chat, logs or GitHub.

No individual DNS queries, DNS domains, client IPs, client names, WireGuard private keys, client configuration files, or external peer endpoint IP addresses are collected. The VPN dashboard may display names of your VPN peers. These are private LAN/VPN data; do not publish Grafana to the WAN.

## Activate on the already-working Pi

1. In Pi-hole v6's web admin, go to Settings > Web Interface/API (select Expert mode if needed) and generate/configure an application password. Copy the value privately. Be aware that rotating an existing app password can invalidate any other integrations that use it.
2. From the Pi's existing feature-branch checkout, ensure git status shows no unexpected tracked edits, then pull:

       cd ~/HomeAwayFromHome
       git status --short
       git pull --ff-only origin feature/pi4-home-infrastructure

3. Initialize credentials with the hidden prompt:

       sudo bash scripts/init-service-monitoring.sh

   Enter the Pi-hole app password. The script also generates an independent random wg-easy Prometheus bearer. It will not overwrite either existing nonempty credential. The existing secrets directory remains owner-only (mode 0700, without changing its original owner). These new files have container-readable permissions because Docker Compose mounts local files read-only without remapping them; other host accounts cannot traverse the parent directory. The deployment user can read the secrets, as with the existing Pi-hole and Grafana credentials.

4. Privately display the wg-easy bearer on the Pi:

       sudo cat secrets/wg_metrics_bearer

   From Windows, continue using the already-working SSH tunnel:

       ssh -N -L 51821:127.0.0.1:51821 codymathis@192.168.1.43

   Open http://127.0.0.1:51821 then Admin Panel > General. Enable Prometheus metrics and set the Bearer Password to exactly that generated value. Save. Do NOT forward TCP 51821, 9187, VictoriaMetrics 8428, or Grafana 3000.

5. Start the bridge-only Pi-hole exporter and apply the new scraper to the existing VictoriaMetrics service:

       sudo docker compose --profile metrics up -d --build pihole-exporter victoria-metrics
       sudo docker compose --profile metrics ps

   VictoriaMetrics will be recreated because its command/mount changes. Its historical data stays at data/victoria-metrics and is NOT erased. Existing Grafana panels may have a brief gap, but SensorDash fan control still runs locally and must never rely on the Pi.

6. Verify:

       sudo bash scripts/verify-service-monitoring.sh

   Both jobs should have up=1; Pi-hole and WireGuard should have actual metric series. Open the HomeAway · DNS & VPN dashboard under HomeAway at http://192.168.1.43:3000. File-provider updates typically appear after about 30 seconds. An empty panel means the metric has no current series or no newer data yet; do not infer a successful DNS/VPN test from an up metric alone.

## Interpreting the graphs

Pi-hole queries, blocked count, cached count and forwarded count are statistics over Pi-hole's current summary window. They are GAUGES, not lifetime counters. Values may decrease as the window advances. Do not use rate() or increase() on those metrics. The Pi-hole blocking metric is read-only: 1 = enabled, 0 = disabled.

wg-easy reports configured/enabled/connected peer counts, per-peer sent/received bytes, and age in seconds since a handshake. Clients without a handshake are represented by a zero-age wg-easy metric; the dashboard excludes them rather than misleadingly displaying a fresh handshake. WireGuard byte counters may reset after a server restart. Five-minute rate charts require more than one scrape.

## If a job remains down

- wg-easy: ensure Admin Panel > General Prometheus is enabled and the Bearer Password matches the file. Do not paste the bearer or reveal full endpoint IPs. The host admin UI remains loopback-only.
- Pi-hole: check whether the application password is current and whether the exporter can reach Pi-hole over the internal bridge. The exporter deliberately suppresses API/session error details to avoid accidental credential disclosure.
- View only non-secret logs: sudo docker compose --profile metrics logs --tail=60 pihole-exporter victoria-metrics.
- The command scripts/verify-service-monitoring.sh checks current series via the internal VictoriaMetrics API without exporting credentials.

Future potential enhancements, not collected by default for privacy: per-client DNS activity, top blocked/allowed domains, per-upstream resolver performance, query histograms and Pi host thermal/disk metrics.

Upstream docs:
- https://wg-easy.github.io/wg-easy/v15.4/advanced/metrics/prometheus/
- https://docs.pi-hole.net/api/auth/
- https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/
