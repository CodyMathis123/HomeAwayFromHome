'use strict';
// Read-only Phase 3 readiness view. All observations reuse existing APIs.
// This module performs no mutating requests, shell commands or configuration.
const HomeAwayReadiness = (() => {
  const $ = id => document.getElementById(id);
  const ids = ['dns', 'wireguard', 'monitor', 'hosts', 'transport', 'accounts', 'computers'];
  let allowed = false;
  let pending = false;
  let checked = false;

  function display(id, state, title, detail) {
    const badge = $('readiness-' + id);
    const description = $('readiness-detail-' + id);
    if (!badge || !description) return;
    badge.dataset.state = state;
    delete badge.dataset.refreshStale;
    badge.removeAttribute('title');
    badge.textContent = title;
    description.textContent = detail;
  }
  function markStale(ids) {
    // Never erase the last reading when a refresh fails. Its state remains
    // the last observation, visually marked as potentially outdated.
    for (const id of ids) {
      const badge = $('readiness-' + id);
      if (!badge) continue;
      badge.dataset.refreshStale = 'true';
      badge.title = 'Refresh failed — showing last observed result';
    }
  }
  function unknown(message = 'Not checked in this session') {
    for (const id of ids) display(id, 'unknown', 'Unknown', message);
  }
  const serviceIds = ['service.pihole', 'service.wireguard', 'service.victoria'];
  function servicesStale() {
    markStale(serviceIds.map(id => id.replace('.', '-')));
  }
  function servicesUnknown(message) {
    for (const id of serviceIds) display(id.replace('.', '-'), 'unknown', 'Unknown', message);
  }
  function displayServices(data) {
    if (data?.schemaVersion !== 1 || !Array.isArray(data.serviceChecks)) {
      servicesUnknown('No validated scrape-health response');
      return;
    }
    const checks = new Map(data.serviceChecks.filter(item =>
      item && serviceIds.includes(item.id)).map(item => [item.id, item]));
    for (const id of serviceIds) {
      const item = checks.get(id);
      if (!item || !['observed', 'attention', 'unknown'].includes(item.state) ||
          item.unit !== 'boolean' || (item.value !== null && item.value !== 0 && item.value !== 1)) {
        display(id.replace('.', '-'), 'unknown', 'Unknown', 'Invalid scrape status');
        continue;
      }
      const label = item.state === 'observed' ? 'Scraped' :
        item.state === 'attention' ? 'Scrape failed' : 'Unknown';
      display(id.replace('.', '-'), item.state, label,
        'Metrics endpoint response');
    }
  }
  const metricIds = ['pi.scrape', 'pi.cpu', 'pi.memory', 'pi.load', 'pi.thermal', 'vm.free'];
  const metricSlug = id => id.replace('.', '-');
  function metricsStale() {
    markStale([...metricIds.map(metricSlug), 'telemetry']);
  }
  function metricsUnknown(message) {
    for (const id of metricIds)
      display(metricSlug(id), 'unknown', 'Unknown', message);
    display('telemetry', 'unknown', 'Unknown', message);
    $('readiness-telemetry-hosts')?.replaceChildren();
    $('readiness-metrics-checked').textContent = message;
  }
  function metricText(item) {
    if (item.state === 'unknown' || typeof item.value !== 'number') return 'Unknown';
    if (item.id === 'pi.scrape')
      return item.state === 'attention' ? 'Scrape failed' : 'Responding';
    if (item.unit === 'percent') return item.value.toFixed(1) + '%';
    if (item.unit === 'celsius') return item.value.toFixed(1) + ' °C';
    if (item.unit === 'load') return item.value.toFixed(2);
    if (item.unit === 'bytes') return (item.value / 1073741824).toFixed(1) + ' GiB free';
    return 'Unknown';
  }
  function displayMetrics(data) {
    if (data?.schemaVersion !== 1 || !Array.isArray(data.checks) ||
        !data.telemetry || !Array.isArray(data.telemetry.hosts)) {
      metricsUnknown('Invalid or unavailable metrics evidence');
      return;
    }
    const items = new Map(data.checks
      .filter(item => item && metricIds.includes(item.id))
      .map(item => [item.id, item]));
    for (const id of metricIds) {
      const item = items.get(id);
      if (!item || !['observed', 'unknown', 'attention'].includes(item.state)) {
        display(metricSlug(id), 'unknown', 'Unknown', 'Metric result not available');
        continue;
      }
      display(metricSlug(id), item.state, metricText(item), item.detail ||
        'Observation from existing VictoriaMetrics series');
    }
    const telemetry = data.telemetry;
    const hosts = $('readiness-telemetry-hosts');
    hosts.replaceChildren();
    if (telemetry.state !== 'observed' || telemetry.hosts.length === 0) {
      display('telemetry', 'unknown', 'Unknown',
        'No SensorDash raw samples observed in the preceding 24 hours. Cause unknown.');
    } else {
      const stale = telemetry.hosts.filter(item => item.state === 'stale').length;
      display('telemetry', stale ? 'stale' : 'observed',
        stale ? stale + ' host(s) without recent exports' : 'Recent samples',
        'Last sensor export received');
      for (const item of telemetry.hosts.slice(0, 16)) {
        if (typeof item.host !== 'string' || !Number.isFinite(item.ageSeconds)) continue;
        const li = document.createElement('li');
        const minutes = item.ageSeconds < 60
          ? item.ageSeconds + ' seconds ago'
          : Math.round(item.ageSeconds / 60) + ' minutes ago';
        li.textContent = item.host + ' · last received ' + minutes;
        hosts.append(li);
      }
    }
    const checked = Date.parse(data.checkedAt);
    $('readiness-metrics-checked').textContent = Number.isFinite(checked)
      ? 'Updated ' + new Date(checked).toLocaleTimeString()
      : 'Metrics checked (timestamp unavailable)';
  }

  const certificateIds = ['browser-home', 'browser-auth', 'browser-metrics', 'telemetry-receiver'];
  function certificatesStale() {
    markStale(certificateIds.map(id => 'cert-' + id));
  }
  function certificatesUnknown(message) {
    for (const id of certificateIds) {
      const label = $('readiness-cert-' + id);
      const detail = $('readiness-cert-detail-' + id);
      if (!label || !detail) continue;
      label.dataset.state = 'unknown';
      label.textContent = 'Unknown';
      detail.textContent = message;
    }
    $('readiness-certificates-checked').textContent = message;
  }
  function displayCertificates(data) {
    if (data?.schemaVersion !== 1 || !Array.isArray(data.certificates)) {
      certificatesUnknown('Certificate evidence unavailable or invalid');
      return;
    }
    const entries = new Map(data.certificates.filter(item => item &&
      certificateIds.includes(item.id)).map(item => [item.id, item]));
    for (const id of certificateIds) {
      const label = $('readiness-cert-' + id);
      const detail = $('readiness-cert-detail-' + id);
      if (!label || !detail) continue;
      const item = entries.get(id);
      if (!item || !['observed', 'attention', 'unknown'].includes(item.state) ||
          (item.daysRemaining != null && !Number.isFinite(item.daysRemaining))) {
        label.dataset.state = 'unknown';
        label.textContent = 'Unknown';
        detail.textContent = 'No valid certificate observation';
        continue;
      }
      label.dataset.state = item.state;
      delete label.dataset.refreshStale;
      label.removeAttribute('title');
      label.textContent = item.state === 'unknown' ? 'Unknown'
        : item.daysRemaining + ' days remaining';
      const trust = item.publicTrust === 'verified' ? 'Public system roots: verified'
        : item.publicTrust === 'not_trusted' ? 'Public system roots: not trusted (internal CA expected)'
        : item.publicTrust === 'not_checked' ? 'Client/CA trust: not checked'
        : 'System trust check: unavailable';
      const hostMatch = item.hostnameMatch === true ? 'Hostname SAN: matches'
        : item.hostnameMatch === false ? 'Hostname SAN: mismatch'
        : 'Hostname SAN: not checked';
      const issuer = typeof item.issuer === 'string' ? item.issuer : 'Unknown issuer';
      detail.textContent = [item.detail || 'No details',
        'Issuer: ' + issuer, hostMatch, trust].join(' · ');
    }
    const checked = Date.parse(data.checkedAt);
    $('readiness-certificates-checked').textContent = Number.isFinite(checked)
      ? 'Updated ' + new Date(checked).toLocaleTimeString()
      : 'Certificate check time unavailable';
  }

  function displayBackup(data) {
    const label = $('readiness-backup');
    const detail = $('readiness-backup-detail');
    const checked = $('readiness-backup-checked');
    if (!label || !detail || !checked) return;
    const stamp = data?.checkedAt ? Date.parse(data.checkedAt) : NaN;
    const evidenceAgeHours = Number.isFinite(stamp) ? (Date.now() - stamp) / 3600000 : Infinity;
    const backupStamp = data?.lastEncryptedBackupAt ? Date.parse(data.lastEncryptedBackupAt) : NaN;
    const verified = data?.checksumVerified === true &&
      Number.isFinite(data?.ageHours) && Number.isFinite(backupStamp);
    const fresh = verified && evidenceAgeHours >= 0 && evidenceAgeHours <= 24;
    label.dataset.state = fresh ? (data.ageHours <= 168 ? 'observed' : 'stale') : 'unknown';
    delete label.dataset.refreshStale;
    label.removeAttribute('title');
    label.textContent = !fresh ? 'Evidence unavailable or stale' :
      data.ageHours <= 168 ? 'Checksum verified' : 'Backup older than 7 days';
    detail.textContent = fresh
      ? 'Encrypted backup: ' + new Date(backupStamp).toLocaleString() +
        ' · SHA-256 verified · Restore untested'
      : 'Backup verification unavailable or outdated';
    checked.textContent = Number.isFinite(stamp) ?
      'Verified ' + new Date(stamp).toLocaleString() :
      'Not verified';
  }

  async function readJson(path) {
    const controller = new AbortController();
    // A hung internal request should never hold the diagnostics page forever.
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(path, {cache: 'no-store',
        credentials: 'same-origin', signal: controller.signal});
      if (!response.ok) throw new Error('Read-only status unavailable');
      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }
  async function refresh() {
    const view = $('setup-readiness');
    if (!allowed || view?.hidden || pending) return;
    const button = $('readiness-refresh');
    const updated = $('readiness-updated');
    if (!button || !updated) return;
    pending = true;
    button.disabled = true;
    checked = true;
    // Keep the last observed values visible until each request returns.
    // This is a transient activity notice, not a replacement observation.
    updated.textContent = 'Refreshing…';
    try {
      // /control/api/me must be freshly verified before querying status.
      const me = await readJson('/control/api/me');
      if (me?.capabilities?.role !== 'admins') throw new Error('Administrator access not verified');
      const caps = me.capabilities;
      display('transport', location.protocol === 'https:' ? 'observed' : 'attention',
        location.protocol === 'https:' ? 'HTTPS in use' : 'Not HTTPS',
        'Secure connection');
      display('accounts', caps.manageUsers ? 'observed' : 'optional',
        caps.manageUsers ? 'Available' : 'Not enabled',
        'Account management enabled');
      display('computers', caps.manageComputers ? 'observed' : 'optional',
        caps.manageComputers ? 'Available' : 'Not enabled',
        'Computer management enabled');

      // Each existing API is independently optional. One failure must not
      // misclassify another component or erase independently observed results.
      // Render each independent observation as soon as its own request
      // completes. A slow SSH/computer check must not block certificates,
      // metrics or backup status; every request has a finite timeout.
      let failedSections = 0;
      const queries = [
        readJson('/api/status').then(
          value => ({status: 'fulfilled', value}),
          () => ({status: 'rejected'})
        ).then(services => {
      if (services.status === 'fulfilled' && Array.isArray(services.value.checks)) {
        const checks = new Map(services.value.checks
          .filter(item => item && typeof item === 'object' &&
          ['dns', 'wireguard', 'monitor'].includes(item.id))
          .map(item => [item.id, item]));
        for (const id of ['dns', 'wireguard', 'monitor']) {
          const check = checks.get(id);
          if (!check || typeof check.ok !== 'boolean' || typeof check.detail !== 'string') {
            display(id, 'unknown', 'Unknown', 'No valid result from the status endpoint');
            continue;
          }
          const isOptional = id === 'monitor';
          const label = check.ok ? 'Responding' : isOptional ? 'Not detected' : 'No response';
          display(id, check.ok ? 'observed' : isOptional ? 'optional' : 'attention',
            label, check.detail +
            (id === 'wireguard' ? ' · Admin listener only' :
             id === 'dns' ? ' · DNS response only' :
             ' · Listener only'));
        }
      } else {
        failedSections++;
        markStale(['dns', 'wireguard', 'monitor']);
      }

        }),
        readJson('/control/api/hosts').then(
          value => ({status: 'fulfilled', value}),
          () => ({status: 'rejected'})
        ).then(computers => {
      if (computers.status === 'fulfilled' && Array.isArray(computers.value.hosts)) {
        const entries = computers.value.hosts;
        if (!entries.every(item => item && typeof item === 'object' &&
          typeof item.state === 'string')) {
          display('hosts', 'unknown', 'Unknown',
            'Computer status response contained invalid records; reachability not verified');
        } else {
          const reachable = entries.filter(item => item.state === 'online').length;
          display('hosts', 'observed', entries.length ? reachable + ' / ' + entries.length : 'No entries',
            entries.length
              ? 'SSH reachability'
              : 'No registered computers');
        }
      } else {
        failedSections++;
        markStale(['hosts']);
      }
        }),
        readJson('/control/api/admin/diagnostics').then(
          value => ({status: 'fulfilled', value}),
          () => ({status: 'rejected'})
        ).then(metrics => {
      if (metrics.status === 'fulfilled')
        displayMetrics(metrics.value);
      else {
        failedSections++;
        metricsStale();
      }
      if (metrics.status === 'fulfilled') displayServices(metrics.value);
      else servicesStale();
        }),
        readJson('/control/api/admin/certificates').then(
          value => ({status: 'fulfilled', value}),
          () => ({status: 'rejected'})
        ).then(certificates => {
      if (certificates.status === 'fulfilled')
        displayCertificates(certificates.value);
      else {
        failedSections++;
        certificatesStale();
      }
        }),
        readJson('/control/api/admin/backup-status').then(
          value => ({status: 'fulfilled', value}),
          () => ({status: 'rejected'})
        ).then(backup => {
      if (backup.status === 'fulfilled') displayBackup(backup.value);
      else {
        failedSections++;
        markStale(['backup']);
      }
        }),
      ];
      await Promise.all(queries);
      updated.textContent = (failedSections
        ? failedSections + ' source(s) unavailable'
        : 'Updated') + ' · ' + new Date().toLocaleTimeString();
    } catch (_) {
      // Session loss or stale admin role. Clear any prior observations and
      // hide this view; never leave sensitive control details on screen.
      unknown('Administrator authorization could not be verified');
      metricsUnknown('Administrator authorization could not be verified');
      servicesUnknown('Administrator authorization could not be verified');
      certificatesUnknown('Administrator authorization could not be verified');
      displayBackup(null);
      updated.textContent = 'Authorization unavailable; reopen HomeAway Control and sign in';
      allowed = false;
      view.hidden = true;
      const link = $('nav-readiness');
      if (link) link.hidden = true;
      if (location.hash === '#setup-readiness') {
        location.hash = '#hosts';
      }
    } finally {
      button.disabled = false;
      pending = false;
    }
  }
  function start(identity) {
    allowed = identity?.capabilities?.role === 'admins';
    const view = $('setup-readiness');
    if (!view) return;
    view.hidden = !allowed;
    $('nav-readiness').hidden = !allowed;
    if (!allowed) checked = false;
    if (!checked) {
      // Initial state only. Refresh must never erase last known observations.
      unknown('Not checked in this session');
      metricsUnknown('Not checked in this session');
      servicesUnknown('Not checked in this session');
      certificatesUnknown('Not checked in this session');
      displayBackup(null);
    }
    if (allowed && location.hash === '#setup-readiness' && !checked)
      void refresh();
  }
  function show() {
    if (allowed && !pending) void refresh();
  }
  $('readiness-refresh')?.addEventListener('click', () => void refresh());
  return Object.freeze({start, show, refresh});
})();
