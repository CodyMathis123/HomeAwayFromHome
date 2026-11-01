'use strict';
const $ = id => document.getElementById(id);
const hosts = $('hosts');
const status = $('updated');
let pending = new Set();
let identity = null;
let authChecked = false;
// These are view-state decisions, never an authorization mechanism.
// The /control/ document and all /control/api/* endpoints remain authenticated
// and server-side capabilities determine which admin sections exist.
const VIEWS = new Set(['hosts', 'computer-admin', 'accounts', 'recent-activity', 'setup-readiness']);
const main = document.querySelector('main');
let currentView = 'hosts';
function updateCurrentSection(focus = false) {
  const requested = location.hash.slice(1);
  let target = VIEWS.has(requested) ? requested : 'hosts';
  const capabilities = identity?.capabilities;
  if (target === 'computer-admin' && !capabilities?.manageComputers) target = 'hosts';
  if (target === 'accounts' && !capabilities?.manageUsers) target = 'hosts';
  if (target === 'recent-activity' && !capabilities?.history) target = 'hosts';
  if (target === 'setup-readiness' && capabilities?.role !== 'admins') target = 'hosts';
  currentView = target;
  main.dataset.activeView = target;
  document.body.classList.add('control-views-ready');
  for (const link of document.querySelectorAll('.section-nav a[href^="#"]')) {
    if (!link.hidden && link.getAttribute('href') === '#' + target)
      link.setAttribute('aria-current', 'location');
    else
      link.removeAttribute('aria-current');
  }
  // Never leave the URL pointing to a privileged section after a role check
  // refuses it. Before identity resolves, retain the requested deep link.
  if (authChecked && requested && requested !== target)
    history.replaceState(history.state, '', '/control/#hosts');
  window.HomeAwayNavigation?.sync();
  if (target === 'setup-readiness' && capabilities?.role === 'admins')
    if (typeof HomeAwayReadiness !== 'undefined') HomeAwayReadiness.show();
  if (focus) {
    const section = $(target);
    if (section) {
      section.focus({preventScroll: true});
      section.scrollIntoView({block: 'start', behavior: 'auto'});
    }
  }
}
function updateSectionNavigation(capabilities) {
  // Convenience only: every control API still enforces roles server-side.
  $('nav-computers').hidden = !capabilities?.manageComputers;
  $('nav-accounts').hidden = !capabilities?.manageUsers;
  $('nav-activity').hidden = !capabilities?.history;
  $('nav-readiness').hidden = capabilities?.role !== 'admins';
  updateCurrentSection();
}
async function loadIdentity() {
  try {
    const response = await fetch('/control/api/me', {cache:'no-store', credentials:'same-origin'});
    if (!response.ok) throw new Error('Account authorization unavailable');
    const nextIdentity = await response.json();
    if (!nextIdentity?.capabilities?.role) throw new Error('Account capabilities unavailable');
    identity = nextIdentity;
    authChecked = true;
    updateSectionNavigation(identity.capabilities);
    $('identity').textContent = identity.user + ' · ' + identity.capabilities.role;
    if (typeof HomeAwayAccounts !== 'undefined') HomeAwayAccounts.start(identity);
    if (typeof HomeAwayComputers !== 'undefined') HomeAwayComputers.start(identity);
    if (typeof HomeAwayReadiness !== 'undefined') HomeAwayReadiness.start(identity);
    return identity;
  } catch (error) {
    // A lost connection or revoked session must not leave stale admin UI visible.
    identity = null;
    authChecked = true;
    updateSectionNavigation(null);
    $('computer-admin').hidden = true;
    $('accounts').hidden = true;
    $('setup-readiness').hidden = true;
    if (typeof HomeAwayReadiness !== 'undefined') HomeAwayReadiness.start(null);
    // Never leave previously rendered power action buttons on screen when
    // authorization cannot be refreshed (the API still enforces all actions).
    hosts.replaceChildren(node('p', 'detail',
      'Computer controls are unavailable until authorization is restored.'));
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
window.addEventListener('hashchange', () => updateCurrentSection(true));
updateCurrentSection();
$('refresh').addEventListener('click', refresh);
$('refresh-history').addEventListener('click', loadHistory);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { void refresh(); void loadHistory(); } });
void refresh(); void loadHistory();
