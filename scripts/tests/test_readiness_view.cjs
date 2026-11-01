'use strict';
// Execute the real Setup & Diagnostics browser module with fake read-only APIs.
// No Pi, Docker, accounts, secrets or installed browser dependencies required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../../control/api/readiness.js'), 'utf8');

function simulate({role = 'admins', servicesFail = false, hostsFail = false,
  malformedServices = false, malformedHosts = false,
  metricsFail = false, certificatesFail = false, protocol = 'https:'} = {}) {
  let currentRole = role;
  const calls = [];
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id, hidden: id === 'setup-readiness' || id === 'nav-readiness',
        disabled: false, textContent: '', dataset: {}, handlers: {}, children: [],
        replaceChildren(...items) {this.children=items;},
        append(...items) {this.children.push(...items);},
        addEventListener(name, callback) { this.handlers[name] = callback; },
        removeAttribute(name) { delete this[name]; }
      });
    }
    return elements.get(id);
  }
  const document = {getElementById: element,
    createElement(tag) {assert.equal(tag,'li');return {textContent:''};}};
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
    if (url === '/control/api/admin/diagnostics')
      return metricsFail ? {ok:false,status:503} :
        {ok:true,json:async()=>({
          schemaVersion:1,checkedAt:'2026-11-01T16:30:00Z',
          checks:[
            {id:'pi.scrape',state:'observed',value:1,unit:'boolean',detail:'Scrape endpoint responded'},
            {id:'pi.cpu',state:'observed',value:12.4,unit:'percent',detail:'Measured CPU usage'},
            {id:'pi.memory',state:'observed',value:47.2,unit:'percent',detail:'Measured memory usage'},
            {id:'pi.load',state:'observed',value:0.62,unit:'load',detail:'Measured load'},
            {id:'pi.thermal',state:'observed',value:44.1,unit:'celsius',detail:'Thermal zone'},
            {id:'vm.free',state:'observed',value:6442450944,unit:'bytes',detail:'VM storage free'}
          ],
          serviceChecks:['service.pihole','service.wireguard','service.victoria'].map(id=>({
            id,state:'observed',value:1,unit:'boolean',detail:'Synthetic scrape'
          })),
          telemetry:{state:'observed',windowHours:24,hosts:[
            {host:'main-desktop',ageSeconds:45,state:'observed'},
            {host:'sleeping-pc',ageSeconds:600,state:'stale'}
          ]}
        })};
    if (url === '/control/api/admin/certificates')
      return certificatesFail ? {ok:false,status:503} :
        {ok:true,json:async()=>({
          schemaVersion:1,checkedAt:'2026-11-01T16:30:00Z',
          certificates:['browser-home','browser-auth','browser-metrics','telemetry-receiver']
            .map(id=>({
              id,state:id==='browser-auth'?'attention':'observed',
              daysRemaining:id==='browser-auth'?7.5:60.3,
              notAfter:'2027-01-01T16:30:00Z',
              issuer:'CN=HomeAway Test CA',
              subjectAltNames:['homeaway.home.arpa'],
              hostnameMatch:id==='telemetry-receiver'?null:true,
              publicTrust:id==='telemetry-receiver'?'not_checked':'not_trusted',
              detail:'Synthetic certificate observed'
            }))
        })};
    if (url === '/control/api/admin/maintenance')
      return {ok:true,json:async()=>({
        schemaVersion:1,checkedAt:'2026-11-01T16:30:00Z',
        mode:'preflight-only',scheduleEnabled:false,
        checks:[{id:'backupPreflight',state:'ok'},
                {id:'retentionReview',state:'attention'}]
      })};
    if (url === '/control/api/admin/backup-status')
      return {ok:true,json:async()=>({
        schemaVersion:1,checkedAt:new Date().toISOString(),state:'observed',
        lastEncryptedBackupAt:new Date().toISOString(),checksumVerified:true,
        ageHours:1,detail:'Synthetic checksum match'
      })};
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
    window, document, location, fetch, Map, Promise, Date, setTimeout, clearTimeout, AbortController
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
    '/control/api/me','/api/status','/control/api/hosts',
    '/control/api/admin/diagnostics', '/control/api/admin/certificates',
    '/control/api/admin/maintenance',
    '/control/api/admin/backup-status'
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
  assert.match(admin.element('readiness-detail-wireguard').textContent, /Admin listener only/);
  assert.equal(admin.element('readiness-refresh').disabled, false);
  admin.readiness.show();
  // Refresh must preserve current values immediately, not clear cards.
  assert.equal(admin.element('readiness-pi-cpu').textContent, '12.4%');
  await admin.settle();
  assert.equal(admin.calls.filter(c=>c.url==='/api/status').length, 2);

  assert.equal(admin.element('readiness-pi-cpu').textContent, '12.4%');
  assert.equal(admin.element('readiness-pi-memory').textContent, '47.2%');
  assert.equal(admin.element('readiness-pi-thermal').textContent, '44.1 °C');
  assert.equal(admin.element('readiness-vm-free').textContent, '6.0 GiB free');
  assert.equal(admin.element('readiness-telemetry').dataset.state, 'stale');
  assert.equal(admin.element('readiness-telemetry-hosts').children.length, 2);
  assert.match(admin.element('readiness-telemetry-hosts').children[1].textContent, /sleeping-pc/);

  assert.equal(admin.element('readiness-cert-browser-home').textContent, '60.3 days remaining');
  assert.equal(admin.element('readiness-cert-browser-auth').dataset.state, 'attention');
  assert.match(admin.element('readiness-cert-detail-browser-metrics').textContent, /not trusted/);
  assert.match(admin.element('readiness-cert-detail-telemetry-receiver').textContent, /not checked/);
  assert.match(admin.element('readiness-cert-detail-browser-home').textContent, /HomeAway Test CA/);
  assert.equal(admin.element('readiness-service-pihole').textContent, 'Scraped');
  assert.equal(admin.element('readiness-backup').textContent, 'Checksum verified');
  assert.equal(admin.element('readiness-maintenance-backupPreflight').textContent, 'Passed');
  assert.equal(admin.element('readiness-maintenance-retentionReview').textContent, 'Attention');
  assert.equal(admin.element('readiness-maintenance-timer').textContent, 'Disabled');

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

  // A missing metrics adapter affects only its metrics, not existing
  // authenticated DNS, VPN manager, or computer status observations.
  const metricsMissing = simulate({metricsFail:true});
  metricsMissing.readiness.start({capabilities:{role:'admins'}});
  await metricsMissing.settle();
  assert.equal(metricsMissing.element('readiness-pi-cpu').textContent, 'Unknown');
  assert.equal(metricsMissing.element('readiness-telemetry').textContent, 'Unknown');
  assert.equal(metricsMissing.element('readiness-dns').textContent, 'Responding');
  assert.equal(metricsMissing.element('readiness-hosts').textContent, '1 / 2');

  const missingCerts = simulate({certificatesFail:true});
  missingCerts.readiness.start({capabilities:{role:'admins'}});
  await missingCerts.settle();
  assert.equal(missingCerts.element('readiness-cert-browser-home').textContent, 'Unknown');
  assert.equal(missingCerts.element('readiness-pi-cpu').textContent, '12.4%');
  assert.equal(missingCerts.element('readiness-dns').textContent, 'Responding');

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
  assert.equal(revoked.element('readiness-pi-thermal').textContent, 'Unknown');
  assert.equal(revoked.element('readiness-telemetry-hosts').children.length, 0);
  assert.equal(revoked.element('readiness-cert-browser-home').textContent, 'Unknown');
  assert.match(revoked.element('readiness-updated').textContent, /Authorization unavailable/);

  const plain = simulate({protocol:'http:'});
  plain.readiness.start({capabilities:{role:'admins'}});
  await plain.settle();
  assert.equal(plain.element('readiness-transport').textContent, 'Not HTTPS');
  assert.equal(plain.element('readiness-transport').dataset.state, 'attention');
  console.log('Setup readiness: admin/viewer, scoped API responses, session loss and HTTPS labels verified');
})().catch(err=>{console.error(err);process.exitCode=1;});
