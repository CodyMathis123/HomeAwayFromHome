'use strict';
// Run the real authenticated control-page script in a zero-dependency fake DOM.
// Test section navigation and capability changes without Docker, real accounts
// or privileged power calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const script = fs.readFileSync(
  path.join(__dirname, '../../control/api/app.js'), 'utf8');

function simulate(initialHash, firstCapabilities) {
  const elements = new Map();
  const handlers = {};
  const links = [];
  const calls = [];
  const location = {hash: initialHash, pathname: '/control/'};
  const history = {
    state: null,
    replaceState(_state, _title, value) {
      calls.push('replace:' + value);
      location.hash = value.includes('#') ? value.slice(value.indexOf('#')) : '';
    }
  };
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    const e = {
      id, hidden: id === 'accounts' || id === 'computer-admin' || id === 'setup-readiness',
      disabled: false, dataset: {}, textContent: '',
      attrs: {}, handlers: {},
      addEventListener(name, callback) { this.handlers[name] = callback; },
      getAttribute(name) { return name === 'href' ? this.href : this.attrs[name]; },
      setAttribute(name, val) { this.attrs[name] = val; },
      removeAttribute(name) { delete this.attrs[name]; },
      focus(args) { calls.push('focus:' + id); assert.equal(args.preventScroll, true); },
      scrollIntoView(args) { calls.push('scroll:' + id); assert.equal(args.block, 'start'); },
      replaceChildren(...children) { this.children = children; },
      append(...children) { this.children = [...(this.children || []), ...children]; }
    };
    elements.set(id, e);
    return e;
  }
  const main = element('main');
  for (const id of ['hosts','computer-admin','accounts','recent-activity','setup-readiness']) {
    const nav = element('nav-' + id);
    nav.href = '#' + id;
    links.push(nav);
  }
  // Real IDs used by both fallback and shared navigation.
  const navComputers = element('nav-computers');
  navComputers.href = '#computer-admin';
  const navAccounts = element('nav-accounts');
  navAccounts.href = '#accounts';
  const navPower = element('nav-power');
  navPower.href = '#hosts';
  links.splice(0, links.length, navPower, navComputers, navAccounts, element('nav-activity'), element('nav-readiness'));
  links[3].href = '#recent-activity';
  links[4].href = '#setup-readiness';
  const document = {
    hidden: false,
    body: {classList: {add(value) { calls.push('body:' + value); }}},
    getElementById: element,
    querySelector(selector) {
      if (selector === 'main') return main;
      throw Error('Unexpected selector ' + selector);
    },
    querySelectorAll(selector) {
      assert.equal(selector, '.section-nav a[href^="#"]');
      return links;
    },
    createElement(tag) {
      const e = element('created-' + (elements.size + 1));
      e.tag = tag;
      return e;
    },
    addEventListener(type, callback) { handlers[type] = callback; }
  };
  let capabilities = firstCapabilities;
  const fetch = async url => {
    if (url === '/control/api/me')
      return capabilities
        ? {ok: true, json: async () => ({user: 'operator', capabilities})}
        : {ok: false, status: 401};
    if (url === '/control/api/hosts')
      return {ok: true, json: async () => ({hosts: []})};
    throw Error('Unexpected API call: ' + url);
  };
  const window = {
    HomeAwayNavigation: {sync() { calls.push('nav-sync'); }},
    addEventListener(type, handler) { handlers[type] = handler; }
  };
  const HomeAwayAccounts = {
    start(identity) {
      element('accounts').hidden = !identity.capabilities.manageUsers;
    }
  };
  const HomeAwayComputers = {
    start(identity) {
      element('computer-admin').hidden = !identity.capabilities.manageComputers;
    }
  };
  const HomeAwayReadiness = {
    start(identity) {
      element('setup-readiness').hidden = identity?.capabilities?.role !== 'admins';
    },
    show() { calls.push('readiness:show'); }
  };
  vm.runInNewContext(script, {
    document, window, location, history, fetch,
    HomeAwayAccounts, HomeAwayComputers, HomeAwayReadiness, Set, Date
  }, {filename: 'control/api/app.js'});

  const settle = async () => { for (let i = 0; i < 3; i++) await new Promise(setImmediate); };
  const navigate = hash => {
    location.hash = hash;
    handlers.hashchange();
  };
  const refresh = async () => {
    element('refresh').handlers.click();
    await settle();
  };
  return {element, main, location, calls, links, settle, navigate, refresh,
          setCapabilities(value) { capabilities = value; }};
}

