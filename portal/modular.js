'use strict';
const byId = id => document.getElementById(id);
const details = {
  'cooling-monitoring': 'SensorDash history and fan/cooling telemetry; control stays local.',
  'linux-host-monitoring': 'CPU, memory, load and storage I/O from this Linux host.',
  'monitoring-diagnostics': 'Read-only monitoring and scrape evidence.',
  'home-portal': 'This component-aware status interface.'
};
function card(title, description, state) {
  const item = document.createElement('article');
  item.className = 'card';
  const label = document.createElement('h3');
  label.textContent = title;
  const info = document.createElement('p');
  info.textContent = description;
  item.append(label, info);
  if (state) {
    const status = document.createElement('div');
    status.className = 'state';
    status.textContent = state;
    item.append(status);
  }
  return item;
}
async function refresh() {
  const button = byId('refresh');
  button.disabled = true;
  try {
    const response = await fetch('api/features', {cache:'no-store'});
    if (!response.ok) throw new Error('Feature list unavailable');
    const config = await response.json();
    const items = config.selectedFeatures || [];
    byId('features').replaceChildren(...items.map(x =>
      card(x.label, details[x.id] || 'Installed monitoring feature', 'Installed')
    ));
    const data = await fetch('api/diagnostics', {cache:'no-store'});
    if (!data.ok) throw new Error('Diagnostics unavailable');
    const summary = await data.json();
    const checks = [...(summary.checks || []), ...(summary.serviceChecks || [])];
    const nodes = checks.map(x => card(x.label, x.detail || 'No recent observations',
      x.value === null ? 'Unknown' : String(x.value) + (x.unit === 'percent' ? '%' : '')));
    if (items.some(x => x.id === 'cooling-monitoring')) {
      const telemetry = summary.telemetry || {};
      nodes.push(card('SensorDash hosts', (telemetry.hosts || []).length +
        ' hosts with observations over the last 24 hours', telemetry.state || 'unknown'));
    }
    byId('diagnostics').replaceChildren(...nodes);
    byId('updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch (_error) {
    byId('updated').textContent = 'Status unavailable; retry';
    byId('diagnostics').replaceChildren(card('Diagnostics unavailable',
      'Unable to retrieve current monitoring evidence. Services may be restarting.', 'Unknown'));
  } finally {
    button.disabled = false;
  }
}
byId('refresh').addEventListener('click', refresh);
void refresh();
