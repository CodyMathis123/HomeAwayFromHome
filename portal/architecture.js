'use strict';
// Read-only architecture links and bounded service probes. Only /api/status is
// queried; there are no configuration, Docker, Windows, or control API requests.
window.HomeAwayArchitecture = (() => {
  const ids = ['dns', 'wireguard', 'monitor'];
  let initializedRoot = null;
  let pending = false;

  function activeRoot() {
    const root = document.querySelector('#page-view-slot > main.shell:not([hidden])');
    return root?.querySelector('.architecture-panel') ? root : null;
  }

  function setResult(root, id, state, label, detail) {
    const indicator = root.querySelector('#arch-check-' + id);
    const description = root.querySelector('#arch-detail-' + id);
    if (!indicator || !description) return;
    indicator.dataset.state = state;
    indicator.textContent = label;
    description.textContent = detail;
  }

  async function refresh() {
    const root = activeRoot();
    if (!root || pending) return;
    const button = root.querySelector('#arch-probes-refresh');
    const updated = root.querySelector('#arch-probes-updated');
    if (!button || !updated) return;
    pending = true;
    button.disabled = true;
    updated.textContent = 'Checking service endpoints…';
    try {
      const response = await fetch('/api/status', {
        credentials: 'same-origin', cache: 'no-store'
      });
      if (!response.ok) throw Error('Status endpoint unavailable');
      const data = await response.json();
      if (!Array.isArray(data.checks)) throw Error('Invalid status response');
      const checks = new Map(data.checks
        .filter(check => ids.includes(check.id))
        .map(check => [check.id, check]));
      for (const id of ids) {
        const check = checks.get(id);
        if (!check || typeof check.ok !== 'boolean' || typeof check.detail !== 'string') {
          setResult(root, id, 'unknown', 'Unknown', 'No valid result received for this check');
          continue;
        }
        const state = check.ok ? 'responding' : id === 'monitor' ? 'optional' : 'failed';
        const label = check.ok ? 'Responding' : id === 'monitor' ? 'Not detected' : 'No response';
        setResult(root, id, state, label, check.detail);
      }
      const timestamp = Date.parse(data.checkedAt);
      updated.textContent = Number.isFinite(timestamp)
        ? 'Checked ' + new Date(timestamp).toLocaleTimeString()
        : 'Checks completed (time unavailable)';
    } catch (_) {
      for (const id of ids) {
        setResult(root, id, 'unknown', 'Unknown',
          'HomeAway status check unavailable; service state not verified');
      }
      updated.textContent = 'Status unavailable — not evidence of an outage';
    } finally {
      button.disabled = false;
      pending = false;
    }
  }

  function init() {
    const root = activeRoot();
    if (!root || initializedRoot === root) return;
    initializedRoot = root;
    const showLink = (id, href) => {
      const link = root.querySelector('#' + id);
      if (!link) return;
      link.href = href;
      link.hidden = false;
    };
    showLink('architecture-dns', 'http://' + location.hostname + ':8080/admin/');
    showLink('architecture-grafana', 'http://' + location.hostname + ':3000/');
    root.querySelector('#arch-probes-refresh')?.addEventListener('click', () => void refresh());

    fetch('/api/control-config', {credentials: 'same-origin', cache: 'no-store'})
      .then(response => {
        if (!response.ok) throw Error('Control configuration unavailable');
        return response.json();
      })
      .then(data => {
        if (!data.enabled || !data.url) return;
        const url = new URL(data.url);
        if (url.protocol !== 'https:' || url.port !== '8443' ||
            url.pathname !== '/control/' || url.search || url.hash) return;
        showLink('architecture-control', url.href);
        showLink('architecture-windows', url.href);
      })
      .catch(() => { /* Optional control service may not be configured. */ });
    void refresh();
  }

  function show() {
    init();
    void refresh();
  }
  return Object.freeze({init, show, refresh});
})();
window.HomeAwayArchitecture.init();
