'use strict';
// Mount once when the Overview view becomes visible. A detached cached
// overview stops polling but keeps its DOM and Grafana disclosure state.
window.HomeAwayOverview = (() => {
  let initialized = false;
  let viewRefresh = null;
  function init() {
    const root = document.querySelector('#page-view-slot > main.shell:not([hidden])');
    if (initialized || !root?.querySelector('#refresh')) return;
    initialized = true;
    const byId = id => root.querySelector('#' + id);
    byId('link-dns').href = '/dns/admin/';
    byId('link-monitor').href = 'https://metrics.homeawayfromhome.house/';
    let pending = false;
    async function refresh() {
      // The overview may be cached but detached while Architecture is displayed.
      if (!root.isConnected || root.hidden || pending) return;
      pending = true;
      byId('refresh').disabled = true;
      try {
        const response = await fetch('/api/status', {cache: 'no-store'});
        if (!response.ok) throw new Error('Status API unavailable');
        const data = await response.json();
        for (const check of data.checks) {
          const status = byId('state-' + check.id);
          const detail = byId('detail-' + check.id);
          if (!status || !detail) continue;
          status.textContent = check.ok ? 'Online' : check.id === 'monitor' ? 'Not running' : 'Unavailable';
          status.dataset.status = check.ok ? 'up' : check.id === 'monitor' ? 'optional' : 'down';
          detail.textContent = check.detail;
        }
        const core = data.checks.filter(check => check.id !== 'monitor');
        byId('core-count').textContent = core.filter(check => check.ok).length;
        byId('dns-state').textContent = data.checks.find(check => check.id === 'dns')?.ok ? 'Responding' : 'Unavailable';
        byId('vpn-state').textContent = data.checks.find(check => check.id === 'wireguard')?.ok ? 'Reachable' : 'Unavailable';
        byId('updated').textContent = 'Last checked ' + new Date(data.checkedAt).toLocaleTimeString();
      } catch (error) {
        byId('updated').textContent = 'Status refresh failed · ' + error.message;
        for (const id of ['dns', 'wireguard', 'monitor']) {
          byId('state-' + id).textContent = 'Unknown';
          byId('state-' + id).dataset.status = 'unknown';
          byId('detail-' + id).textContent = 'Portal status check unavailable; service state not verified';
        }
        byId('core-count').textContent = '—';
        byId('dns-state').textContent = 'Unknown';
        byId('vpn-state').textContent = 'Unknown';
      } finally {
        byId('refresh').disabled = false;
        pending = false;
      }
    }
    byId('refresh').addEventListener('click', refresh);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
    setInterval(() => { if (!document.hidden) void refresh(); }, 30000);
    void refresh();
    
    async function setupEmbeddedMetrics() {
      const details = byId('metrics-section');
      const holder = byId('metrics-frame');
      const description = byId('metrics-description');
      const link = byId('metrics-open');
      try {
        const response = await fetch('/api/embed-config', {cache: 'no-store'});
        if (!response.ok) throw new Error('Embed configuration unavailable');
        const config = await response.json();
        if (!config.enabled || !config.url) {
          description.textContent = 'No embedded view configured yet. Share the HomeAway Portal Metrics dashboard from Grafana, then configure its link on the Pi.';
          return;
        }
        const url = new URL(config.url);
        // Never embed an arbitrary origin. Use only the private metrics gateway.
        if (url.protocol !== 'https:' ||
            url.hostname !== 'metrics.homeawayfromhome.house' ||
            url.port !== '' ||
            !url.pathname.startsWith('/public-dashboards/') ||
            !/^[a-fA-F0-9]{32,128}$/.test(url.pathname.slice('/public-dashboards/'.length))) {
          throw new Error('Embedded URL must use this Pi host and a shared-dashboard link');
        }
        link.href = url.href;
        link.hidden = false;
        description.textContent = 'Live, read-only metrics. The embedded view refreshes every 30 seconds while visible.';
        let nearViewport = false;
        function updateEmbed() {
          if (!details.open || document.hidden || !nearViewport) {
            holder.replaceChildren();
            return;
          }
          if (holder.firstChild) return;
          const frame = document.createElement('iframe');
          frame.title = 'HomeAway read-only Grafana metrics';
          frame.className = 'metrics-iframe';
          frame.loading = 'lazy';
          frame.referrerPolicy = 'no-referrer';
          frame.src = url.href;
          holder.appendChild(frame);
        }
        details.addEventListener('toggle', updateEmbed);
        document.addEventListener('visibilitychange', updateEmbed);
        if ('IntersectionObserver' in window) {
          const observer = new IntersectionObserver(entries => {
            nearViewport = entries.some(entry => entry.isIntersecting);
            updateEmbed();
          }, {rootMargin: '200px'});
          observer.observe(details);
        } else {
          nearViewport = true;
        }
        updateEmbed();
      } catch (error) {
        description.textContent = 'Embedded metrics unavailable: ' + error.message;
      }
    }
    void setupEmbeddedMetrics();
    
    async function setupPcControls() {
      try {
        const data = window.HomeAwayControlConfig?.get
          ? await window.HomeAwayControlConfig.get()
          : await fetch('/api/control-config', {
              credentials: 'same-origin', cache: 'no-store'
            }).then(response => response.ok ? response.json() : null);
        if (!data) return;
        if (!data.enabled || !data.url) return;
        const parsed = new URL(data.url);
        if (parsed.protocol !== 'https:' ||
            parsed.hostname !== 'home.homeawayfromhome.house' ||
            parsed.port !== '' ||
            parsed.pathname !== '/control/' || parsed.search || parsed.hash) return;
        const link = byId('link-pc-control');
        link.href = (location.protocol === 'https:' && location.host === parsed.host)
          ? '/control/' : parsed.href;
        byId('pc-access').hidden = false;
      } catch (_) { /* Optional control stack can be stopped. */ }
    }
    void setupPcControls();
    viewRefresh = refresh;
  }
  function show() {
    init();
    if (viewRefresh) void viewRefresh();
  }
  return Object.freeze({init, show});
})();
window.HomeAwayOverview.init();
