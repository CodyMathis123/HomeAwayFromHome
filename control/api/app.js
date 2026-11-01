'use strict';
const $ = id => document.getElementById(id);
const hosts = $('hosts');
const status = $('updated');
let pending = new Set();
let identity = null;
async function loadIdentity() {
  const response = await fetch('/control/api/me', {cache:'no-store', credentials:'same-origin'});
  if (!response.ok) throw new Error('Account authorization unavailable');
  identity = await response.json();
  $('identity').textContent = identity.user + ' · ' + identity.capabilities.role;
  return identity;
}
const actions = [
  ['wake', 'Wake'],
  ['sleep', 'Sleep'],
  ['restart', 'Restart'],
  ['shutdown', 'Shut down']
];
const node = (tag, cls, text) => {
  const element = document.createElement(tag);
  if (cls) element.className = cls;
  if (text !== undefined) element.textContent = text;
  return element;
};
function card(item) {
  const article = node('article', 'host-card');
  const top = node('div', 'top');
  const heading = node('h2', '', item.name);
  const state = node('span', 'state ' + (item.state === 'online' ? 'online' : 'offline'),
                     item.state === 'online' ? 'Reachable' : item.state === 'offline' ? 'Not reachable' : 'Unknown');
  top.append(heading, state);
  article.append(top, node('p', 'detail', item.detail));
  const row = node('div', 'buttons');
  actions.filter(([action]) => identity?.capabilities?.actions?.includes(action))
    .forEach(([action, label]) => {
    const btn = node('button', action === 'shutdown' ? 'danger' : '', label);
    btn.type = 'button';
    if (pending.has(item.id)) btn.disabled = true;
    btn.addEventListener('click', () => doAction(item, action));
    row.append(btn);
  });
  article.append(row);
  return article;
}
async function refresh() {
  const button = $('refresh');
  button.disabled = true;
  status.textContent = 'Checking SSH reachability…';
  try {
    await loadIdentity();
    const response = await fetch('/control/api/hosts', {cache: 'no-store', credentials: 'same-origin'});
    if (!response.ok) throw new Error('Could not read computer status');
    const data = await response.json();
    hosts.replaceChildren(...data.hosts.map(card));
    status.textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch (error) {
    status.textContent = error.message;
  } finally { button.disabled = false; }
}
async function doAction(item, action) {
  if (pending.has(item.id) || !identity?.capabilities?.actions?.includes(action)) return;
  const destructive = action === 'restart' || action === 'shutdown';
  if (!window.confirm('Request ' + action + ' for ' + item.name + '? This may interrupt unsaved work.')) return;
  const confirm = destructive ? window.prompt('Enter computer ID "' + item.id + '" to confirm:') : '';
  if (destructive && confirm !== item.id) return;
  pending.add(item.id);
  $('last-action').textContent = 'Sending ' + action + ' request for ' + item.name + '…';
  try {
    const response = await fetch('/control/api/actions', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-HomeAway-Request': '1'},
      body: JSON.stringify({host: item.id, action, confirm})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || result.detail || 'Request failed');
    $('last-action').textContent = item.name + ': ' + result.detail;
  } catch (error) {
    $('last-action').textContent = item.name + ': ' + error.message;
  } finally {
    pending.delete(item.id);
    await refresh();
    await loadHistory();
  }
}
async function loadHistory() {
  try {
    if (!identity) await loadIdentity();
    if (!identity.capabilities.history) {
      $('events').textContent = 'Activity is available to administrators.';
      return;
    }
    const response = await fetch('/control/api/history', {cache:'no-store'});
    if (!response.ok) throw new Error('History unavailable');
    const data = await response.json();
    const rows = data.events.map(e => node('div', 'event',
      new Date(e.time).toLocaleString() + ' · ' + e.actor + ' · ' + e.host + ' · ' + e.action + ' · ' +
      (e.ok ? 'Requested' : 'Failed / unknown')));
    $('events').replaceChildren(...(rows.length ? rows : [node('p', '', 'No commands yet.')]));
  } catch (error) { $('events').textContent = error.message; }
}
$('refresh').addEventListener('click', refresh);
$('refresh-history').addEventListener('click', loadHistory);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { void refresh(); void loadHistory(); } });
void refresh(); void loadHistory();
