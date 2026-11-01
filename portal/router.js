'use strict';
// Progressive same-document routing for the READ-ONLY portal pages.
// Never intercept /control/ or administrative APIs: the Caddy/Authelia
// boundary remains the only way to enter the privileged management UI.
(() => {
  const routeFor = pathname => pathname === '/' || pathname === '/index.html'
    ? '/' : pathname === '/architecture/' || pathname === '/architecture'
      ? '/architecture/' : null;
  const slot = document.getElementById('page-view-slot');
  const main = slot?.querySelector('main.shell');
  if (!main) return;
  const initialRoute = routeFor(location.pathname);
  if (!initialRoute) return;

  const views = new Map([[initialRoute, main]]);
  const titles = {
    '/': 'HomeAway · Home infrastructure',
    '/architecture/': 'HomeAway · Architecture'
  };
  let sequence = 0;

  function position(hash) {
    if (!hash) {
      window.scrollTo(0, 0);
      return;
    }
    try {
      const target = document.getElementById(decodeURIComponent(hash.slice(1)));
      if (target) target.scrollIntoView();
    } catch (_) { /* Invalid fragment: keep browser behavior benign. */ }
  }

  async function show(target, writeHistory) {
    const route = routeFor(target.pathname);
    if (!route) return false;
    const thisSequence = ++sequence;
    let next = views.get(route);
    if (!next) {
      // Static same-origin HTML only; no user-supplied URL or HTML is fetched.
      const response = await fetch(route, {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok || !response.headers.get('content-type')?.includes('text/html'))
        throw Error('Portal page unavailable');
      const documentFragment = new DOMParser().parseFromString(await response.text(), 'text/html');
      next = documentFragment.querySelector('main.shell');
      if (!next || !next.querySelector('.masthead')) throw Error('Unexpected portal page');
      views.set(route, next);
    }
    if (thisSequence !== sequence) return false;
    const active = slot.querySelector('main.shell:not([hidden])');
    if (active !== next) {
      if (active) {
        active.hidden = true;
        active.inert = true;
      }
      // DOMParser yields nodes connected to a *different* Document. A node
      // may report isConnected=true while still outside our visible slot.
      if (next.parentElement !== slot) slot.append(next);
      next.hidden = false;
      next.inert = false;
    }
    if (writeHistory) history.pushState({homeAwayPage: route}, '', target.pathname + target.search + target.hash);
    document.title = titles[route];
    window.HomeAwayNavigation?.sync();
    if (route === '/architecture/') window.HomeAwayArchitecture?.show();
    else window.HomeAwayOverview?.show();
    position(target.hash);
    next.setAttribute('tabindex', '-1');
    next.focus({preventScroll: true});
    return true;
  }

  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest?.('a[href]');
    if (!link || link.hasAttribute('download') ||
        (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, location.href);
    const route = routeFor(url.pathname);
    if (url.origin !== location.origin || !route) return;
    if (route === routeFor(location.pathname) && url.hash) {
      // Native same-document fragment navigation (including Back/Forward).
      ++sequence;
      return;
    }
    event.preventDefault();
    if (url.href === location.href) {
      position(url.hash);
      return;
    }
    show(url, true).catch(() => { location.assign(url.href); });
  });

  window.addEventListener('popstate', () => {
    show(new URL(location.href), false).catch(() => { location.reload(); });
  });
})();
