'use strict';
// Execute the real Setup & Diagnostics browser module with fake read-only APIs.
// No Pi, Docker, accounts, secrets or installed browser dependencies required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../../control/api/readiness.js'), 'utf8');

function simulate({role = 'admins', servicesFail = false, hostsFail = false,
  malformedServices = false, malformedHosts = false, protocol = 'https:'} = {}) {
  let currentRole = role;
  const calls = [];
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id, hidden: id === 'setup-readiness' || id === 'nav-readiness',
        disabled: false, textContent: '', dataset: {}, handlers: {},
        addEventListener(name, callback) { this.handlers[name] = callback; }
      });
    }
    return elements.get(id);
  }
  const document = {getElementById: element};
  const location = {protocol, hash:'#setup-readiness'};
  const fetch = async (url, opts) => {
    calls.push({url, opts});
    assert.equal(opts.cache, 'no-store');
    assert.equal(opts.credentials, 'same-origin');
    assert.ok(!opts.method || opts.method === 'GET', 'No admin mutating requests');
    if (url === '/control/api/me')
      return currentRole ? {ok:true,json:async()=>({capabilities:{
        role:currentRole,manageUsers:currentRole==='admins',manageComputers:currentRole==='admins'
      }})} : {ok:false,status:401};
    if (url === '/api/status')
      return servicesFail ? {ok:false,status:503} :
        {ok:true,json:async()=>({checks:[
          ...(malformedServices ? [null, false, {id:'dns',ok:'yes',detail:null}] : []),
          {id:'dns',ok:true,detail:'DNS lookup succeeded'},
          {id:'wireguard',ok:true,detail:'Admin listener reachable; tunnel not verified'},
          {id:'monitor',ok:false,detail:'Optional Grafana metrics profile may be disabled'}
        ]})};
    if (url === '/control/api/hosts')
      return hostsFail ? {ok:false,status:503} :
        {ok:true,json:async()=>({hosts:[
          ...(malformedHosts ? [null, false] : []),
          {state:'online',name:'Do not disclose hostname',id:'one'},
          {state:'offline',name:'Do not disclose other host',id:'two'}
        ]})};
    throw Error('Unexpected API request ' + url);
  };
  const window = {};
  vm.runInNewContext(script + '\nwindow._readinessTest = HomeAwayReadiness;', {
    window, document, location, fetch, Map, Promise, Date
  }, {filename:'control/api/readiness.js'});
  const readiness = window._readinessTest;
  const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(setImmediate); };
  return {readiness, element, location, calls, settle, setRole(value) {currentRole = value;}};
}

(async () => {
  // Viewer never queries status and never reveals the setup view.
  const viewer = simulate({role:'viewers'});
  viewer.readiness.start({capabilities:{role:'viewers'}});
  await viewer.settle();
  assert.equal(viewer.element('setup-readiness').hidden, true);
  assert.equal(viewer.element('nav-readiness').hidden, true);
  assert.equal(viewer.calls.length, 0);

  const admin = simulate();
  admin.readiness.start({capabilities:{role:'admins'}});
  await admin.settle();
  assert.equal(admin.element('setup-readiness').hidden, false);
  assert.equal(admin.element('nav-readiness').hidden, false);
  assert.deepEqual(admin.calls.map(call=>call.url), [
    '/control/api/me','/api/status','/control/api/hosts'
  ]);
  assert.equal(admin.element('readiness-transport').textContent, 'HTTPS in use');
  assert.equal(admin.element('readiness-accounts').textContent, 'Available');
  assert.equal(admin.element('readiness-computers').textContent, 'Available');
  assert.equal(admin.element('readiness-dns').textContent, 'Responding');
  assert.equal(admin.element('readiness-wireguard').textContent, 'Responding');
  assert.equal(admin.element('readiness-monitor').textContent, 'Not detected');
  assert.equal(admin.element('readiness-monitor').dataset.state, 'optional');
  assert.equal(admin.element('readiness-hosts').textContent, '1 / 2');
  assert.ok(!admin.element('readiness-detail-hosts').textContent.includes('Do not disclose'));
  assert.match(admin.element('readiness-detail-wireguard').textContent, /not verified/);
  assert.equal(admin.element('readiness-refresh').disabled, false);
  admin.readiness.show();
  await admin.settle();
  assert.equal(admin.calls.filter(c=>c.url==='/api/status').length, 2);

  const partial = simulate({servicesFail:true});
  partial.readiness.start({capabilities:{role:'admins'}});
  await partial.settle();
  for(const id of ['dns','wireguard','monitor']) {
    assert.equal(partial.element('readiness-' + id).textContent, 'Unknown');
    assert.equal(partial.element('readiness-' + id).dataset.state, 'unknown');
  }
  assert.equal(partial.element('readiness-hosts').textContent, '1 / 2');

  const missingHosts = simulate({hostsFail:true});
  missingHosts.readiness.start({capabilities:{role:'admins'}});
  await missingHosts.settle();
  assert.equal(missingHosts.element('readiness-dns').textContent, 'Responding');
  assert.equal(missingHosts.element('readiness-hosts').textContent, 'Unknown');

  // Malformed entries are treated as invalid/missing evidence, never as
  // an invalid admin session. The other status source remains independent.
  const malformed = simulate({malformedServices:true, malformedHosts:true});
  malformed.readiness.start({capabilities:{role:'admins'}});
  await malformed.settle();
  assert.equal(malformed.element('setup-readiness').hidden, false);
  assert.equal(malformed.element('readiness-dns').textContent, 'Responding');
  assert.equal(malformed.element('readiness-hosts').textContent, 'Unknown');
  assert.equal(malformed.element('readiness-hosts').dataset.state, 'unknown');
  assert.equal(malformed.element('readiness-refresh').disabled, false);
  assert.ok(!/Authorization unavailable/.test(malformed.element('readiness-updated').textContent));

  const revoked = simulate();
  revoked.readiness.start({capabilities:{role:'admins'}});
  await revoked.settle();
  revoked.setRole(null);
  revoked.readiness.show();
  await revoked.settle();
  assert.equal(revoked.element('setup-readiness').hidden, true);
  assert.equal(revoked.element('nav-readiness').hidden, true);
  assert.equal(revoked.location.hash, '#hosts');
  assert.equal(revoked.element('readiness-hosts').textContent, 'Unknown');
  assert.match(revoked.element('readiness-updated').textContent, /Authorization unavailable/);

  const plain = simulate({protocol:'http:'});
  plain.readiness.start({capabilities:{role:'admins'}});
  await plain.settle();
  assert.equal(plain.element('readiness-transport').textContent, 'Not HTTPS');
  assert.equal(plain.element('readiness-transport').dataset.state, 'attention');
  console.log('Setup readiness: admin/viewer, scoped API responses, session loss and HTTPS labels verified');
})().catch(err=>{console.error(err);process.exitCode=1;});
