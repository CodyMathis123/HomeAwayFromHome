'use strict';
// No external dependencies: execute the actual router in a tiny DOM harness.
// In particular, DOMParser's HTMLDocument nodes may be isConnected=true while
// attached to the WRONG document. That broke both navigation directions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const router = fs.readFileSync(path.join(__dirname, '../../portal/router.js'), 'utf8');

function browser(initialPath) {
  let currentURL = new URL('http://homeaway.test' + initialPath);
  const handlers = {};
  const calls = [];
  const createView = (name, parentElement) => ({
    name, parentElement, hidden: false, inert: false,
    isConnected: true, // Crucial: also true for a DOMParser document.
    querySelector(selector) {
      return selector === '.masthead' ? {ok: true} : null;
    },
    setAttribute(name, value) { this[name] = value; },
    focus() { calls.push('focus:' + name); }
  });
  const slot = {
    children: [],
    querySelector(selector) {
      if (selector === 'main.shell') return this.children[0] || null;
      if (selector === 'main.shell:not([hidden])')
        return this.children.find(view => !view.hidden) || null;
      throw Error('Unexpected slot selector: ' + selector);
    },
    append(view) {
      if (view.parentElement?.children)
        view.parentElement.children = view.parentElement.children.filter(item => item !== view);
      this.children.push(view);
      view.parentElement = this;
      calls.push('append:' + view.name);
    }
  };
  const initialName = initialPath.startsWith('/architecture') ? 'architecture' : 'overview';
  const first = createView(initialName, slot);
  slot.children.push(first);
  const location = {
    get href() { return currentURL.href; },
    get pathname() { return currentURL.pathname; },
    get origin() { return currentURL.origin; },
    get hash() { return currentURL.hash; },
    assign(value) { throw Error('Unexpected fallback navigation to ' + value); },
    reload() { throw Error('Unexpected document reload'); }
  };
  const document = {
    getElementById(id) { return id === 'page-view-slot' ? slot : null; },
    addEventListener(type, handler) { handlers[type] = handler; }
  };
  const window = {
    HomeAwayNavigation: {sync() { calls.push('nav:sync'); }},
    HomeAwayArchitecture: {show() { calls.push('show:architecture'); }},
    HomeAwayOverview: {show() { calls.push('show:overview'); }},
    addEventListener(type, handler) { handlers[type] = handler; },
    scrollTo() {}
  };
  class DOMParser {
    parseFromString(markup, mime) {
      assert.equal(mime, 'text/html');
      const parsedDoc = {children: []};
      const view = createView(markup === 'architecture' ? 'architecture' : 'overview', parsedDoc);
      parsedDoc.children.push(view);
      return {querySelector: selector => selector === 'main.shell' ? view : null};
    }
  }
  const history = {
    pushState(_state, _title, target) { currentURL = new URL(target, currentURL); }
  };
  async function fetch(route) {
    assert.ok(route === '/' || route === '/architecture/', 'No admin route fetched');
    calls.push('fetch:' + route);
    return {
      ok: true,
      headers: {get: key => key === 'content-type' ? 'text/html; charset=utf-8' : null},
      async text() { return route === '/' ? 'overview' : 'architecture'; }
    };
  }
  vm.runInNewContext(router, {
    URL, document, window, location, history, DOMParser, fetch, decodeURIComponent
  }, {filename: 'portal/router.js'});
  function click(href) {
    const event = {
      defaultPrevented: false, button: 0,
      target: {closest: () => ({href: new URL(href, currentURL).href, target: '',
                                hasAttribute: () => false})},
      preventDefault() { this.defaultPrevented = true; }
    };
    handlers.click(event);
    return event;
  }
  async function settle() {
    await new Promise(resolve => setImmediate(resolve));
  }
  return {slot, first, location, calls, click, settle, handlers};
}

async function testFrom(initialPath, otherPath) {
  const app = browser(initialPath);
  assert.equal(app.slot.children.length, 1);
  const event = app.click(otherPath);
  assert.equal(event.defaultPrevented, true);
  await app.settle();
  assert.equal(app.location.pathname, otherPath);
  assert.equal(app.slot.children.length, 2, 'Parsed view was not adopted into live slot');
  const next = app.slot.children.find(view => !view.hidden);
  assert.ok(next, 'Visible main element missing');
  assert.equal(next.parentElement, app.slot, 'Visible main is in another document');
  assert.equal(next.inert, false);
  assert.equal(app.first.hidden, true);
  assert.equal(app.first.inert, true);
  app.click(initialPath);
  await app.settle();
  assert.equal(app.location.pathname, initialPath);
  assert.equal(app.first.hidden, false, 'Returning view must become visible');
  assert.equal(app.first.inert, false);
  assert.equal(next.hidden, true);
  assert.equal(app.slot.children.length, 2, 'Cached views should not be recreated');

  const protectedLink = app.click('/control/');
  assert.equal(protectedLink.defaultPrevented, false, 'Do not intercept authenticated control');
  assert.equal(app.location.pathname, initialPath);
  assert.ok(!app.calls.some(value => value === 'fetch:/control/'));
}

(async () => {
  await testFrom('/', '/architecture/');
  await testFrom('/architecture/', '/');
  console.log('Portal router: both directions adopt parsed views; cache and auth boundary verified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
