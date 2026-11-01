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
    badge.textContent = title;
    description.textContent = detail;
  }
  function unknown(message = 'Not checked in this session') {
    for (const id of ids) display(id, 'unknown', 'Unknown', message);
  }
  const metricIds = ['pi.scrape', 'pi.cpu', 'pi.memory', 'pi.load', 'pi.thermal', 'vm.free'];
  const metricSlug = id => id.replace('.', '-');
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
        'Last raw sensor exports; gaps can indicate sleep, offline Windows, or an exporter issue.');
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
      ? 'VictoriaMetrics observations checked ' + new Date(checked).toLocaleTimeString()
      : 'Metrics checked (timestamp unavailable)';
  }

  const certificateIds = ['browser-home', 'browser-auth', 'browser-metrics', 'telemetry-receiver'];
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
      ? 'Certificate leaves observed ' + new Date(checked).toLocaleTimeString()
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
    label.textContent = !fresh ? 'Evidence unavailable or stale' :
      data.ageHours <= 168 ? 'Checksum verified' : 'Backup older than 7 days';
    detail.textContent = fresh
      ? 'Encrypted backup created ' + new Date(backupStamp).toLocaleString() +
        '. SHA-256 matched at observation time. Decryption and clean-host restore are not established.'
      : 'Run the Pi backup status publisher to refresh local checksum evidence.';
    checked.textContent = Number.isFinite(stamp) ?
      'Last local checksum observation ' + new Date(stamp).toLocaleString() :
      'No local checksum observation available';
  }

  async function readJson(path) {
    const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin'});
    if (!response.ok) throw new Error('Read-only status unavailable');
    return response.json();
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
    unknown('Checking source…');
    metricsUnknown('Checking existing VictoriaMetrics series…');
    certificatesUnknown('Checking private TLS endpoints…');
    displayBackup(null);
    updated.textContent = 'Checking authorized status…';
    try {
      // /control/api/me must be freshly verified before querying status.
      const me = await readJson('/control/api/me');
      if (me?.capabilities?.role !== 'admins') throw new Error('Administrator access not verified');
      const caps = me.capabilities;
      display('transport', location.protocol === 'https:' ? 'observed' : 'attention',
        location.protocol === 'https:' ? 'HTTPS in use' : 'Not HTTPS',
        'Browser connection scheme only; certificate trust, issuer, expiry and renewal are not verified');
      display('accounts', caps.manageUsers ? 'observed' : 'optional',
        caps.manageUsers ? 'Available' : 'Not enabled',
        'Account administration capability; directory availability is not checked here');
      display('computers', caps.manageComputers ? 'observed' : 'optional',
        caps.manageComputers ? 'Available' : 'Not enabled',
        'Computer-management capability; Windows setup and SSH trust are not verified here');

      // Each existing API is independently optional. One failure must not
      // misclassify another component or erase independently observed results.
      const [services, computers, metrics, certificates, backup] = await Promise.allSettled([
        readJson('/api/status'),
        readJson('/control/api/hosts'),
        readJson('/control/api/admin/diagnostics'),
        readJson('/control/api/admin/certificates'),
        readJson('/control/api/admin/backup-status')
      ]);
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
            (id === 'wireguard' ? ' · Tunnel handshake and remote access not verified' :
             id === 'dns' ? ' · Client DNS configuration not verified' :
             ' · Dashboard content and telemetry freshness not verified'));
        }
      } else {
        for (const id of ['dns', 'wireguard', 'monitor'])
          display(id, 'unknown', 'Unknown', 'Portal status unavailable; service state not established');
      }

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
              ? 'Computers responding to the existing restricted SSH status check; this does not prove a power command completed'
              : 'No registered active computer targets; this is not a failed reachability test');
        }
      } else {
        display('hosts', 'unknown', 'Unknown', 'Computer inventory or reachability check unavailable');
      }
      if (metrics.status === 'fulfilled')
        displayMetrics(metrics.value);
      else
        metricsUnknown('Metrics observer unavailable; existing source state not established');
      if (certificates.status === 'fulfilled')
        displayCertificates(certificates.value);
      else
        certificatesUnknown('Certificate observer unavailable; issuer/expiry not verified');
      displayBackup(backup.status === 'fulfilled' ? backup.value : null);
      updated.textContent = 'Checked ' + new Date().toLocaleTimeString() +
        ' · Individual statuses have limited scope';
    } catch (_) {
      // Session loss or stale admin role. Clear any prior observations and
      // hide this view; never leave sensitive control details on screen.
      unknown('Administrator authorization could not be verified');
      metricsUnknown('Administrator authorization could not be verified');
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
    if (!allowed) {
      checked = false;
      unknown('Not checked in this session');
      metricsUnknown('Not checked in this session');
      certificatesUnknown('Not checked in this session');
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
