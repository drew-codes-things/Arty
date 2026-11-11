'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const proof = require('../service-proof');

const appFolder = path.join(__dirname, '..');
const read = (name) => fs.readFileSync(path.join(appFolder, name), 'utf8');

test('only a proof makes the service trusted, and silence with no service of ours is not trust', () => {
  assert.equal(proof.trustAfterCheck({ proven: true, answered: true, childAlive: false, trusted: false }), true);
  assert.equal(proof.trustAfterCheck({ proven: false, answered: true, childAlive: true, trusted: true }), false);
  assert.equal(proof.trustAfterCheck({ proven: false, answered: false, childAlive: false, trusted: true }), false);
  assert.equal(proof.trustAfterCheck({ proven: false, answered: false, childAlive: true, trusted: true }), true);
  assert.equal(proof.trustAfterCheck({ proven: false, answered: false, childAlive: true, trusted: false }), false);
});

test('the port warning is shown once until the real service is back', () => {
  const warning = proof.createPortWarning();
  assert.equal(warning.shouldShow(), true);
  assert.equal(warning.shouldShow(), false);
  assert.equal(warning.shouldShow(), false);
  warning.reset();
  assert.equal(warning.shouldShow(), true);
});

test('the service is not trusted just because Arty started it', () => {
  const main = read('main.js');
  assert.doesNotMatch(main, /bridgeTrusted = true/);
  assert.match(main, /trustAfterCheck\(/);
  assert.match(main, /portWarning\.shouldShow\(\)/);
});

function loadBridge(token) {
  const sent = [];
  const context = {
    window: { arty: { token: () => token } },
    fetch: async (address, options) => { sent.push({ address, options }); return { ok: true }; },
  };
  vm.createContext(context);
  vm.runInContext(read('renderer/bridge.js'), context);
  return { context, sent };
}

test('the window sends nothing while the service is unproven', async () => {
  const { context, sent } = loadBridge('');
  await assert.rejects(context.api('/connection', { method: 'POST', body: '{"api_key":"k"}' }), /proven/);
  assert.deepEqual(sent, []);
});

test('the window sends the token once the service is proven', async () => {
  const { context, sent } = loadBridge('the-token');
  await context.api('/health');
  assert.equal(sent[0].address, 'http://127.0.0.1:8765/health');
  assert.equal(sent[0].options.headers['X-Arty-Token'], 'the-token');
});

test('the page loads the bridge before the app', () => {
  const page = read('renderer/index.html');
  assert.ok(page.indexOf('src="bridge.js"') >= 0 && page.indexOf('src="bridge.js"') < page.indexOf('src="app.js"'));
  assert.doesNotMatch(read('renderer/app.js'), /function api\(/);
});

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.textContent = '';
    this.className = '';
  }

  append(...children) { this.children.push(...children); }

  replaceChildren(...children) { this.children = children; }

  addEventListener(kind, listener) { this.listeners[kind] = listener; }
}

function loadSites() {
  const context = { document: { createElement: (tag) => new FakeElement(tag) } };
  vm.createContext(context);
  vm.runInContext(read('renderer/sites.js'), context);
  return context;
}

test('the Chrome sites screen lists each allowed site with a Forget button', () => {
  const { fillSiteList } = loadSites();
  const list = new FakeElement('div');
  const forgotten = [];
  fillSiteList(list, ['shop.example', '<img src=x onerror=alert(1)>'], (site) => forgotten.push(site));
  assert.equal(list.children.length, 2);
  const [first, second] = list.children;
  assert.equal(first.children[0].textContent, 'shop.example');
  assert.equal(second.children[0].textContent, '<img src=x onerror=alert(1)>');
  assert.equal(first.children[1].textContent, 'Forget');
  first.children[1].listeners.click();
  assert.deepEqual(forgotten, ['shop.example']);
});

test('an empty list says so', () => {
  const { fillSiteList } = loadSites();
  const list = new FakeElement('div');
  fillSiteList(list, [], () => {});
  assert.equal(list.children.length, 1);
  assert.match(list.children[0].textContent, /No sites/);
});

test('the tray opens the Chrome sites screen, which uses the service routes', () => {
  assert.match(read('main.js'), /label: 'Chrome sites…'.*send\('chrome-sites'\)/);
  assert.match(read('preload.js'), /onChromeSites: \(callback\) => ipcRenderer\.on\('chrome-sites'/);
  const page = read('renderer/index.html');
  assert.match(page, /<section id="chrome-sites" class="chrome-sites" hidden>/);
  assert.ok(page.indexOf('src="sites.js"') >= 0 && page.indexOf('src="sites.js"') < page.indexOf('src="app.js"'));
  const renderer = read('renderer/app.js');
  for (const route of ['/browser/sites', '/browser/sites/forget', '/browser/pairing']) assert.ok(renderer.includes(`'${route}'`), route);
  assert.match(renderer, /window\.arty\?\.onChromeSites\(showChromeSites\)/);
});