const admins = {role: 'admins', actions: [], manageUsers: true, manageComputers: true, history: true};
const viewers = {role: 'viewers', actions: [], manageUsers: false, manageComputers: false, history: false};

(async () => {
  // An admin's deep link must survive the asynchronous identity check.
  const admin = simulate('#accounts', admins);
  assert.equal(admin.location.hash, '#accounts');
  await admin.settle();
  assert.equal(admin.main.dataset.activeView, 'accounts');
  assert.equal(admin.element('accounts').hidden, false);
  assert.equal(admin.element('nav-activity').hidden, false);
  assert.equal(admin.element('nav-accounts').attrs['aria-current'], 'location');
  admin.navigate('#computer-admin');
  assert.equal(admin.main.dataset.activeView, 'computer-admin');
  assert.ok(admin.calls.includes('focus:computer-admin'));
  admin.navigate('#recent-activity');
  assert.equal(admin.main.dataset.activeView, 'recent-activity');
  admin.navigate('#setup-readiness');
  assert.equal(admin.main.dataset.activeView, 'setup-readiness');
  assert.equal(admin.element('setup-readiness').hidden, false);
  assert.ok(admin.calls.includes('readiness:show'));
  admin.navigate('#hosts');
  assert.equal(admin.main.dataset.activeView, 'hosts');

  // Revocation while the Users view is open must revoke navigation and its
  // mounted panel, regardless of a stale hash or previous role.
  admin.navigate('#accounts');
  admin.setCapabilities(viewers);
  await admin.refresh();
  assert.equal(admin.main.dataset.activeView, 'hosts');
  assert.equal(admin.location.hash, '#hosts');
  assert.equal(admin.element('accounts').hidden, true);
  assert.equal(admin.element('computer-admin').hidden, true);
  assert.equal(admin.element('setup-readiness').hidden, true);
  assert.equal(admin.element('nav-readiness').hidden, true);
  assert.equal(admin.element('nav-accounts').hidden, true);
  assert.equal(admin.element('nav-computers').hidden, true);

  // A viewer opening an admin deep link must fail closed without triggering
  // any admin request. The shared menu is only a visual convenience.
  const viewer = simulate('#computer-admin', viewers);
  await viewer.settle();
  assert.equal(viewer.main.dataset.activeView, 'hosts');
  assert.equal(viewer.location.hash, '#hosts');
  assert.equal(viewer.element('computer-admin').hidden, true);
  viewer.navigate('#accounts');
  assert.equal(viewer.main.dataset.activeView, 'hosts');
  assert.equal(viewer.location.hash, '#hosts');
  assert.equal(viewer.element('accounts').hidden, true);
  assert.equal(viewer.element('nav-power').attrs['aria-current'], 'location');
  viewer.navigate('#recent-activity');
  assert.equal(viewer.main.dataset.activeView, 'hosts');
  assert.equal(viewer.location.hash, '#hosts');
  assert.equal(viewer.element('nav-activity').hidden, true);
  viewer.navigate('#setup-readiness');
  assert.equal(viewer.main.dataset.activeView, 'hosts');
  assert.equal(viewer.location.hash, '#hosts');
  assert.equal(viewer.element('nav-readiness').hidden, true);

  // A revoked session while on an admin panel must clear the stale fragment,
  // hide the old admin views and remove cached power-action cards.
  const expired = simulate('#accounts', admins);
  await expired.settle();
  expired.setCapabilities(null);
  await expired.refresh();
  assert.equal(expired.location.hash, '#hosts');
  assert.equal(expired.main.dataset.activeView, 'hosts');
  assert.equal(expired.element('accounts').hidden, true);
  assert.equal(expired.element('computer-admin').hidden, true);
  assert.equal(expired.element('nav-accounts').hidden, true);
  assert.ok(expired.element('hosts').children.length > 0);
  assert.equal(expired.element('identity').textContent, 'Authorization unavailable');

  console.log('Control views: deep links, history fragments, capability revocation and denial passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
