'use strict';
// Exercise the real shared navigation script without a browser or network.
// Control availability is a single-flight GET, retriable after an error,
// and the mobile active link remains visible in its horizontal rail.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../../portal/navigation.js'), 'utf8');

function simulate({failFirst = false} = {}) {
  const links = [];
  const handlers = {};
  const scroll = [];
  const location = {pathname:'/',hash:''};
  const mount = {replaceChildren(node) { this.child=node; }};
  const document = {
    getElementById(id) { return id === 'app-navigation' ? mount : null; },
    createElement(tag) {
      const e = {
        tag, dataset:{}, attrs:{}, hidden:false, textContent:'',
        setAttribute(name,value) { this.attrs[name]=value; },
        removeAttribute(name) { delete this.attrs[name]; },
        scrollIntoView(options) { scroll.push({key:this.dataset.navKey,options}); },
        append(child) { this.children=(this.children||[]).concat(child); },
        querySelectorAll(selector) {
          assert.equal(selector,'a[data-nav-key]');
          return links;
        }
      };
      if(tag === 'a') links.push(e);
      return e;
    }
  };
  let calls=0;
  async function fetch(url,opts) {
    assert.equal(url,'/api/control-config');
    assert.equal(opts.cache,'no-store');
    assert.equal(opts.credentials,'same-origin');
    calls++;
    if(failFirst && calls === 1) return {ok:false,status:503};
    return {ok:true,json:async()=>({enabled:true,url:'https://home.homeawayfromhome.house/control/'})};
  }
  const window = {
    matchMedia(query) {
      assert.equal(query,'(max-width: 900px)');
      return {matches:true};
    },
    addEventListener(name,listener) {handlers[name]=listener;}
  };
  vm.runInNewContext(script,{window,document,location,fetch,URL,Object},
    {filename:'portal/navigation.js'});
  const settle=async()=>{for(let i=0;i<4;i++)await new Promise(setImmediate);};
  return {window,location,links,handlers,scroll,settle,get calls(){return calls;}};
}

(async()=>{
  const healthy=simulate();
  await healthy.settle();
  assert.equal(healthy.calls,1,'Navigation should request config once');
  const get=healthy.window.HomeAwayControlConfig.get;
  const [a,b,c]=await Promise.all([get(),get(),get()]);
  assert.equal(a.enabled,true);
  assert.equal(b.enabled,true);
  assert.equal(c.enabled,true);
  assert.equal(healthy.calls,1,'Multiple consumers must share one request');
  const control=healthy.links.find(link=>link.dataset.navKey==='control');
  assert.equal(control.hidden,false);
  assert.equal(control.href,'https://home.homeawayfromhome.house/control/');
  assert.equal(healthy.scroll[0].key,'overview');
  healthy.location.pathname='/architecture/';
  healthy.handlers.popstate();
  assert.equal(healthy.scroll.at(-1).key,'architecture',
    'Mobile nav should reveal the selected item');
  const count=healthy.scroll.length;
  healthy.handlers.popstate();
  assert.equal(healthy.scroll.length,count,'No redundant nav scroll');

  const retry=simulate({failFirst:true});
  await retry.settle();
  assert.equal(retry.calls,1);
  const config=await retry.window.HomeAwayControlConfig.get();
  assert.equal(config.enabled,true);
  assert.equal(retry.calls,2,'Failed request must not remain cached');
  console.log('Shared navigation: single-flight, retry and mobile active-link tests passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
