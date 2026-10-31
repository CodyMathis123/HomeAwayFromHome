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
