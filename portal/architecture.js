'use strict';
// Read-only architecture linking. Runs once for a direct visit or cached view.
window.HomeAwayArchitecture = (() => {
  let initializedView = null;
  function init() {
    const view = document.querySelector('main.shell .architecture-panel');
    if (!view || initializedView === view) return;
    initializedView = view;
      const show = (id, href) => {
        const link = view.closest('main.shell')?.querySelector('#' + id);
        if (!link) return;
        link.href = href;
        link.hidden = false;
      };
      show('architecture-dns', 'http://' + location.hostname + ':8080/admin/');
      show('architecture-grafana', 'http://' + location.hostname + ':3000/');
    
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
          show('architecture-control', url.href);
          show('architecture-windows', url.href);
        })
        .catch(() => { /* Optional control stack may be offline; no false link. */ });
    
  }
  return Object.freeze({init});
})();
window.HomeAwayArchitecture.init();
