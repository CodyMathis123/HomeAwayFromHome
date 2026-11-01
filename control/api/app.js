'use strict';
const $ = id => document.getElementById(id);
const hosts = $('hosts');
const status = $('updated');
let pending = new Set();
let identity = null;
function updateCurrentSection() {
  const local = new Set(['#hosts', '#computer-admin', '#accounts', '#recent-activity']);
  const target = local.has(location.hash) ? location.hash : '#hosts';
  for (const link of document.querySelectorAll('.section-nav a[href^="#"]')) {
    if (!link.hidden && link.getAttribute('href') === target)
      link.setAttribute('aria-current', 'location');
    else
      link.removeAttribute('aria-current');
  }
}
function updateSectionNavigation(capabilities) {
  // Convenience only: every control API still enforces roles server-side.
  $('nav-computers').hidden = !capabilities?.manageComputers;
  $('nav-accounts').hidden = !capabilities?.manageUsers;
  updateCurrentSection();
}
async function loadIdentity() {
  try {
    const response = await fetch('/control/api/me', {cache:'no-store', credentials:'same-origin'});
    if (!response.ok) throw new Error('Account authorization unavailable');
    const nextIdentity = await response.json();
    if (!nextIdentity?.capabilities?.role) throw new Error('Account capabilities unavailable');
    identity = nextIdentity;
    updateSectionNavigation(identity.capabilities);
    $('identity').textContent = identity.user + ' · ' + identity.capabilities.role;
    if (typeof HomeAwayAccounts !== 'undefined') HomeAwayAccounts.start(identity);
    if (typeof HomeAwayComputers !== 'undefined') HomeAwayComputers.start(identity);
    return identity;
  } catch (error) {
    // A lost connection or revoked session must not leave stale admin UI visible.
    identity = null;
    updateSectionNavigation(null);
    $('computer-admin').hidden = true;
    $('accounts').hidden = true;
    $('identity').textContent = 'Authorization unavailable';
    throw error;
  }
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
    if (!Array.isArray(data.hosts)) throw new Error('Unexpected computer status response');
    hosts.replaceChildren(...(data.hosts.length
      ? data.hosts.map(card) : [node('p', 'detail', 'No computers are currently registered.')]));
    delete hosts.dataset.stale;
    status.textContent = 'Updated ' + new Date().toLocaleTimeString();
  } catch (error) {
    hosts.dataset.stale = 'true';
    status.textContent = 'Status unavailable — ' + error.message + '. Previously shown results may be stale.';
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
window.addEventListener('hashchange', updateCurrentSection);
updateCurrentSection();
$('refresh').addEventListener('click', refresh);
$('refresh-history').addEventListener('click', loadHistory);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { void refresh(); void loadHistory(); } });
void refresh(); void loadHistory();
