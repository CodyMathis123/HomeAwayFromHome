'use strict';
// Browser-only link wiring: these controls never perform mutations.
(() => {
  const show = (id, href) => {
    const link = document.getElementById(id);
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
})();
