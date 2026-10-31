'use strict';
const byId = id => document.getElementById(id);
const host = window.location.hostname;
byId('link-dns').href = `http://${host}:8080/admin/`;
byId('link-monitor').href = `http://${host}:3000/`;
let pending = false;
async function refresh() {
  if (pending) return;
  pending = true;
  byId('refresh').disabled = true;
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    if (!response.ok) throw new Error('Status API unavailable');
    const data = await response.json();
    for (const check of data.checks) {
      const status = byId('state-' + check.id);
      const detail = byId('detail-' + check.id);
      if (!status || !detail) continue;
      status.textContent = check.ok ? 'Online' : check.id === 'monitor' ? 'Not running' : 'Unavailable';
      status.dataset.status = check.ok ? 'up' : check.id === 'monitor' ? 'optional' : 'down';
      detail.textContent = check.detail;
    }
    const core = data.checks.filter(check => check.id !== 'monitor');
    byId('core-count').textContent = core.filter(check => check.ok).length;
    byId('dns-state').textContent = data.checks.find(check => check.id === 'dns')?.ok ? 'Responding' : 'Unavailable';
    byId('vpn-state').textContent = data.checks.find(check => check.id === 'wireguard')?.ok ? 'Reachable' : 'Unavailable';
    byId('updated').textContent = 'Last checked ' + new Date(data.checkedAt).toLocaleTimeString();
  } catch (error) {
    byId('updated').textContent = 'Status refresh failed · ' + error.message;
    for (const id of ['dns', 'wireguard', 'monitor']) {
      byId('state-' + id).textContent = 'Unknown';
      byId('state-' + id).dataset.status = 'down';
      byId('detail-' + id).textContent = 'Cannot reach status API';
    }
    byId('core-count').textContent = '—';
    byId('dns-state').textContent = 'Unknown';
    byId('vpn-state').textContent = 'Unknown';
  } finally {
    byId('refresh').disabled = false;
    pending = false;
  }
}
byId('refresh').addEventListener('click', refresh);
document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
setInterval(() => { if (!document.hidden) void refresh(); }, 30000);
void refresh();

async function setupEmbeddedMetrics() {
  const details = byId('metrics-section');
  const holder = byId('metrics-frame');
  const description = byId('metrics-description');
  const link = byId('metrics-open');
  try {
    const response = await fetch('/api/embed-config', {cache: 'no-store'});
    if (!response.ok) throw new Error('Embed configuration unavailable');
    const config = await response.json();
    if (!config.enabled || !config.url) {
      description.textContent = 'No embedded view configured yet. Share the HomeAway Portal Metrics dashboard from Grafana, then configure its link on the Pi.';
      return;
    }
    const url = new URL(config.url);
    // Defense in depth: never load an arbitrary origin or admin path.
    // HomeAway may be opened by a local DNS name while Grafana uses the Pi's LAN IP.
    // The server has already validated and constructed that private LAN origin.
    if (url.protocol !== 'http:' || url.port !== '3000' ||
        !url.pathname.startsWith('/public-dashboards/') ||
        !/^[a-fA-F0-9]{32,128}$/.test(url.pathname.slice('/public-dashboards/'.length))) {
      throw new Error('Embedded URL must use this Pi host and a shared-dashboard link');
    }
    link.href = url.href;
    link.hidden = false;
    description.textContent = 'Live, read-only metrics. The embedded view refreshes every 30 seconds while visible.';
    let nearViewport = false;
    function updateEmbed() {
      if (!details.open || document.hidden || !nearViewport) {
        holder.replaceChildren();
        return;
      }
      if (holder.firstChild) return;
      const frame = document.createElement('iframe');
      frame.title = 'HomeAway read-only Grafana metrics';
      frame.className = 'metrics-iframe';
      frame.loading = 'lazy';
      frame.referrerPolicy = 'no-referrer';
      frame.src = url.href;
      holder.appendChild(frame);
    }
    details.addEventListener('toggle', updateEmbed);
    document.addEventListener('visibilitychange', updateEmbed);
    if ('IntersectionObserver' in window) {
      const observer = new IntersectionObserver(entries => {
        nearViewport = entries.some(entry => entry.isIntersecting);
        updateEmbed();
      }, {rootMargin: '200px'});
      observer.observe(details);
    } else {
      nearViewport = true;
    }
    updateEmbed();
  } catch (error) {
    description.textContent = 'Embedded metrics unavailable: ' + error.message;
  }
}
void setupEmbeddedMetrics();
