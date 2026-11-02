'use strict';
// Exercise the shipped architecture script without external browser packages.
// Never reaches a real Pi, backend or authenticated control endpoint.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../../portal/architecture.js'), 'utf8');

function simulate(statusResponse) {
  const calls = [];
  const elements = new Map();
  const root = {hidden: false,
    querySelector(selector) {
      if (selector === '.architecture-panel') return {};
      if (!elements.has(selector)) {
        elements.set(selector, {
          textContent: '', dataset: {}, hidden: true, disabled: false, href: '',
          events: {},
          addEventListener(name, cb) { this.events[name] = cb; }
        });
      }
      return elements.get(selector);
    }
  };
  const document = {querySelector(selector) {
    assert.equal(selector, '#page-view-slot > main.shell:not([hidden])');
    return root.hidden ? null : root;
  }};
  const fetch = async (url, options) => {
    calls.push({url,options});
    if (url === '/api/control-config') {
      return {ok: true, json: async () => ({enabled: false, url: null})};
    }
    assert.equal(url, '/api/status');
    return statusResponse();
  };
  const window = {};
  vm.runInNewContext(script, {
    window, document, fetch, location: {hostname:'home.homeawayfromhome.house'},
    URL, Map, Date, Number
  }, {filename: 'portal/architecture.js'});
  const settle = async () => {
    for (let i=0; i<3; i++) await new Promise(setImmediate);
  };
  const el = id => root.querySelector('#' + id);
  return {calls,window,root,el,settle};
}

const success = () => ({
  ok:true,
  json:async () => ({
    checkedAt:'2026-11-01T16:30:00+00:00',
    checks:[
      {id:'dns',ok:true,detail:'DNS lookup succeeded'},
      {id:'wireguard',ok:true,detail:'Admin listener reachable; tunnel not verified'},
      {id:'monitor',ok:false,detail:'Optional Grafana metrics profile may be disabled'}
    ]
  })
});

(async () => {
  const working = simulate(success);
  await working.settle();
  assert.equal(working.el('architecture-dns').href, '/dns/admin/');
  assert.equal(working.el('architecture-grafana').href, 'https://metrics.homeawayfromhome.house/');
  assert.equal(working.el('arch-check-dns').textContent, 'Responding');
  assert.equal(working.el('arch-check-dns').dataset.state, 'responding');
  assert.equal(working.el('arch-check-wireguard').textContent, 'Responding');
  assert.match(working.el('arch-detail-wireguard').textContent, /tunnel not verified/);
  assert.equal(working.el('arch-check-monitor').textContent, 'Not detected');
  assert.equal(working.el('arch-check-monitor').dataset.state, 'optional');
  assert.equal(working.el('arch-probes-refresh').disabled, false);
  assert.equal(working.calls.filter(call=>call.url==='/api/status').length, 1);
  assert.ok(working.calls.every(call=>call.options.cache==='no-store'));
  assert.ok(working.calls.every(call=>!call.options.method || call.options.method==='GET'));

  // Returning to a cached view should query status again without resetting
  // read-only links or re-registering the button.
  working.window.HomeAwayArchitecture.show();
  await working.settle();
  assert.equal(working.calls.filter(call=>call.url==='/api/status').length, 2);
  working.el('arch-probes-refresh').events.click();
  await working.settle();
  assert.equal(working.calls.filter(call=>call.url==='/api/status').length, 3);
  working.root.hidden = true;
  working.window.HomeAwayArchitecture.show();
  await working.settle();
  assert.equal(working.calls.filter(call=>call.url==='/api/status').length, 3);

  // A failed *portal status request* cannot be described as VPN/DNS failure.
  const unavailable = simulate(() => ({ok:false,status:503}));
  await unavailable.settle();
  for (const id of ['dns','wireguard','monitor']) {
    assert.equal(unavailable.el('arch-check-' + id).textContent, 'Unknown');
    assert.equal(unavailable.el('arch-check-' + id).dataset.state, 'unknown');
    assert.match(unavailable.el('arch-detail-' + id).textContent, /not verified/);
  }
  assert.match(unavailable.el('arch-probes-updated').textContent, /not evidence of an outage/);
  assert.equal(unavailable.el('arch-probes-refresh').disabled, false);

  // Missing check data is unknown, not invented or inferred from other probes.
  const partial = simulate(() => ({
    ok:true,json:async () => ({
      checkedAt:'2026-11-01T16:31:00+00:00',
      checks:[{id:'dns',ok:true,detail:'DNS lookup succeeded'}]
    })
  }));
  await partial.settle();
  assert.equal(partial.el('arch-check-dns').textContent, 'Responding');
  assert.equal(partial.el('arch-check-wireguard').textContent, 'Unknown');
  assert.equal(partial.el('arch-check-monitor').textContent, 'Unknown');

  console.log('Architecture checks: scoped success, optional, unavailable, missing and revisit cases passed');
})().catch(error => { console.error(error); process.exitCode=1; });
