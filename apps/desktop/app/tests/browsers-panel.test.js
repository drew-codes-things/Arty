'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'renderer', 'sites.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'renderer', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'renderer', 'app.js'), 'utf8');

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.textContent = '';
    this.className = '';
    this.listeners = {};
  }

  append(...children) { this.children.push(...children); }

  replaceChildren(...children) { this.children = children; }

  addEventListener(kind, listener) { this.listeners[kind] = listener; }
}

function load() {
  const document = { createElement: (tag) => new FakeElement(tag) };
  const context = { document };
  vm.createContext(context);
  vm.runInContext(source, context);
  return context;
}

const textOf = (element) => [element.textContent, ...element.children.map(textOf)].join(' ').replace(/\s+/g, ' ').trim();

test('each browser with the extension gets one clear line saying whether it is connected', () => {
  const context = load();
  const list = new FakeElement('div');
  context.fillBrowserList(list, [
    { name: 'Chrome', packaging: 'native', profiles: 1, native: 'on', connected: true },
    { name: 'Firefox', packaging: 'native', profiles: 2, native: 'off', connected: false },
    { name: 'Chromium', packaging: 'snap', profiles: 1, native: 'unavailable', connected: false },
    { name: 'Edge', packaging: 'native', profiles: 1, native: 'on', connected: false },
  ], () => {});
  const rows = list.children.map(textOf);
  assert.equal(rows.length, 4);
  assert.match(rows[0], /^Chrome: connected\.$/);
  assert.match(rows[1], /^Firefox \(2 profiles\): not connected\. Press Connect\. Connect$/);
  assert.match(rows[2], /^Chromium \(snap\): can't connect by itself, so paste the pairing code/);
  assert.match(rows[3], /^Edge: connects by itself when it is open\. Connect$/);
});

test('Connect is offered only where Arty can fix it by itself, and says which browser', () => {
  const context = load();
  const list = new FakeElement('div');
  const asked = [];
  context.fillBrowserList(list, [
    { name: 'Chrome', packaging: 'native', profiles: 1, native: 'on', connected: true },
    { name: 'Firefox', packaging: 'native', profiles: 1, native: 'off', connected: false },
    { name: 'Chromium', packaging: 'snap', profiles: 1, native: 'unavailable', connected: false },
  ], (name) => asked.push(name));
  const buttons = list.children.flatMap((row) => row.children.filter((child) => child.tagName === 'button'));
  assert.equal(buttons.length, 1);
  buttons[0].listeners.click();
  assert.deepEqual(asked, ['Firefox']);
});

test('the link screen says plainly whether starfire Desktop is linked and how', () => {
  const context = load();
  assert.equal(context.linkStateLine({ linked: true, automatic: true, name: 'starfire Desktop' }),
    'Linked with starfire Desktop. It linked by itself (same user on this computer).');
  assert.equal(context.linkStateLine({ linked: true, automatic: false, name: 'starfire Desktop' }), 'Linked with starfire Desktop.');
  assert.match(context.linkStateLine({ linked: false, links_by_itself: true }), /links by itself, or make a code/);
  assert.equal(context.linkStateLine({ linked: false, links_by_itself: false }), 'Not linked yet.');
  assert.equal(context.linkAutomaticLabel(true), 'Stop linking by itself');
  assert.equal(context.linkAutomaticLabel(false), 'Link by itself');
  assert.match(html, /id="link-automatic"/);
  assert.match(app, /'\/link\/automatic'/);
  assert.match(app, /'\/browser\/found\/connect'/);
});

test('no browser with the extension says how to get it', () => {
  const context = load();
  const list = new FakeElement('div');
  context.fillBrowserList(list, []);
  assert.match(textOf(list), /No browser has the Arty extension yet/);
});

test('the switch label says what pressing it does', () => {
  const context = load();
  assert.equal(context.automaticButtonLabel(true), 'Stop connecting browsers by themselves');
  assert.equal(context.automaticButtonLabel(false), 'Connect browsers by themselves');
});

test('the Chrome sites panel has a Browsers section that the window fills from the local service', () => {
  assert.match(html, /<h2>Browsers<\/h2>/);
  assert.match(html, /id="browser-list"/);
  assert.match(html, /id="browsers-automatic"/);
  assert.match(app, /api\('\/browser\/found'\)/);
  assert.match(app, /'\/browser\/found\/automatic'/);
});
