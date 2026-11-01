'use strict';
// Shared read-only navigation for the Portal and Architecture pages.
// This script never authenticates a user or grants access to /control/.
(() => {
  const mount = document.getElementById('app-navigation');
  if (!mount) return;

  const nav = document.createElement('nav');
  nav.setAttribute('aria-label', 'HomeAway pages and services');
  const title = document.createElement('div');
  title.className = 'sidebar-title';
  title.textContent = 'HOMEAWAY';
  nav.append(title);

  const links = [
    ['Overview', '/', 'overview'],
    ['At a glance', '/#services', 'services'],
    ['Architecture', '/architecture/', 'architecture'],
    ['Live metrics', '/#metrics-heading', 'metrics']
  ];
  const current = location.pathname.startsWith('/architecture') ? 'architecture' : 'overview';
  function add(label, href, key) {
    const link = document.createElement('a');
    link.className = 'sidebar-link';
    link.textContent = label;
    link.href = href;
    if (key === current) link.setAttribute('aria-current', 'page');
    nav.append(link);
    return link;
  }
  links.forEach(([label, href, key]) => add(label, href, key));

  const section = document.createElement('div');
  section.className = 'sidebar-section';
  section.textContent = 'SERVICES';
  nav.append(section);
  add('Pi-hole DNS', 'http://' + location.hostname + ':8080/admin/', 'dns');
  add('Grafana', 'http://' + location.hostname + ':3000/', 'grafana');

  const control = add('Computers & users', '/control/', 'control');
  // Only the configured, validated private HTTPS origin should be linked.
  control.hidden = true;
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
      control.href = url.href;
      control.hidden = false;
    })
    .catch(() => { control.hidden = true; });
  mount.append(nav);
})();
