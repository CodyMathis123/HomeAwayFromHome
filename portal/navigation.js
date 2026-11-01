'use strict';
// The same navigation implementation runs on public read-only pages and the
// separately authenticated /control/ document. It does not grant permissions.
(() => {
  const mount = document.getElementById('app-navigation');
  if (!mount) return;
  const isControl = location.pathname === '/control/' || location.pathname === '/control';
  const nav = document.createElement('nav');
  nav.className = 'section-nav';
  nav.setAttribute('aria-label',
    isControl ? 'Control page sections' : 'HomeAway pages and services');

  function title(text, name) {
    const span = document.createElement('span');
    span.className = name;
    span.textContent = text;
    nav.append(span);
  }
  function add(label, href, key, id, hidden = false) {
    const link = document.createElement('a');
    link.className = 'sidebar-link';
    link.textContent = label;
    link.href = href;
    link.dataset.navKey = key;
    if (id) link.id = id;
    link.hidden = hidden;
    nav.append(link);
    return link;
  }
  title('HOMEAWAY', 'sidebar-title');
  const links = [
    ['Overview', '/', 'overview'],
    ['At a glance', '/#services', 'services'],
    ['Architecture', '/architecture/', 'architecture'],
    ['Live metrics', '/#metrics-heading', 'metrics']
  ];
  links.forEach(([label, href, key]) => add(label, href, key));

  title('SERVICES', 'sidebar-section');
  add('Pi-hole DNS', 'http://' + location.hostname + ':8080/admin/', 'dns');
  add('Grafana', 'http://' + location.hostname + ':3000/', 'grafana');

  if (isControl) {
    title('MANAGE', 'sidebar-section');
    add('Power controls', '#hosts', 'hosts', 'nav-power');
    // Server capability checks (and server-side route enforcement) decide
    // whether privileged links can be revealed by control/app.js.
    add('Manage computers', '#computer-admin', 'computer-admin', 'nav-computers', true);
    add('Users and permissions', '#accounts', 'accounts', 'nav-accounts', true);
    add('Activity', '#recent-activity', 'recent-activity', 'nav-activity', true);
  } else {
    const control = add('Computers & users', '/control/', 'control', null, true);
    // Do not expose an unvalidated/unconfigured link on a public LAN page.
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
  }

  function sync() {
    let current = 'overview';
    if (isControl) {
      const key = location.hash.slice(1);
      current = ['hosts', 'computer-admin', 'accounts', 'recent-activity'].includes(key)
        ? key : 'hosts';
    } else if (location.pathname === '/architecture' ||
               location.pathname === '/architecture/') {
      current = 'architecture';
    } else if (location.hash === '#services') {
      current = 'services';
    } else if (location.hash === '#metrics-heading') {
      current = 'metrics';
    }
    for (const link of nav.querySelectorAll('a[data-nav-key]')) {
      if (!link.hidden && link.dataset.navKey === current)
        link.setAttribute('aria-current', isControl ? 'location' : 'page');
      else
        link.removeAttribute('aria-current');
    }
  }
  // Replaces only the navigation markup, never page content or capabilities.
  // /control/ ships a minimal static fallback so it can still function when
  // the portal (which serves this script) is unavailable.
  mount.replaceChildren(nav);
  window.HomeAwayNavigation = Object.freeze({sync});
  window.addEventListener('hashchange', sync);
  window.addEventListener('popstate', sync);
  sync();
})();
