'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appFolder = path.join(__dirname, '..');
const read = (...parts) => fs.readFileSync(path.join(appFolder, ...parts), 'utf8');

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.textContent = '';
    this.className = '';
    this.listeners = {};
    this.disabled = false;
  }

  append(...children) { this.children.push(...children); }

  replaceChildren(...children) { this.children = children; }

  addEventListener(kind, listener) { this.listeners[kind] = listener; }
}

function load(now = { value: 1000 }) {
  const FakeDate = Object.assign(function FakeDate(...args) { return new Date(...args); }, { now: () => now.value });
  const context = { document: { createElement: (tag) => new FakeElement(tag) }, Date: FakeDate };
  vm.createContext(context);
  vm.runInContext(read('renderer', 'phones.js'), context);
  return context;
}

const textOf = (element) => [element.textContent, ...element.children.map(textOf)].join(' ').replace(/\s+/g, ' ').trim();

test('the window has a Link a phone panel with the code, the linked phones and a new-code button', () => {
  const page = read('renderer', 'index.html');
  assert.match(page, /<section id="phone-code"[^>]*\bhidden\b/);
  for (const id of ['phone-code-image', 'phone-list', 'phone-code-new', 'phone-code-close']) assert.match(page, new RegExp(`id="${id}"`));
  assert.match(page, /<script src="phones\.js"><\/script>\s*\n[\s\S]*<script src="app\.js">/);
  assert.doesNotMatch(page, /holds your API key/);
  assert.match(read('main.js'), /label: 'Link a phone…'[^\n]*send\('phone-code'\)/);
});

test('the panel follows the code and lists, links and removes phones only through the local service', () => {
  const script = read('renderer', 'app.js');
  for (const route of ["api('/phone-setup-code')", 'api(`/phone-link/${encodeURIComponent(offer)}`)', "api('/linked-phones')",
    "api('/linked-phones/remove'"]) {
    assert.ok(script.includes(route), route);
  }
});

test('each linked phone shows its name and a Remove that needs a second tap', () => {
  const now = { value: 1000 };
  const context = load(now);
  const list = new FakeElement('div');
  const removed = [];
  context.fillPhoneList(list, [{ id: 7, name: 'Pixel 8', created: 0, last_used: null }], (phone) => removed.push(phone.id));
  const [row] = list.children;
  assert.match(textOf(row), /^Pixel 8 \(not used yet\) Remove$/);
  const button = row.children[1];
  button.listeners.click();
  assert.deepEqual(removed, []);
  assert.equal(button.textContent, 'Tap again to remove');
  now.value += 1000;
  button.listeners.click();
  assert.deepEqual(removed, [7]);
  assert.equal(button.disabled, true);
});

test('a second tap long after the first only asks again', () => {
  const now = { value: 1000 };
  const context = load(now);
  const list = new FakeElement('div');
  const removed = [];
  context.fillPhoneList(list, [{ id: 7, name: 'Pixel 8', created: 0, last_used: 5 }], (phone) => removed.push(phone.id));
  const button = list.children[0].children[1];
  button.listeners.click();
  now.value += 60000;
  button.listeners.click();
  assert.deepEqual(removed, []);
});

test('with no phones the list says so, and the code states read plainly', () => {
  const context = load();
  const list = new FakeElement('div');
  context.fillPhoneList(list, [], () => {});
  assert.equal(textOf(list), 'No phones linked yet.');
  assert.match(context.phoneLinkLine({ state: 'linked', name: 'Pixel 8' }), /^Linked Pixel 8\. The code is used up/);
  assert.match(context.phoneLinkLine({ state: 'gone' }), /run out/);
  assert.equal(context.phoneLinkLine({ state: 'waiting' }), '');
});
